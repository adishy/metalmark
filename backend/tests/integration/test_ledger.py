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
from app.models import CategoryGroup
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import SplitIn, TransactionCreate
from app.services import ledger, reports
from app.services import transactions as txns

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
    # Δ net worth == cash flow + revaluation (the invariant)
    assert series["delta_net_worth"] == series["net_cash_flow"] + series["currency_revaluation"]


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
