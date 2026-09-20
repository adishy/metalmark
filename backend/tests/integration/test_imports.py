"""CSV import (WS-IMP, M1a): parsing, mapping, dedupe, and what a bad row does.

The tests that matter here are the ones that would catch the importer lying: a
re-import that duplicates the ledger, two genuine identical rows collapsed into
one, a possible duplicate silently merged, a guessed date or owner, or a
half-imported file. Also the tenant boundaries — an import is a write path like
any other and resolves its account, owners and categories under RLS, so
"someone else's row" has to be a 404 and never a silent attribution.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db import scoped_session
from app.models import CategoryGroup, Transaction
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import TransactionCreate
from app.services import imports as imp
from app.services import ledger, owners
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _csv_bom(*lines: str) -> bytes:
    return ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


def _dt(y, m, d):
    return datetime(y, m, d, 12, tzinfo=UTC)


def _mapping_for(raw: bytes) -> dict[str, str | None]:
    """The mapping the UI would open with for this file.

    Taken from the real suggester rather than written out by hand, so these tests
    also prove the suggestion is one the importer accepts — a preview that
    suggested an uncommittable mapping would be worse than no preview.
    """
    return imp.suggest_mapping(imp.parse_table(raw).headers)


async def _checking(session, household_id, name="Checking", owner_id=None):
    return await ledger.create_account(
        session, household_id,
        AccountCreate(name=name, type="depository", currency="USD", owner_id=owner_id),
    )


async def _make_category(session, household_id, name, group_type="expense"):
    g = CategoryGroup(household_id=household_id, name=group_type.title(), type=group_type)
    session.add(g)
    await session.flush()
    return await ledger.create_category(session, household_id, g.id, name, None, None, 0)


async def _all_txns(session) -> list[Transaction]:
    return list(
        (
            await session.execute(
                select(Transaction).order_by(Transaction.transacted_at, Transaction.description)
            )
        ).scalars().all()
    )


# ---- Preview ---------------------------------------------------------------


async def test_preview_reports_headers_sample_and_a_suggestion(household_factory):
    """Preview reads the file and writes nothing at all."""
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount,Memo,Owner",
        "01/05/2026,Coffee,-5.00,Card,Alex",
        "01/06/2026,Payroll,2500.00,,",
        "01/07/2026,Tea,-3.00,,",
    )
    preview = imp.preview_csv(raw)

    assert preview.headers == ["Date", "Description", "Amount", "Memo", "Owner"]
    assert preview.sample[0] == ["01/05/2026", "Coffee", "-5.00", "Card", "Alex"]
    assert len(preview.sample) == 3
    assert preview.suggested == {
        "Date": "date",
        "Description": "description",
        "Amount": "amount",
        # "Memo" is a description synonym, but "Description" already claimed it:
        # suggesting a field twice would make the mapping ambiguous.
        "Memo": None,
        "Owner": "owner",
    }
    async with scoped_session(household_id=hh) as s:
        assert (await s.execute(select(func.count()).select_from(Transaction))).scalar_one() == 0


async def test_preview_suggests_one_column_per_field():
    preview = imp.preview_csv(_csv(
        "Posted Date,Transaction Date,Amount,Debit,Credit",
        "01/05/2026,01/06/2026,5.00,,",
    ))
    assert preview.suggested["Posted Date"] == "date"
    assert preview.suggested["Transaction Date"] is None
    # A paired debit/credit file maps both: the pair is one amount source.
    assert preview.suggested["Debit"] == "debit"
    assert preview.suggested["Credit"] == "credit"


async def test_preview_handles_a_bom_and_a_semicolon_delimiter():
    """A BOM glued to "Date" would make the first column unmappable."""
    preview = imp.preview_csv(_csv_bom(
        "Date;Description;Amount",
        "01/05/2026;Coffee;-5.00",
    ))
    assert preview.headers == ["Date", "Description", "Amount"]
    assert preview.suggested["Date"] == "date"
    assert preview.sample == [["01/05/2026", "Coffee", "-5.00"]]


async def test_preview_rejects_a_file_with_no_date_column():
    with pytest.raises(LedgerError) as exc:
        imp.preview_csv(_csv("Description,Amount", "Coffee,-5.00"))
    assert exc.value.status == 400
    assert "date" in exc.value.message


async def test_preview_rejects_a_file_with_no_amount_column():
    with pytest.raises(LedgerError) as exc:
        imp.preview_csv(_csv("Date,Description", "01/05/2026,Coffee"))
    assert exc.value.status == 400
    assert "amount" in exc.value.message and "debit" in exc.value.message


async def test_preview_rejects_an_oversized_file():
    raw = b"Date,Amount\n" + b"01/05/2026,1.00\n" * (imp.MAX_FILE_BYTES // 16 + 1)
    assert len(raw) > imp.MAX_FILE_BYTES
    with pytest.raises(LedgerError) as exc:
        imp.preview_csv(raw)
    assert exc.value.status == 400
    assert "MB" in exc.value.message


async def test_preview_rejects_too_many_rows():
    raw = _csv("Date,Amount", *["01/05/2026,1.00"] * (imp.MAX_ROWS + 1))
    with pytest.raises(LedgerError) as exc:
        imp.preview_csv(raw)
    assert exc.value.status == 400
    assert str(imp.MAX_ROWS) in exc.value.message


async def test_preview_rejects_an_empty_file():
    for raw in (b"", b"\n\n\n", b"  \n"):
        with pytest.raises(LedgerError) as exc:
            imp.preview_csv(raw)
        assert exc.value.status == 400 and "empty" in exc.value.message


# ---- Commit: the rows that land --------------------------------------------


async def test_commit_inserts_rows_as_csv_sourced_manual_rows(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        "01/05/2026,Coffee,-5.00",
        "01/06/2026,Payroll,2500.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped, result.suspects, result.errors) == (2, 0, 0, [])
    assert [r.description for r in rows] == ["Coffee", "Payroll"]
    assert [r.amount for r in rows] == [D("-5.0000"), D("2500.0000")]
    assert [r.transacted_at for r in rows] == [_dt(2026, 1, 5), _dt(2026, 1, 6)]
    # source='csv' is the manual-origin boundary in practice: no external_id, so a
    # later sync — which merges by external_id — can never land on these rows
    # (ADR-0019).
    assert {r.source for r in rows} == {"csv"}
    assert {r.external_id for r in rows} == {None}
    assert all(r.import_hash and len(r.import_hash) == 64 for r in rows)
    assert {r.currency for r in rows} == {"USD"}
    assert all(r.base_amount is not None for r in rows)


async def test_reimporting_the_same_file_skips_every_row(household_factory):
    """The whole point of import_hash: the second pass adds nothing."""
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        "01/05/2026,Coffee,-5.00",
        "01/06/2026,Payroll,2500.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        mapping = _mapping_for(raw)
        first = await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=acct.id)
        second = await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=acct.id)
        rows = await _all_txns(s)

    assert first.inserted == 2
    assert (second.inserted, second.skipped) == (0, 2)
    assert len(rows) == 2


async def test_two_genuine_identical_rows_both_import(household_factory):
    """Two $5 coffees on one day are two rows, not one.

    The ordinal keeps their digests apart, so both land. The second is counted a
    suspect — the ledger cannot tell a genuine pair from a doubled row, and
    ARCHITECTURE §2 says a possible duplicate goes to review rather than being
    collapsed or dropped. The first is not a suspect: nothing preceded it.
    """
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        "01/05/2026,Coffee,-5.00",
        "01/05/2026,Coffee,-5.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped, result.suspects) == (2, 0, 1)
    assert len(rows) == 2
    assert len({r.import_hash for r in rows}) == 2  # ordinals 0 and 1


async def test_a_near_duplicate_is_imported_and_flagged_not_merged(household_factory):
    """Same account, same amount, a day apart, different wording: keep both.

    The default category is what makes the flag readable: a row that has a
    category is normally ``reviewed``, so ``needs_review`` here can only be the
    possible-duplicate flag.
    """
    hh = await household_factory()
    raw = _csv("Date,Description,Amount", "01/06/2026,Sushi Place,-12.34")
    async with scoped_session(household_id=hh) as s:
        dining = await _make_category(s, hh, "Dining")
        acct = await _checking(s, hh)
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-12.34"), transacted_at=_dt(2026, 1, 5),
            description="SUSHI PLACE 01/05",
        ))
        result = await imp.commit_csv(
            s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id,
            default_category_id=dining.id,
        )
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped, result.suspects) == (1, 0, 1)
    assert len(rows) == 2  # nothing merged, nothing dropped
    imported = next(r for r in rows if r.source == "csv")
    assert imported.review_status == "needs_review"
    assert imported.category_id == dining.id


async def test_a_row_outside_the_duplicate_window_is_not_a_suspect(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Description,Amount", "01/10/2026,Sushi,-12.34")
    async with scoped_session(household_id=hh) as s:
        dining = await _make_category(s, hh, "Dining")
        acct = await _checking(s, hh)
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-12.34"), transacted_at=_dt(2026, 1, 1),
        ))
        result = await imp.commit_csv(
            s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id,
            default_category_id=dining.id,
        )
        rows = await _all_txns(s)

    assert (result.inserted, result.suspects) == (1, 0)
    assert next(r for r in rows if r.source == "csv").review_status == "reviewed"


async def test_a_suspect_on_another_account_is_not_a_suspect(household_factory):
    """Near-duplicates are scoped to the account: the same charge on a different
    card is not a duplicate of anything."""
    hh = await household_factory()
    raw = _csv("Date,Description,Amount", "01/05/2026,Sushi,-12.34")
    async with scoped_session(household_id=hh) as s:
        one = await _checking(s, hh, name="One")
        two = await _checking(s, hh, name="Two")
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=one.id, amount=D("-12.34"), transacted_at=_dt(2026, 1, 5),
        ))
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=two.id)
    assert (result.inserted, result.suspects) == (1, 0)


# ---- Amounts ---------------------------------------------------------------


async def test_amount_cell_formats_real_exports_contain(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        '01/01/2026,Currency symbol,"$1,234.56"',
        '01/11/2026,Parentheses,"(123.45)"',
        "01/21/2026,Trailing minus,123.45-",
        '01/31/2026,European decimal,"1.234,56"',
        '02/10/2026,Thousands,"1,234"',
        "02/20/2026,Plain,12.5",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        by_description = {r.description: r.amount for r in await _all_txns(s)}

    assert result.errors == []
    assert by_description == {
        "Currency symbol": D("1234.5600"),
        "Parentheses": D("-123.4500"),
        "Trailing minus": D("-123.4500"),
        "European decimal": D("1234.5600"),
        "Thousands": D("1234.0000"),
        "Plain": D("12.5000"),
    }


async def test_debit_is_negative_and_credit_is_positive(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Debit,Credit",
        "01/05/2026,Coffee,5.00,",
        "01/06/2026,Payroll,,2500.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        by_description = {r.description: r.amount for r in await _all_txns(s)}

    assert result.inserted == 2
    assert by_description == {"Coffee": D("-5.0000"), "Payroll": D("2500.0000")}


async def test_an_amount_cell_beats_the_debit_and_credit_columns(household_factory):
    """A file with a signed amount column *and* leftover debit/credit columns."""
    hh = await household_factory()
    raw = _csv("Date,Description,Amount,Debit,Credit", "01/05/2026,Coffee,-5.00,5.00,")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id)
        assert [r.amount for r in await _all_txns(s)] == [D("-5.0000")]


async def test_debit_wins_when_a_row_fills_both_columns(household_factory):
    """The documented rule for a file that describes one movement twice."""
    hh = await household_factory()
    raw = _csv("Date,Description,Debit,Credit", "01/05/2026,Both,5.00,5.00")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id)
        assert [r.amount for r in await _all_txns(s)] == [D("-5.0000")]


async def test_an_unparseable_amount_is_a_row_error_not_a_garbage_row(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        "01/05/2026,Coffee,-5.00",
        "01/06/2026,Broken,n/a",
        "01/07/2026,Tea,-3.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped) == (2, 0)
    assert [e.line for e in result.errors] == [3]  # the header is line 1
    assert "n/a" in result.errors[0].message
    assert "Broken" not in [r.description for r in rows]
    # An exponent-shaped cell must not be quietly mangled into a number: a
    # fabricated amount is worse than a reported error.
    with pytest.raises(imp.RowError):
        imp.parse_amount("1e5")
    assert imp.parse_amount("1.000,5") == D("1000.5")  # EU grouping with a decimal comma


# ---- Dates -----------------------------------------------------------------


async def test_date_formats_iso_and_us(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount",
        "2026-01-05,ISO,-1.00",
        "01/06/2026,US,-2.00",
        "1/7/2026,Unpadded,-3.00",
        "2026/01/08,ISO with slashes,-4.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        by_description = {r.description: r.transacted_at for r in await _all_txns(s)}

    assert result.errors == []
    assert by_description == {
        "ISO": _dt(2026, 1, 5),
        "US": _dt(2026, 1, 6),
        "Unpadded": _dt(2026, 1, 7),
        "ISO with slashes": _dt(2026, 1, 8),
    }


async def test_dayfirst_decides_an_ambiguous_slash_date(household_factory):
    """05/03/2026 is 3 May or 5 March, and only the explicit option may decide."""
    hh = await household_factory()
    raw = _csv("Date,Amount", "05/03/2026,-1.00")
    async with scoped_session(household_id=hh) as s:
        us = await _checking(s, hh, name="US")
        eu = await _checking(s, hh, name="EU")
        mapping = _mapping_for(raw)
        await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=us.id)
        await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=eu.id, dayfirst=True)
        rows = await _all_txns(s)
        us_row = next(r for r in rows if r.account_id == us.id)
        eu_row = next(r for r in rows if r.account_id == eu.id)

    assert us_row.transacted_at == _dt(2026, 5, 3)  # US-first by default
    assert eu_row.transacted_at == _dt(2026, 3, 5)


async def test_a_date_that_only_parses_one_way_is_read_that_way(household_factory):
    """13/05/2026 is not a guess: there is no 13th month."""
    hh = await household_factory()
    raw = _csv("Date,Amount", "13/05/2026,-1.00")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert result.errors == []
    assert rows[0].transacted_at == _dt(2026, 5, 13)


async def test_an_unparseable_date_is_a_row_error(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Amount", "13/13/2026,-1.00", "01/05/2026,-2.00")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert result.inserted == 1
    assert [e.line for e in result.errors] == [2]
    assert len(rows) == 1


# ---- Owner by name ---------------------------------------------------------


async def test_owner_name_matches_ignoring_case_and_padding(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Amount,Owner", "01/05/2026,-5.00,  alex ")
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        acct = await _checking(s, hh)
        await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id)
        rows = await _all_txns(s)
    assert [r.owner_id for r in rows] == [alex.id]


async def test_blank_owner_cell_inherits_the_account(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Amount,Owner", "01/05/2026,-5.00,")
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        acct = await _checking(s, hh, owner_id=alex.id)
        await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id)
        rows = await _all_txns(s)
    # Null means inherit, not "unattributed": the chain terminates at the
    # account's owner (ADR-0026).
    assert [r.owner_id for r in rows] == [None]


async def test_an_unknown_owner_name_is_a_row_error_not_shared(household_factory):
    """Attribution drives reports, so it is refused rather than guessed at."""
    hh = await household_factory()
    raw = _csv("Date,Amount,Owner", "01/05/2026,-5.00,Nobody", "01/06/2026,-6.00,")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)

    assert result.inserted == 1
    assert [e.line for e in result.errors] == [2]
    assert "Nobody" in result.errors[0].message
    assert [r.owner_id for r in rows] == [None]


async def test_another_households_owner_name_does_not_resolve(household_factory):
    """The lookup runs in the household-scoped session, so a foreign owner is
    simply not in the map — it cannot be attributed by accident."""
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=b) as s:
        await owners.create_owner(s, b, name="Alex")
    raw = _csv("Date,Amount,Owner", "01/05/2026,-5.00,Alex")
    async with scoped_session(household_id=a) as s:
        acct = await _checking(s, a)
        result = await imp.commit_csv(s, a, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)
    assert result.inserted == 0
    assert [e.line for e in result.errors] == [2]
    assert rows == []


# ---- Category by name ------------------------------------------------------


async def test_category_resolves_case_insensitively(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Amount,Category", "01/05/2026,-5.00,dining")
    async with scoped_session(household_id=hh) as s:
        dining = await _make_category(s, hh, "Dining")
        acct = await _checking(s, hh)
        await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id)
        rows = await _all_txns(s)
    assert [r.category_id for r in rows] == [dining.id]


async def test_an_unknown_category_falls_back_to_the_commit_default(household_factory):
    """A bank's categories are not the household's, so an unknown name must not
    fail the row: the money is right either way, and the row stays reviewable."""
    hh = await household_factory()
    raw = _csv(
        "Date,Description,Amount,Category",
        "01/05/2026,Bank says groceries,-5.00,Groceries",
        "01/06/2026,No category cell,-6.00,",
    )
    async with scoped_session(household_id=hh) as s:
        dining = await _make_category(s, hh, "Dining")
        acct = await _checking(s, hh)
        result = await imp.commit_csv(
            s, hh, raw=raw, mapping=_mapping_for(raw), account_id=acct.id,
            default_category_id=dining.id,
        )
        rows = await _all_txns(s)

    assert result.errors == []
    # A named-but-unknown category and a blank cell both land on the default.
    assert [r.category_id for r in rows] == [dining.id, dining.id]


async def test_an_unknown_category_without_a_default_is_uncategorized(household_factory):
    hh = await household_factory()
    raw = _csv("Date,Amount,Category", "01/05/2026,-5.00,Groceries")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
        rows = await _all_txns(s)
    assert result.inserted == 1
    assert [r.category_id for r in rows] == [None]
    assert rows[0].review_status == "needs_review"


# ---- Contract and boundaries ----------------------------------------------


async def test_the_counts_account_for_every_non_blank_row(household_factory):
    hh = await household_factory()
    raw = _csv(
        "Date,Amount",
        "01/05/2026,-5.00",
        "01/06/2026,n/a",
        "",
        "01/07/2026,-6.00",
    )
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        mapping = _mapping_for(raw)
        first = await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=acct.id)
        second = await imp.commit_csv(s, hh, raw=raw, mapping=mapping, account_id=acct.id)
        rows = await _all_txns(s)

    # A blank line is not a row; every other row is inserted, skipped or reported.
    assert first.inserted + first.skipped + len(first.errors) == 3
    assert second.inserted + second.skipped + len(second.errors) == 3
    assert (first.inserted, first.skipped, len(first.errors)) == (2, 0, 1)
    assert (second.inserted, second.skipped, len(second.errors)) == (0, 2, 1)
    assert {r.amount for r in rows} == {D("-5.0000"), D("-6.0000")}


async def test_a_row_error_names_its_file_line_after_a_blank_line(household_factory):
    """Line numbers come from the file, not from a count of parsed rows."""
    hh = await household_factory()
    raw = b"Date,Amount\n\n01/05/2026,-5.00\ngarbage,-6.00\n"
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                      account_id=acct.id)
    assert [e.line for e in result.errors] == [4]


async def test_commit_is_atomic_per_file(household_factory):
    """One transaction: a failure after the parse leaves nothing behind."""
    hh = await household_factory()
    with pytest.raises(RuntimeError):
        async with scoped_session(household_id=hh) as s:
            acct = await _checking(s, hh)
            raw = _csv("Date,Amount", "01/05/2026,-5.00")
            await imp.commit_csv(s, hh, raw=raw, mapping=_mapping_for(raw),
                                 account_id=acct.id)
            raise RuntimeError("something blew up mid-file")

    async with scoped_session(household_id=hh) as s:
        count = (await s.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert count == 0


async def test_commit_cannot_import_into_another_households_account(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=b) as s:
        theirs = await _checking(s, b, name="Theirs")
    raw = _csv("Date,Amount", "01/05/2026,-5.00")
    async with scoped_session(household_id=a) as s:
        with pytest.raises(LedgerError) as exc:
            await imp.commit_csv(s, a, raw=raw, mapping=_mapping_for(raw),
                                 account_id=theirs.id)
    assert exc.value.status == 404


async def test_commit_rejects_a_foreign_default_category(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=b) as s:
        theirs = await _make_category(s, b, "Theirs")
    raw = _csv("Date,Amount", "01/05/2026,-5.00")
    async with scoped_session(household_id=a) as s:
        acct = await _checking(s, a)
        with pytest.raises(LedgerError) as exc:
            await imp.commit_csv(
                s, a, raw=raw, mapping=_mapping_for(raw), account_id=acct.id,
                default_category_id=theirs.id,
            )
    assert exc.value.status == 404


async def test_commit_rejects_a_mapping_the_file_cannot_satisfy(household_factory):
    hh = await household_factory()
    with pytest.raises(LedgerError) as exc:
        imp.validate_mapping(["Date", "Amount"], {"Nope": "date", "Amount": "amount"})
    assert exc.value.status == 400 and "does not have" in exc.value.message

    with pytest.raises(LedgerError) as exc:
        imp.validate_mapping(["Date", "Amount"], {"Date": "date", "Amount": "whenever"})
    assert exc.value.status == 400 and "whenever" in exc.value.message

    with pytest.raises(LedgerError) as exc:
        imp.validate_mapping(["Date", "Amount"], {"Date": "date", "Amount": "date"})
    assert exc.value.status == 400 and "same field" in exc.value.message

    # And commit refuses the same way rather than importing a partial reading.
    raw = _csv("Date,Amount", "01/05/2026,-5.00")
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        with pytest.raises(LedgerError):
            await imp.commit_csv(s, hh, raw=raw, mapping={"Date": None}, account_id=acct.id)


# ---- The multipart contract, over HTTP -------------------------------------

CSRF = "X-CSRF-Token"
STATEMENT = _csv(
    "Date,Description,Amount",
    "01/05/2026,Coffee,-5.00",
    "01/06/2026,Payroll,2500.00",
)
MAPPED = {"Date": "date", "Description": "description", "Amount": "amount"}


async def _signed_in_client() -> tuple[httpx.AsyncClient, dict]:
    """A client holding a session cookie and the CSRF header, like the frontend.

    Signup is open (ADR-0027), so this joins the household if one exists and
    creates it otherwise — either way it returns a household this test can write
    to, which is why it does not matter which file ran first.

    The base URL is https for one reason: the session cookie is Secure outside
    the dev environment, and a cookie jar will not keep a Secure cookie from an
    http origin. Nothing here leaves the process — ASGITransport only reads the
    scheme.
    """
    from app.main import create_app

    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
    )
    resp = await http.post(
        "/auth/signup",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "display_name": "Alex",
            "password": "password123",
            "household_name": "Home",
        },
    )
    assert resp.status_code == 201, resp.text
    me = resp.json()
    http.headers[CSRF] = me["csrf_token"]
    return http, me


async def _an_account(http: httpx.AsyncClient) -> dict:
    resp = await http.post("/accounts", json={
        "name": "Checking", "type": "depository", "currency": "USD", "current_balance": "0",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_preview_and_commit_over_multipart_http():
    http, _me = await _signed_in_client()
    try:
        acct = await _an_account(http)
        files = {"file": ("statement.csv", STATEMENT, "text/csv")}

        preview = await http.post("/import/csv/preview", files=files)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["headers"] == ["Date", "Description", "Amount"]
        assert body["sample"][0] == ["01/05/2026", "Coffee", "-5.00"]
        assert body["suggested"] == MAPPED

        commit = await http.post("/import/csv/commit", files=files, data={
            "account_id": acct["id"], "mapping": json.dumps(body["suggested"]),
        })
        assert commit.status_code == 200, commit.text
        assert commit.json() == {"inserted": 2, "skipped": 0, "suspects": 0, "errors": []}

        page = (await http.get("/transactions")).json()
        assert {t["description"] for t in page["items"]} == {"Coffee", "Payroll"}
        assert {t["source"] for t in page["items"]} == {"csv"}

        # The same file again is a clean no-op, not a second copy.
        again = await http.post("/import/csv/commit", files=files, data={
            "account_id": acct["id"], "mapping": json.dumps(MAPPED),
        })
        assert again.json()["skipped"] == 2

        # A row-level failure is reported with its line, and still a 200: part of
        # the file is worthless, the rest of it is not.
        bad = await http.post("/import/csv/commit", files={
            "file": ("bad.csv", _csv("Date,Amount", "01/09/2026,n/a"), "text/csv"),
        }, data={"account_id": acct["id"],
                 "mapping": json.dumps({"Date": "date", "Amount": "amount"})})
        assert bad.status_code == 200, bad.text
        assert bad.json()["errors"][0]["line"] == 2

        # A file that cannot be imported is a clean 400 naming what is missing.
        missing = await http.post("/import/csv/preview", files={
            "file": ("no-date.csv", _csv("Description,Amount", "Coffee,-5.00"), "text/csv"),
        })
        assert missing.status_code == 400
        assert "date" in missing.json()["detail"]

        # ...and so is a malformed mapping, rather than a 422 or a 500.
        broken = await http.post("/import/csv/commit", files=files, data={
            "account_id": acct["id"], "mapping": "{not json",
        })
        assert broken.status_code == 400
        assert "JSON" in broken.json()["detail"]
    finally:
        await http.aclose()


async def test_import_routes_are_closed_to_the_unauthenticated():
    from app.main import create_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as anon:
        files = {"file": ("statement.csv", STATEMENT, "text/csv")}
        assert (await anon.post("/import/csv/preview", files=files)).status_code == 401
        assert (await anon.post("/import/csv/commit", files=files)).status_code == 401


async def test_commit_requires_the_csrf_token():
    """It is a POST that writes money into the ledger, so it goes through the
    same CSRF gate as every other mutation."""
    http, _me = await _signed_in_client()
    try:
        acct = await _an_account(http)
        http.headers[CSRF] = "not-the-token"
        resp = await http.post("/import/csv/commit", files={
            "file": ("statement.csv", STATEMENT, "text/csv"),
        }, data={"account_id": acct["id"], "mapping": json.dumps(MAPPED)})
        assert resp.status_code == 403
    finally:
        await http.aclose()
