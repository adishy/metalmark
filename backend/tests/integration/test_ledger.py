"""Ledger correctness invariants (M1a acceptance bars, PLAN §acceptance).

These are the tests the whole build exists to make pass: reconciliation in
single- and multi-currency, split base allocation without drift, transfer
exclusion, provenance, and the FX "no rate" flag.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.db import scoped_session
from app.models import CategoryGroup, Tag
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import SplitIn, TransactionCreate, TransactionUpdate
from app.services import ledger, reports
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal


def _dt(y, m, d, hour=0):
    return datetime(y, m, d, hour, tzinfo=UTC)


async def _make_category(session, household_id, group_type: str, name: str):
    g = CategoryGroup(household_id=household_id, name=group_type.title(), type=group_type)
    session.add(g)
    await session.flush()
    c = await ledger.create_category(session, household_id, g.id, name, None, None, 0)
    return c


async def test_single_currency_net_worth(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD",
                                 current_balance=D("1000"), balance_date=date(2026, 1, 1))
        )
        await ledger.create_account(
            s, hh, AccountCreate(name="Card", type="credit", currency="USD",
                                 current_balance=D("300"), balance_date=date(2026, 1, 1))
        )
        nw = await ledger.net_worth(s, hh)
    assert nw["assets"] == D("1000.0000")
    assert nw["liabilities"] == D("300.0000")
    assert nw["net_worth"] == D("700.0000")
    assert nw["unconverted_currencies"] == []


async def test_multi_currency_conversion_and_no_rate_flag(household_factory):
    # GBP, deliberately, and not EUR: ``fx_rates`` is global — it carries no
    # ``household_id`` and the unique key is (base, quote, date) — so a rate any
    # other test writes for USD/EUR is visible here, and "no rate yet" would be
    # false depending on collection order. The pair under test is a pair no other
    # test touches.
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.create_account(
            s, hh, AccountCreate(name="Sterling", type="depository", currency="GBP",
                                 current_balance=D("90"), balance_date=date(2026, 1, 1))
        )
        # No FX rate yet -> unconverted.
        nw = await ledger.net_worth(s, hh)
        assert "GBP" in nw["unconverted_currencies"]

        # 1 USD = 0.90 GBP  => 90 GBP = 100 USD
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="GBP",
                                    rate_date=date(2026, 1, 1), rate=D("0.90"))
        nw = await ledger.net_worth(s, hh)
    assert nw["net_worth"] == D("100.0000")
    assert nw["unconverted_currencies"] == []


async def test_reconciliation_single_currency(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        inc = await _make_category(s, hh, "income", "Salary")
        exp = await _make_category(s, hh, "expense", "Groceries")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD",
                                 current_balance=D("1000"), balance_date=date(2026, 1, 1))
        )
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("500"), transacted_at=_dt(2026, 1, 10),
            category_id=inc.id))
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-200"), transacted_at=_dt(2026, 1, 15),
            category_id=exp.id))
        # End-of-month stated balance reflects the flows.
        from app.schemas.ledger import AccountUpdate
        await ledger.update_account(s, acct.id, AccountUpdate(
            current_balance=D("1300"), balance_date=date(2026, 1, 31)))

        series = await reports.net_worth_series(s, hh, date(2026, 1, 1), date(2026, 1, 31))

    assert series["delta_net_worth"] == D("300.0000")
    assert series["net_cash_flow"] == D("300.0000")
    # single-currency reconciles exactly: revaluation is zero
    assert series["currency_revaluation"] == D("0.0000")
    # ... as is the term nothing owns, which is the point of ADR-0032: three
    # computed terms, one residual, and the residual is genuinely nothing here
    # rather than the place the other three were defined to add up.
    assert series["market_appreciation"] == D("0.0000")
    assert series["unexplained"] == D("0.0000")


async def test_reconciliation_with_fx_revaluation(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Euro", type="depository", currency="EUR",
                                 current_balance=D("90"), balance_date=date(2026, 1, 1))
        )
        # (create_account snapshotted 90 EUR at 2026-01-01)
        # rate weakens: 0.90 -> 0.75 EUR per USD  => 90 EUR: 100 USD -> 120 USD
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="EUR",
                                    rate_date=date(2026, 1, 1), rate=D("0.90"))
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="EUR",
                                    rate_date=date(2026, 1, 31), rate=D("0.75"))
        # same balance snapshotted at end (no cash flow)
        from app.schemas.ledger import AccountUpdate
        await ledger.update_account(s, acct.id, AccountUpdate(
            current_balance=D("90"), balance_date=date(2026, 1, 31)))

        series = await reports.net_worth_series(s, hh, date(2026, 1, 1), date(2026, 1, 31))

    assert series["net_cash_flow"] == D("0.0000")
    assert series["currency_revaluation"] == D("20.0000")
    # Δ net worth == cash flow + revaluation + appreciation + unexplained
    # (the invariant, ADR-0032). The revaluation is now computed from the balance
    # and the two rates rather than being whatever is left over, so the assertion
    # on it above and this one are independent statements.
    assert series["delta_net_worth"] == (
        series["net_cash_flow"]
        + series["currency_revaluation"]
        + series["market_appreciation"]
        + series["unexplained"]
    )
    assert series["unexplained"] == D("0.0000")


async def test_report_range_covers_the_whole_end_day(household_factory):
    """A report range is inclusive of both of its days, at timestamp precision.

    ``transacted_at`` is a timestamptz but report bounds are dates, so Postgres
    reads ``<= end`` as ``<= end 00:00`` — which silently drops everything posted
    *later that same day*, i.e. most of a day's activity, from every report. The
    bound is half-open instead (``< end + 1 day``), which this pins: midday on the
    last day is in, midnight the following morning is out.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        exp = await _make_category(s, hh, "expense", "Groceries")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))

        # Midday on the range's last day: the case that used to vanish.
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-40"), transacted_at=_dt(2026, 1, 31, 12),
            category_id=exp.id))
        # Midnight the next morning: one instant past the range, must stay out.
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-999"), transacted_at=_dt(2026, 2, 1),
            category_id=exp.id))

        _base, cf = await reports.cash_flow_series(
            s, hh, date(2026, 1, 1), date(2026, 1, 31))
        _base, spend, spend_total = await reports.spending_by_category(
            s, hh, date(2026, 1, 1), date(2026, 1, 31))

    assert cf[0]["expense"] == D("-40.0000")
    assert spend_total == D("40.0000")
    assert [row["category_name"] for row in spend] == ["Groceries"]
    assert spend[0]["total"] == D("40.0000")


async def test_report_buckets_do_not_reach_outside_the_window(household_factory):
    """A month bucket is clipped to the window, not summed over its whole month.

    The series derived month-ends from the range and then summed each month from
    its 1st to its own last day — so a window of 15 Jan – 20 Sep reported the
    first half of January and the last ten days of September as well, and the bars
    added up to more than the range the reader had asked for. Two transactions
    outside the window but inside one of its edge months are what that counted;
    the two inside are what it should.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        exp = await _make_category(s, hh, "expense", "Misc")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))

        for amount, day in (
            (D("-11"), (2026, 1, 14)),  # before the window, inside its first month
            (D("-22"), (2026, 1, 15)),  # the window's first day
            (D("-44"), (2026, 9, 20)),  # the window's last day
            (D("-33"), (2026, 9, 21)),  # after it, inside its last month
        ):
            await txns.create_transaction(s, hh, TransactionCreate(
                account_id=acct.id, amount=amount, transacted_at=_dt(*day),
                category_id=exp.id))

        _base, cf = await reports.cash_flow_series(
            s, hh, date(2026, 1, 15), date(2026, 9, 20))

    # Buckets are keyed by the calendar month they *are*, even the two the window
    # clipped — `2026-01`, not `2026-01-15`.
    assert [p["month"] for p in cf] == [f"2026-{m:02d}" for m in range(1, 10)]
    assert cf[0]["expense"] == D("-22.0000")
    assert cf[-1]["expense"] == D("-44.0000")
    # The property the clipping exists for: the chart's total and the range's
    # total are the same money, because the buckets partition the window.
    assert sum((p["net"] for p in cf), D("0")) == D("-66.0000")


async def test_net_worth_points_start_at_the_window_start_and_end_at_its_end(household_factory):
    """The series covers the window rather than the months it happens to touch.

    Both faults were in the same month-end list: a window opening on 15 January
    had no point before 31 January, and one closing on 20 September had a final
    point computed at 30 September — a level read from snapshots dated after the
    range, drawn as though it were inside it.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD",
                                 current_balance=D("1000"), balance_date=date(2026, 1, 15)))

        series = await reports.net_worth_series(
            s, hh, date(2026, 1, 15), date(2026, 9, 20))

        # A balance observed *after* the window, which a month-end read at
        # 2026-09-30 would have picked up and drawn as the window's last point.
        acct.current_balance = D("9999")
        acct.balance_date = date(2026, 9, 25)
        await ledger.upsert_balance_snapshot(s, acct)
        guarded = await reports.net_worth_series(
            s, hh, date(2026, 1, 15), date(2026, 9, 20))

    dates = [p["date"] for p in series["points"]]
    assert dates[0] == date(2026, 1, 15)
    assert dates[-1] == date(2026, 9, 20)
    assert dates == sorted(set(dates))
    # Every point is a level inside the window, and the delta is the change
    # between the first and the last of them.
    assert series["points"][0]["net_worth"] == D("1000.0000")
    assert guarded["points"][-1]["net_worth"] == D("1000.0000")
    assert guarded["delta_net_worth"] == D("0.0000")


async def test_split_base_allocation_no_drift(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="EUR",
                                    rate_date=date(2026, 1, 1), rate=D("0.90"))
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Euro", type="depository", currency="EUR")
        )
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-100"), transacted_at=_dt(2026, 1, 5)))
        parent = await txns.get_transaction(s, txn.id)
        assert parent.base_amount is not None

        result = await txns.replace_splits(s, txn.id, [
            SplitIn(pct=D("1")), SplitIn(pct=D("1")), SplitIn(pct=D("1")),
        ])
        native_sum = sum((sp.amount for sp in result.splits), Decimal(0))
        base_sum = sum((sp.base_amount for sp in result.splits), Decimal(0))
    assert native_sum == D("-100.0000")
    assert base_sum == parent.base_amount  # exact, no convert-then-round drift


async def test_transfer_excluded_from_cash_flow(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        exp = await _make_category(s, hh, "expense", "Misc")
        a = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))
        b = await ledger.create_account(
            s, hh, AccountCreate(name="Savings", type="depository", currency="USD"))
        t_out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        t_in = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        # a real expense that SHOULD count
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-30"), transacted_at=_dt(2026, 1, 11),
            category_id=exp.id))

        # before linking, the two legs distort cash flow
        _b, before = await reports.cash_flow_series(s, hh, date(2026, 1, 1), date(2026, 1, 31))
        await txns.link_transfer(s, hh, t_out.id, t_in.id)
        _b, after = await reports.cash_flow_series(s, hh, date(2026, 1, 1), date(2026, 1, 31))

    # after linking, only the -30 expense remains in cash flow
    assert after[0]["expense"] == D("-30.0000")
    assert after[0]["income"] == D("0.0000")


async def test_transaction_owner_and_provenance(household_factory):
    """Owner is a label row, and setting it is a user-sourced decision."""
    from app.services import owners as owner_svc

    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        alex = await owner_svc.create_owner(s, hh, name="Alex")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Chk", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5),
            owner_id=alex.id))
    assert txn.owner_id == alex.id
    assert txn.field_sources.get("owner") == "user"


async def test_provenance_marks_user_fields(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        cat = await _make_category(s, hh, "expense", "Food")
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-10"), transacted_at=_dt(2026, 1, 5),
            merchant="Cafe", category_id=cat.id))
    assert txn.field_sources.get("category") == "user"
    assert txn.field_sources.get("merchant") == "user"
    assert txn.field_sources.get("amount") == "user"


async def test_split_parent_amount_is_edited_through_its_splits(household_factory):
    """A split parent's amount mirrors its children, and the children are what
    every report sums. Editing the parent alone therefore succeeded while changing
    nothing a user could see, and left the row claiming one number while every
    total showed another. It is refused instead."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-100"), transacted_at=_dt(2026, 1, 5)))
        await txns.replace_splits(s, txn.id, [
            SplitIn(amount=D("-60")), SplitIn(amount=D("-40"))])

        with pytest.raises(LedgerError) as exc:
            await txns.update_transaction(
                s, hh, txn.id, TransactionUpdate(amount=D("-500")))
        assert exc.value.status == 409

        after = await txns.get_transaction(s, txn.id)
        assert after.amount == D("-100.0000")  # untouched by the refused patch
        assert sum((sp.amount for sp in after.splits), Decimal(0)) == after.amount


async def test_moving_a_split_parent_reallocates_child_base_amounts(household_factory):
    """The children carry the base amounts reports sum, so they have to follow the
    parent when its date — and therefore its FX rate — changes. Otherwise they keep
    the old day's conversion and quietly stop summing to the parent."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="EUR",
                                    rate_date=date(2026, 1, 5), rate=D("0.90"))
        await ledger.upsert_fx_rate(s, hh, base_ccy="USD", quote_ccy="EUR",
                                    rate_date=date(2026, 1, 6), rate=D("0.80"))
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Euro", type="depository", currency="EUR"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-100"), transacted_at=_dt(2026, 1, 5)))
        parent = await txns.get_transaction(s, txn.id)
        await txns.replace_splits(s, txn.id, [SplitIn(pct=D("1")), SplitIn(pct=D("1"))])
        base_on_the_5th = parent.base_amount

        await txns.update_transaction(
            s, hh, txn.id, TransactionUpdate(transacted_at=_dt(2026, 1, 6)))
        after = await txns.get_transaction(s, txn.id)
        child_base = sum((sp.base_amount for sp in after.splits), Decimal(0))

    assert after.base_amount != base_on_the_5th  # the rate really did move...
    assert child_base == after.base_amount       # ...and the children followed it


async def test_explicit_null_tag_ids_clears_the_tags(household_factory):
    """Absent means "no change", null means "clear". ``is not None`` collapsed the
    two, so a patch that cleared the tags looked like it had worked and had not."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        tag = Tag(household_id=hh, name="Reimbursable")
        s.add(tag)
        await s.flush()
        acct = await ledger.create_account(
            s, hh, AccountCreate(name="Checking", type="depository", currency="USD"))
        txn = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=acct.id, amount=D("-20"), transacted_at=_dt(2026, 1, 7),
            tag_ids=[tag.id]))
        assert (await txns._tag_ids_for(s, [txn.id])) == {txn.id: [tag.id]}

        # omitted: no change
        await txns.update_transaction(s, hh, txn.id, TransactionUpdate(amount=D("-21")))
        assert (await txns._tag_ids_for(s, [txn.id])) == {txn.id: [tag.id]}

        # explicit null: cleared
        await txns.update_transaction(s, hh, txn.id, TransactionUpdate(tag_ids=None))
        assert (await txns._tag_ids_for(s, [txn.id])) == {}
