"""Ownership model (ADR-0026): the attribution chain, the filters, and the
report-attribution asymmetry that makes the same `owner_id` mean two things.

These are the tests that would catch the model quietly lying — a split charge
counted for the wrong person, an account filter that returns rows, a net-worth
decomposition that stops reconciling once a filter is applied.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.db import scoped_session
from app.models import CategoryGroup
from app.schemas.ledger import AccountCreate, AccountUpdate
from app.schemas.owners import OwnerUpdate
from app.schemas.transactions import SplitIn, TransactionCreate, TransactionUpdate
from app.services import ledger, owners, reports
from app.services import transactions as txns
from app.services.errors import LedgerError
from app.services.ownership import effective_owner_id

pytestmark = pytest.mark.integration

D = Decimal


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def _make_category(session, household_id, group_type: str, name: str):
    g = CategoryGroup(household_id=household_id, name=group_type.title(), type=group_type)
    session.add(g)
    await session.flush()
    return await ledger.create_category(session, household_id, g.id, name, None, None, 0)


# ---- The Shared owner and CRUD --------------------------------------------


async def test_household_gets_exactly_one_shared_owner(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        found = await owners.list_owners(s)
    assert [o.kind for o in found] == ["shared"]
    assert found[0].name == "Shared"


async def test_ensure_shared_owner_is_idempotent(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        first = await owners.ensure_shared_owner(s, hh)
        second = await owners.ensure_shared_owner(s, hh)
    assert first.id == second.id


async def test_shared_owner_sorts_first(household_factory):
    """The picker's default ("Shared") has to be the first thing in the list."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await owners.create_owner(s, hh, name="Alex", sort=-5)
        await owners.create_owner(s, hh, name="Beth")
        names = [o.name for o in await owners.list_owners(s)]
    assert names == ["Shared", "Alex", "Beth"]


async def test_create_owner_rejects_duplicate_name_case_insensitively(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await owners.create_owner(s, hh, name="Alex")
        with pytest.raises(LedgerError) as exc:
            await owners.create_owner(s, hh, name="  alex  ")
    assert exc.value.status == 409


async def test_rename_onto_an_existing_name_is_rejected(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await owners.create_owner(s, hh, name="Alex")
        beth = await owners.create_owner(s, hh, name="Beth")
        with pytest.raises(LedgerError) as exc:
            await owners.update_owner(s, beth.id, OwnerUpdate(name="Alex"))
    assert exc.value.status == 409


async def test_renaming_an_owner_to_its_own_name_is_fine(household_factory):
    """The uniqueness check must exclude the row being edited."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        beth = await owners.create_owner(s, hh, name="Beth")
        await owners.update_owner(s, beth.id, OwnerUpdate(name="Beth"))
        assert beth.name == "Beth"


async def test_shared_owner_may_be_renamed_but_not_deleted(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        shared = await owners.ensure_shared_owner(s, hh)
        await owners.update_owner(s, shared.id, OwnerUpdate(name="Household"))
        assert shared.name == "Household"
        with pytest.raises(LedgerError) as exc:
            await owners.delete_owner(s, shared)
    assert exc.value.status == 409


async def test_create_owner_always_makes_a_person(household_factory):
    """`kind` is not client input — Shared is created with the household."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        o = await owners.create_owner(s, hh, name="Alex")
    assert o.kind == "person"


async def test_delete_owner_reassigns_to_shared_by_default(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        shared = await owners.ensure_shared_owner(s, hh)
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Alex Card", type="credit", currency="USD",
                                 owner_id=alex.id)
        )
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5),
            owner_id=alex.id))
        await txns.replace_splits(s, txn.id, [SplitIn(amount=D("-10"), owner_id=alex.id)])

        counts = await owners.delete_owner(s, alex)
        assert counts == {
            "reassigned_accounts": 1,
            "reassigned_transactions": 1,
            "reassigned_splits": 1,
        }
        # The reassignment is Core SQL, so the loaded row must be re-read.
        await s.refresh(acct)
        assert acct.owner_id == shared.id
        names = [o.name for o in await owners.list_owners(s)]
    assert "Alex" not in names


async def test_delete_owner_reassigns_to_a_named_target(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        beth = await owners.create_owner(s, hh, name="Beth")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Alex Card", type="credit", currency="USD",
                                 owner_id=alex.id)
        )
        await owners.delete_owner(s, alex, reassign_to=beth.id)
        await s.refresh(acct)
    assert acct.owner_id == beth.id


async def test_delete_owner_refuses_its_own_reassign_target(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        with pytest.raises(LedgerError) as exc:
            await owners.delete_owner(s, alex, reassign_to=alex.id)
    assert exc.value.status == 400


async def test_get_owner_hides_other_households(household_factory):
    """Resolution goes through RLS, so a foreign id is a 404, not a leak."""
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        alex = await owners.create_owner(s, a, name="Alex")
    async with scoped_session(household_id=b) as s:
        with pytest.raises(LedgerError) as exc:
            await owners.get_owner(s, alex.id)
    assert exc.value.status == 404


# ---- Accounts --------------------------------------------------------------


async def test_create_account_defaults_to_shared_owner(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))
        shared = await owners.ensure_shared_owner(s, hh)
    assert acct.owner_id == shared.id


async def test_create_account_rejects_an_unknown_owner(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(LedgerError) as exc:
            await ledger.create_account(
                s, hh, AccountCreate(name="Checking", type="depository", currency="USD",
                                     owner_id=uuid.uuid4()))
    assert exc.value.status == 404


async def test_account_owner_cannot_be_cleared():
    """The column is NOT NULL and there is no unowned state — 422 at the schema."""
    with pytest.raises(ValidationError):
        AccountUpdate(owner_id=None)
    # Absent is still fine: it means "no change".
    assert AccountUpdate(name="x").owner_id is None


async def test_update_account_resolves_a_foreign_owner_as_404(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        foreign = await owners.create_owner(s, a, name="Alex")
    async with scoped_session(household_id=b) as s:
        acct = await ledger.create_account(
            s, b, AccountCreate(name="Checking", type="depository", currency="USD"))
        with pytest.raises(LedgerError) as exc:
            await ledger.update_account(s, acct.id, AccountUpdate(owner_id=foreign.id))
    assert exc.value.status == 404


async def test_account_owner_filter_is_plain_equality(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        await ledger.create_account(
            s, hh, AccountCreate(name="Joint Card", type="credit", currency="USD"))
        await ledger.create_account(
            s, hh, AccountCreate(name="Alex Checking", type="depository", currency="USD",
                                 owner_id=alex.id))
        alex_accts = await ledger.list_accounts(s, alex.id)
        shared_accts = await ledger.list_accounts(s, (await owners.ensure_shared_owner(s, hh)).id)
    assert [a.name for a in alex_accts] == ["Alex Checking"]
    assert [a.name for a in shared_accts] == ["Joint Card"]


# ---- The attribution chain -------------------------------------------------


def test_effective_owner_precedence():
    """The chain itself, with no database in the way."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert effective_owner_id(split_owner_id=a, transaction_owner_id=b,
                              account_owner_id=c) == a
    assert effective_owner_id(split_owner_id=None, transaction_owner_id=b,
                              account_owner_id=c) == b
    assert effective_owner_id(split_owner_id=None, transaction_owner_id=None,
                              account_owner_id=c) == c


async def test_owner_columns_store_what_was_asked_for(household_factory):
    """NULL means inherit, and stays NULL — it is not eagerly materialized."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        beth = await owners.create_owner(s, hh, name="Beth")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Chk", type="depository", currency="USD",
                                 owner_id=alex.id))
        inheriting = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5)))
        owned = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-20"), transacted_at=_dt(2026, 1, 6),
            owner_id=beth.id))
        split_txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-30"), transacted_at=_dt(2026, 1, 7),
            owner_id=beth.id))
        result = await txns.replace_splits(s, split_txn.id, [
            SplitIn(amount=D("-10"), owner_id=alex.id),
            SplitIn(amount=D("-20")),  # no owner of its own
        ])
    assert inheriting.owner_id is None
    assert owned.owner_id == beth.id
    by_amount = {sp.amount: sp.owner_id for sp in result.splits}
    assert by_amount[D("-10.0000")] == alex.id
    assert by_amount[D("-20.0000")] is None


async def test_clearing_a_transaction_owner_makes_it_inherit(household_factory):
    """Explicit null is a clear, not an ignored no-op (the is_set fix)."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        beth = await owners.create_owner(s, hh, name="Beth")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Chk", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5),
            owner_id=beth.id))
        assert txn.owner_id == beth.id

        cleared = await txns.update_transaction(s, hh, txn.id, TransactionUpdate(owner_id=None))
        assert cleared.owner_id is None
        # Still user-sourced: clearing is a decision a rule must not undo.
        assert cleared.field_sources.get("owner") == "user"
        # And it now resolves to the account's owner, not to nobody.
        assert effective_owner_id(split_owner_id=None, transaction_owner_id=None,
                                  account_owner_id=acct.owner_id) == acct.owner_id


async def test_absent_owner_in_patch_leaves_it_alone(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        beth = await owners.create_owner(s, hh, name="Beth")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Chk", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5),
            owner_id=beth.id))
        updated = await txns.update_transaction(s, hh, txn.id, TransactionUpdate(notes="hi"))
    assert updated.owner_id == beth.id


def test_required_transaction_fields_reject_null():
    with pytest.raises(ValidationError):
        TransactionUpdate(amount=None)
    with pytest.raises(ValidationError):
        TransactionUpdate(is_hidden=None)


# ---- Filters ---------------------------------------------------------------


async def _owner_filter_fixture(s, hh):
    """Two people, a shared card, and a split charge that is mostly Beth's.

    * ``plain``      - Alex's own account, no owner on the row.
    * ``overridden`` - on the *shared* card, attributed to Beth.
    * ``split``      - on the shared card, 30 to Beth and 70 left to inherit.
    """
    alex = await owners.create_owner(s, hh, name="Alex")
    beth = await owners.create_owner(s, hh, name="Beth")
    card = await ledger.create_account(
        s, hh, AccountCreate(name="Joint Card", type="credit", currency="USD"))
    alex_acct = await ledger.create_account(
        s, hh, AccountCreate(name="Alex Checking", type="depository", currency="USD",
                             owner_id=alex.id))
    beth_acct = await ledger.create_account(
        s, hh, AccountCreate(name="Beth Checking", type="depository", currency="USD",
                             owner_id=beth.id))

    plain = await txns.create_transaction(s, hh, TransactionCreate(
        account_id=alex_acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5)))
    overridden = await txns.create_transaction(s, hh, TransactionCreate(
        account_id=card.id, amount=D("-20"), transacted_at=_dt(2026, 1, 6),
        owner_id=beth.id))
    split = await txns.create_transaction(s, hh, TransactionCreate(
        account_id=card.id, amount=D("-100"), transacted_at=_dt(2026, 1, 7)))
    await txns.replace_splits(s, split.id, [
        SplitIn(amount=D("-30"), owner_id=beth.id), SplitIn(amount=D("-70")),
    ])
    return {
        "shared": await owners.ensure_shared_owner(s, hh),
        "alex": alex, "beth": beth, "card": card,
        "alex_acct": alex_acct, "beth_acct": beth_acct,
        "plain": plain, "overridden": overridden, "split": split,
    }


async def test_transaction_owner_filter_follows_inheritance(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)

        async def ids_for(owner_id):
            rows, _ = await txns.list_transactions(s, owner_id=owner_id)
            return {r.id for r in rows}

        alex_ids = await ids_for(f["alex"].id)
        beth_ids = await ids_for(f["beth"].id)
        shared_ids = await ids_for(f["shared"].id)

    # Alex: only the plain charge on his own account.
    assert alex_ids == {f["plain"].id}
    # Beth: the overridden charge, plus the split parent she has a real share of.
    assert beth_ids == {f["overridden"].id, f["split"].id}
    # Shared: the split charge (its 70 inherits through the account) and nothing
    # else — crucially NOT the overridden charge, which sits on Shared's account
    # but belongs to Beth.
    assert shared_ids == {f["split"].id}


async def test_split_parent_is_not_matched_through_its_account(household_factory):
    """The regression the ``NOT has_children`` guard exists for.

    A shared-card charge split *entirely* to Beth must not surface for the card's
    own owner just because the account is theirs.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)
        all_beth = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=f["card"].id, amount=D("-40"), transacted_at=_dt(2026, 1, 8)))
        await txns.replace_splits(s, all_beth.id, [SplitIn(amount=D("-40"),
                                                           owner_id=f["beth"].id)])
        shared_rows, _ = await txns.list_transactions(s, owner_id=f["shared"].id)
        beth_rows, _ = await txns.list_transactions(s, owner_id=f["beth"].id)
    assert all_beth.id not in {r.id for r in shared_rows}
    assert all_beth.id in {r.id for r in beth_rows}


async def test_transaction_owner_filter_ignores_a_stale_split_flag(household_factory):
    """``is_split_parent`` is denormalized; the filter derives from children."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)
        stale = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=f["alex_acct"].id, amount=D("-5"), transacted_at=_dt(2026, 1, 9)))
        stale.is_split_parent = True  # lies: it has no children
        await s.flush()
        rows, _ = await txns.list_transactions(s, owner_id=f["alex"].id)
    assert stale.id in {r.id for r in rows}


async def test_owner_filter_combines_with_other_filters(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)
        rows, _ = await txns.list_transactions(
            s, owner_id=f["beth"].id, account_ids=[f["card"].id])
    assert {r.id for r in rows} == {f["overridden"].id, f["split"].id}


# ---- Report attribution ----------------------------------------------------


async def test_spending_report_is_row_scoped(household_factory):
    """Each split child is judged on its own owner, not the parent's account."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)
        dining = await _make_category(s, hh, "expense", "Dining")
        for txn_id in (f["split"].id, f["overridden"].id):
            await txns.update_transaction(s, hh, txn_id,
                                          TransactionUpdate(category_id=dining.id))

        _b, _rows, beth_total, _w = await reports.spending_by_category(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), f["beth"].id)
        _b, _rows_all, all_total, _w = await reports.spending_by_category(
            s, hh, date(2026, 1, 1), date(2026, 1, 31))

    # Beth: her 30 share of the split + the 20 she owns outright = 50 — not the
    # split parent's full 100.
    assert beth_total == D("50.0000")
    assert all_total == D("130.0000")  # 100 + 20 + 10


async def test_cash_flow_report_is_row_scoped(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        f = await _owner_filter_fixture(s, hh)
        _b, _g, all_months = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), granularity="month")
        _b, _g, beth_months = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), f["beth"].id,
            granularity="month")
    assert all_months[0]["expense"] == D("-130.0000")
    assert beth_months[0]["expense"] == D("-50.0000")


async def test_net_worth_report_is_account_scoped_and_still_reconciles(household_factory):
    """The asymmetry, and the invariant it must not break.

    Beth's own account falls 40; she also has a 20 charge on the shared card.
    Account-scoped, her net worth only knows about her account — the card is
    Shared's. Row-scoped cash flow *for the same filter* would report -60 and the
    identity would assert something false, which is the bug this pins shut.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        beth = await owners.create_owner(s, hh, name="Beth")
        card = await ledger.create_account(
            s, hh, AccountCreate(name="Joint Card", type="credit", currency="USD",
                                 current_balance=D("0"), balance_date=date(2026, 1, 1)))
        beth_acct = await ledger.create_account(
            s, hh, AccountCreate(name="Beth Checking", type="depository", currency="USD",
                                 owner_id=beth.id, current_balance=D("100"),
                                 balance_date=date(2026, 1, 1)))
        dining = await _make_category(s, hh, "expense", "Dining")
        hers = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=beth_acct.id, amount=D("-40"), transacted_at=_dt(2026, 1, 15),
            category_id=dining.id))
        on_card = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=card.id, amount=D("-20"), transacted_at=_dt(2026, 1, 16),
            owner_id=beth.id, category_id=dining.id))
        assert hers.owner_id is None  # inherits Beth's account
        assert on_card.owner_id == beth.id
        # End-of-month stated balances reflect the flows.
        await ledger.update_account(s, card.id, AccountUpdate(
            current_balance=D("20"), balance_date=date(2026, 1, 31)))
        await ledger.update_account(s, beth_acct.id, AccountUpdate(
            current_balance=D("60"), balance_date=date(2026, 1, 31)))

        whole = await reports.net_worth_series(s, hh, date(2026, 1, 1), date(2026, 1, 31))
        beth_series = await reports.net_worth_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), beth.id)
        _b, _rows, beth_spending, _w = await reports.spending_by_category(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), beth.id)

    assert beth_series["attribution"] == "account"
    # Her account moved 100 -> 60; the shared card is not hers to report.
    assert beth_series["delta_net_worth"] == D("-40.0000")
    assert beth_series["net_cash_flow"] == D("-40.0000")
    assert beth_series["delta_net_worth"] == (
        beth_series["net_cash_flow"] + beth_series["currency_revaluation"])
    # Whole household: both charges count, and it still reconciles.
    assert whole["delta_net_worth"] == D("-60.0000")
    assert whole["net_cash_flow"] == D("-60.0000")
    assert whole["delta_net_worth"] == (
        whole["net_cash_flow"] + whole["currency_revaluation"])
    # The row-scoped view sees all 60 of Beth's spending: same filter, other answer.
    assert beth_spending == D("60.0000")


async def test_filtered_net_worth_counts_transfers_crossing_the_subset(household_factory):
    """A transfer out of a filtered account is cash flow *for that account*.

    Household-wide the legs cancel, which is why excluding them there is exact.
    For a subset they do not cancel, so counting them is what keeps
    ``ΔNW = cash flow + revaluation`` true.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        beth = await owners.create_owner(s, hh, name="Beth")
        beth_acct = await ledger.create_account(
            s, hh, AccountCreate(name="Beth Checking", type="depository", currency="USD",
                                 owner_id=beth.id, current_balance=D("100"),
                                 balance_date=date(2026, 1, 1)))
        alex_acct = await ledger.create_account(
            s, hh, AccountCreate(name="Alex Checking", type="depository", currency="USD",
                                 current_balance=D("0"), balance_date=date(2026, 1, 1)))
        out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=beth_acct.id, amount=D("-40"), transacted_at=_dt(2026, 1, 10)))
        into = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=alex_acct.id, amount=D("40"), transacted_at=_dt(2026, 1, 10)))
        await txns.link_transfer(s, hh, out.id, into.id)
        await ledger.update_account(s, beth_acct.id, AccountUpdate(
            current_balance=D("60"), balance_date=date(2026, 1, 31)))
        await ledger.update_account(s, alex_acct.id, AccountUpdate(
            current_balance=D("40"), balance_date=date(2026, 1, 31)))

        _b, _g, whole = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), granularity="month")
        beth_series = await reports.net_worth_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), beth.id)
        _b, _g, beth_rows = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), beth.id, granularity="month")

    # Household-wide: the transfer cancelled, so nothing happened.
    assert whole[0]["net"] == D("0.0000")
    # Account-scoped: it is the whole reason Beth's balance fell.
    assert beth_series["delta_net_worth"] == D("-40.0000")
    assert beth_series["net_cash_flow"] == D("-40.0000")
    assert beth_series["delta_net_worth"] == (
        beth_series["net_cash_flow"] + beth_series["currency_revaluation"])
    # The row-scoped series still excludes it: two views, two questions.
    assert beth_rows[0]["net"] == D("0.0000")


async def test_net_worth_filter_with_no_accounts_is_zero(household_factory):
    """An owner with nothing must not fall back to the whole household."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await _owner_filter_fixture(s, hh)
        nobody = await owners.create_owner(s, hh, name="Nobody")
        series = await reports.net_worth_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), nobody.id)
        _b, _g, months = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31), nobody.id,
            granularity="month")
    assert series["delta_net_worth"] == D("0.0000")
    assert series["net_cash_flow"] == D("0.0000")
    assert months[0]["net"] == D("0.0000")
