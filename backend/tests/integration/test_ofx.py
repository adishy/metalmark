"""OFX/QFX import end to end (ADR-0030): preview, commit, dedupe, and the routes.

The tests that matter here are the ones that would catch the importer lying: a
re-import that duplicates the ledger, two overlapping exports that double because
the bank rewrote a description between them, a file that claims to be an
all-investment statement importing "successfully" with nothing in it, a balance
that lands on a date nothing asked about, or a file's own account number being
obeyed instead of reported.

The fixtures are hand-authored (``tests/fixtures/ofx/README.md``); the account
ids, merchant names and amounts in them are invented.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.db import scoped_session
from app.models import BalanceSnapshot, Transaction
from app.schemas.ledger import AccountCreate
from app.schemas.rules import RuleActions, RuleConditions, RuleCreate
from app.schemas.transactions import TransactionCreate
from app.services import imports as imp
from app.services import ledger, rules
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ofx"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _dt(y, m, d, hour=12):
    return datetime(y, m, d, hour, tzinfo=UTC)


async def _checking(session, household_id, name="Checking", **kw):
    kw.setdefault("type", "depository")
    return await ledger.create_account(
        session, household_id,
        AccountCreate(name=name, currency="USD", **kw),
    )


async def _all_txns(session) -> list[Transaction]:
    return list(
        (
            await session.execute(
                select(Transaction).order_by(Transaction.transacted_at, Transaction.description)
            )
        ).scalars().all()
    )


# ---- Preview ---------------------------------------------------------------


async def test_preview_reports_what_the_file_says_and_writes_nothing(household_factory):
    hh = await household_factory()
    preview = imp.preview_ofx(_fixture("statement.ofx"))

    assert preview.org == "Harborline Credit Union"
    assert (preview.acct_id, preview.acct_type, preview.currency) == (
        "000111222333", "CHECKING", "USD",
    )
    assert (preview.start, preview.end) == (_dt(2026, 1, 1, 17), _dt(2026, 1, 31, 17))
    assert (preview.transaction_count, preview.investment_count) == (4, 0)

    async with scoped_session(household_id=hh) as s:
        assert (await s.execute(select(func.count()).select_from(Transaction))).scalar_one() == 0


async def test_preview_of_a_1x_file_is_the_refusal_not_a_parse(household_factory):
    with pytest.raises(LedgerError) as exc:
        imp.preview_ofx(_fixture("legacy.ofx"))
    assert exc.value.status == 400
    assert "OFX 1.x" in exc.value.message and "SGML" in exc.value.message


async def test_preview_counts_the_investment_rows_it_will_skip(household_factory):
    preview = imp.preview_ofx(_fixture("investments.ofx"))
    assert (preview.transaction_count, preview.investment_count) == (0, 5)


# ---- Commit ----------------------------------------------------------------


async def test_commit_inserts_the_files_rows_with_the_fitid_as_the_external_id(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        result = await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped, result.suspects, result.investments_skipped) == (
        4, 0, 0, 0,
    )
    assert result.errors == []
    assert [r.external_id for r in rows] == [
        "2026010500001", "2026010700001", "2026011200001", "2026012100001",
    ]
    assert [r.amount for r in rows] == [D("-42.7500"), D("2400.0000"), D("-18.4000"), D("-96.1000")]
    assert {r.source for r in rows} == {"ofx"}
    # The digest is written alongside the FITID: it is the key the *CSV* path
    # knows, so writing it here is what makes "the same row by CSV and then by OFX
    # does not double" true rather than aspirational (PLAN-v0.9 decision J).
    assert all(r.import_hash and len(r.import_hash) == 64 for r in rows)


async def test_reimporting_the_same_file_inserts_nothing(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        first = await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        second = await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        rows = await _all_txns(s)

    assert first.inserted == 4
    assert (second.inserted, second.skipped, second.suspects) == (0, 4, 0)
    assert len(rows) == 4


async def test_two_overlapping_exports_dedupe_by_fitid(household_factory):
    """ADR-0030 §3's whole reason for preferring FITID over a content hash.

    ``january_february.ofx`` re-exports January's three rows with the same FITIDs
    **and a corrected memo on one of them**, plus two February rows. A content
    hash differs for the corrected row by construction, so a hash-first importer
    would insert a duplicate of it; FITID does not, so the row is skipped and the
    ledger keeps one row per movement.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        january = await imp.commit_ofx(s, hh, raw=_fixture("january.ofx"), account_id=acct.id)
        overlap = await imp.commit_ofx(
            s, hh, raw=_fixture("january_february.ofx"), account_id=acct.id,
        )
        rows = await _all_txns(s)

    assert (january.inserted, january.skipped) == (3, 0)
    # Three skipped, two inserted — one per movement, even though the bank
    # rewrote one description between the two files.
    assert (overlap.inserted, overlap.skipped) == (2, 3)
    assert len(rows) == 5
    jan = [r for r in rows if r.transacted_at.month == 1]
    assert len(jan) == 3
    # The original description stands: a re-import never rewrites a row it
    # already has, which is ADR-0019's boundary applied to the importer.
    assert [r.description for r in jan if r.external_id == "2026010500001"] == [
        "POS PURCHASE 0117"
    ]


async def test_a_file_without_fitids_falls_back_to_the_import_hash(household_factory):
    """The fallback, including its hard case: two genuinely identical rows in one
    file must both import, and a re-import of that file must not."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh, name="Savings")
        first = await imp.commit_ofx(s, hh, raw=_fixture("no_fitid.ofx"), account_id=acct.id)
        rows = await _all_txns(s)
        second = await imp.commit_ofx(s, hh, raw=_fixture("no_fitid.ofx"), account_id=acct.id)
        again = await _all_txns(s)

    assert (first.inserted, first.skipped) == (3, 0)
    assert {r.external_id for r in rows} == {None}
    # The two identical $5 coffees are separate rows, and their digests differ by
    # ordinal — which is what separates them from a re-import of the same file.
    assert len({r.import_hash for r in rows}) == 3
    assert (second.inserted, second.skipped) == (0, 3)
    assert len(again) == 3


async def test_investment_rows_are_counted_and_reported_not_imported(household_factory):
    """An all-investment file has to read as "0 imported, 5 skipped" — not as a
    success, and not as five cash movements (ADR-0030 §5)."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh, name="Brokerage", type="investment")
        result = await imp.commit_ofx(s, hh, raw=_fixture("investments.ofx"), account_id=acct.id)
        rows = await _all_txns(s)

    assert (result.inserted, result.skipped, result.investments_skipped) == (0, 0, 5)
    assert rows == []


async def test_the_ledger_balance_lands_as_a_snapshot_on_its_own_date(household_factory):
    """ADR-0030 §6: the same upsert sync uses. The date is the balance's own
    (``<DTASOF>``), which is what makes a re-import update one row in place rather
    than adding a point to the history every time."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        await s.refresh(acct)
        snapshots = {
            sn.balance_date: sn.balance
            for sn in (await s.execute(select(BalanceSnapshot))).scalars().all()
        }

    assert acct.current_balance == D("4210.5500")
    assert acct.balance_date == date(2026, 1, 31)
    # The statement's own date, not the day the file happened to be imported. The
    # account was opened with no balance, so that is the only point in its history.
    assert snapshots == {date(2026, 1, 31): D("4210.5500")}

    # Re-importing updates that snapshot rather than adding a second one — the
    # property that makes an accidental double-import a no-op in the history too.
    async with scoped_session(household_id=hh) as s:
        before = {sn.balance_date for sn in (await s.execute(select(BalanceSnapshot))).scalars()}
        await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        after = {sn.balance_date for sn in (await s.execute(select(BalanceSnapshot))).scalars()}
    assert before == after


async def test_a_derived_account_gets_the_balance_but_not_the_history(household_factory):
    """ADR-0021's guard, shared with sync: a derived account's balance series
    belongs to its holdings, so an imported point in it would be a second author
    for a number that has one."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await ledger.create_account(
            s, hh,
            AccountCreate(name="Brokerage", type="investment", currency="USD",
                          current_balance=D("100.00"), balance_date=date(2026, 1, 1)),
        )
        assert acct.balance_source == "derived"
        opened = {
            sn.balance_date: sn.balance
            for sn in (await s.execute(select(BalanceSnapshot))).scalars().all()
        }
        await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        await s.refresh(acct)
        after = {
            sn.balance_date: sn.balance
            for sn in (await s.execute(select(BalanceSnapshot))).scalars().all()
        }

    assert acct.current_balance == D("4210.5500")  # the file's number is still best
    assert after == opened  # and the history is untouched


async def test_the_files_account_number_is_reported_and_never_obeyed(household_factory):
    """ADR-0030 §4: the human picks the account. A file that names an account the
    household does not have imports into the one it was told to."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        chosen = await _checking(s, hh, name="Everyday")
        await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=chosen.id)
        rows = await _all_txns(s)

    assert {r.account_id for r in rows} == {chosen.id}


async def test_commit_refuses_an_account_this_household_cannot_see(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        foreign = await _checking(s, a, name="A's account")
    async with scoped_session(household_id=b) as s:
        with pytest.raises(LedgerError) as exc:
            await imp.commit_ofx(s, b, raw=_fixture("statement.ofx"), account_id=foreign.id)
        assert exc.value.status == 404
        # Refused before a single row landed.
        assert (await s.execute(select(func.count()).select_from(Transaction))).scalar_one() == 0


async def test_a_foreign_default_category_is_a_404_not_a_foreign_key_error(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        group = await ledger.create_category_group(s, a, "Expense", "expense", 0)
        foreign_cat = await ledger.create_category(s, a, group.id, "A's category", None, None, 0)
    async with scoped_session(household_id=b) as s:
        acct = await _checking(s, b, name="B's account")
        with pytest.raises(LedgerError) as exc:
            await imp.commit_ofx(
                s, b, raw=_fixture("statement.ofx"), account_id=acct.id,
                default_category_id=foreign_cat.id,
            )
        assert exc.value.status == 404


async def test_commit_applies_the_households_rules_to_what_it_imports(household_factory):
    """ADR-0030 §7: a downloaded statement is precisely the artefact a rule is
    written for, and the rule set is loaded once for the whole file."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        group = await ledger.create_category_group(s, hh, "Expense", "expense", 0)
        rent = await ledger.create_category(s, hh, group.id, "Rent", None, None, 0)
        await rules.create_rule(
            s, hh,
            RuleCreate(
                name="Rent",
                conditions=RuleConditions(description_regex="MONTHLY PASS"),
                actions=RuleActions(set_category_id=rent.id, mark_reviewed=True),
            ),
        )
        await imp.commit_ofx(s, hh, raw=_fixture("statement.ofx"), account_id=acct.id)
        rows = await _all_txns(s)

    rent_row = [r for r in rows if r.description == "MONTHLY PASS"][0]
    assert rent_row.category_id == rent.id
    assert rent_row.review_status == "reviewed"
    assert rent_row.field_sources["category"] == "rule"
    # A row no rule matched is left exactly as the file described it.
    other = [r for r in rows if r.description == "DIRECT DEPOSIT"][0]
    assert other.category_id is None


async def test_a_suspect_row_is_flagged_rather_than_merged_or_dropped(household_factory):
    """A near-miss — same account and amount, a day or two apart — imports and
    goes to review. Nothing is silently merged (ARCHITECTURE §2).

    Imported rows are filed under a category so that "needs_review" means one
    thing in this test: an uncategorized row is *also* needs_review, and the
    interesting row would otherwise be hidden among four of them.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _checking(s, hh)
        group = await ledger.create_category_group(s, hh, "Expense", "expense", 0)
        cat = await ledger.create_category(s, hh, group.id, "Unsorted", None, None, 0)
        await txns.create_transaction(
            s, hh,
            TransactionCreate(account_id=acct.id, amount=D("-42.75"),
                              transacted_at=_dt(2026, 1, 4), description="SOMETHING ELSE"),
        )
        result = await imp.commit_ofx(
            s, hh, raw=_fixture("statement.ofx"), account_id=acct.id,
            default_category_id=cat.id,
        )
        rows = await _all_txns(s)

    assert (result.inserted, result.suspects) == (4, 1)
    imported = {r.external_id: r for r in rows if r.external_id}
    assert imported["2026010500001"].review_status == "needs_review"
    # The other three landed reviewed — the flag is the near-miss, not the import.
    assert {
        r.review_status for fitid, r in imported.items() if fitid != "2026010500001"
    } == {"reviewed"}
    # And it is a real row, not a merge: the ledger has both of them.
    assert len(rows) == 5


# ---- The routes over HTTP --------------------------------------------------

CSRF = "X-CSRF-Token"
STATEMENT = _fixture("statement.ofx")


async def _signed_in_client() -> tuple[httpx.AsyncClient, dict]:
    """A client holding a session cookie and the CSRF header, like the frontend."""
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


async def test_preview_and_commit_over_multipart_http():
    http, _me = await _signed_in_client()
    try:
        acct = (
            await http.post("/accounts", json={
                "name": "Checking", "type": "depository", "currency": "USD",
            })
        ).json()
        files = {"file": ("statement.ofx", STATEMENT, "application/x-ofx")}

        preview = await http.post("/import/ofx/preview", files=files)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["org"] == "Harborline Credit Union"
        assert body["acct_id"] == "000111222333"
        assert body["transaction_count"] == 4 and body["investment_count"] == 0

        commit = await http.post(
            "/import/ofx/commit", files=files, data={"account_id": acct["id"]},
        )
        assert commit.status_code == 200, commit.text
        assert commit.json() == {
            "inserted": 4, "skipped": 0, "suspects": 0, "investments_skipped": 0, "errors": [],
        }

        # And the rows are what the ledger now reports. ``external_id`` is not on
        # the wire — the FITID is the importer's dedupe key, not a client field —
        # so the proof here is that the file's rows are visible in the ledger and
        # that a second commit of the same bytes adds none.
        # Filtered to the account this file went into: signup is open, so the
        # household is shared with every other HTTP test in the run and an
        # unfiltered page is whatever they left behind.
        page = (await http.get("/transactions", params={"account_id": acct["id"]})).json()
        assert {t["description"] for t in page["items"]} == {
            "POS PURCHASE 0117", "DIRECT DEPOSIT", "LANTERN AND LOOM BOOKS", "MONTHLY PASS",
        }
        assert {t["source"] for t in page["items"]} == {"ofx"}

        again = await http.post(
            "/import/ofx/commit", files=files, data={"account_id": acct["id"]},
        )
        assert again.json()["inserted"] == 0 and again.json()["skipped"] == 4
    finally:
        await http.aclose()


async def test_a_1x_file_comes_back_as_a_400_with_the_explanation():
    """The refusal has to survive the route: a 500 here would be the importer
    failing with nothing to tell the user what the file was."""
    http, _me = await _signed_in_client()
    try:
        resp = await http.post(
            "/import/ofx/preview", files={"file": ("legacy.ofx", _fixture("legacy.ofx"))},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "OFX 1.x" in detail and "CSV" in detail
    finally:
        await http.aclose()


async def test_an_entity_bomb_comes_back_as_a_400_and_echoes_nothing():
    http, _me = await _signed_in_client()
    try:
        resp = await http.post(
            "/import/ofx/preview", files={"file": ("entities.ofx", _fixture("entities.ofx"))},
        )
        assert resp.status_code == 400
        assert "unsafe" in resp.json()["detail"]
        assert "lol" not in resp.text
    finally:
        await http.aclose()


async def test_import_routes_require_a_session():
    from app.main import create_app

    files = {"file": ("statement.ofx", STATEMENT)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as anon:
        assert (await anon.post("/import/ofx/preview", files=files)).status_code == 401
        assert (await anon.post(
            "/import/ofx/commit", files=files, data={"account_id": str(uuid.uuid4())},
        )).status_code == 401


async def test_commit_over_http_requires_an_account_explicitly():
    """No account_id, no import: the file's own <ACCTID> is never a fallback."""
    http, _me = await _signed_in_client()
    try:
        resp = await http.post("/import/ofx/commit", files={"file": ("s.ofx", STATEMENT)})
        assert resp.status_code == 422
    finally:
        await http.aclose()
