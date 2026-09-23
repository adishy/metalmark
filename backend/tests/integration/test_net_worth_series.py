"""The net-worth line agrees with the number on the Accounts page.

Session 04's audit found the chart and the headline disagreeing by six figures on
the demo capture alone, and every cause was a writer or a valuation rule rather
than the chart: an investment account whose value the series computed from
holdings nobody had entered, a balance edit that rewrote a past point, a card whose
debt counted in the household's favour. The invariant that catches the whole class
is the obvious one — **on the day the balances are dated, the series' value is the
headline** — and it is asserted here for each shape of household that broke it.

Base currency only, deliberately: the headline converts each account at its own
``balance_date``'s rate and the series at the point's, which is a separate gap.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import (
    Account,
    AccountConnection,
    BalanceSnapshot,
    Holding,
    Security,
    SecurityPrice,
    SyncRunEvent,
    Transaction,
)
from app.schemas.ledger import AccountCreate, AccountUpdate
from app.services import imports, ledger, reports, sync
from app.services import investments as inv
from app.services.errors import LedgerError
from app.services.fake_simplefin import FakeProvider
from tests.fakes import simplefin as scenarios
from tests.integration.test_sync import NOW, _make_connection

pytestmark = pytest.mark.integration

D = Decimal


async def _series_matches_headline(session, household_id) -> tuple[Decimal, Decimal]:
    """``(series value, headline)`` on the latest date any balance is dated."""
    accounts = (await session.execute(select(Account))).scalars().all()
    on = max(a.balance_date for a in accounts if a.balance_date is not None)
    headline = (await ledger.net_worth(session, household_id))["net_worth"]
    (point,) = await reports.net_worth_points(session, [on], "USD")
    return point, headline


# ---- derived investment accounts with nothing to derive from ------------------


async def test_a_synced_account_with_holdings_is_in_the_series(household_factory):
    """The demo capture's savings account holds AAPL, so sync types it as an
    investment account. Sync writes no holdings, so valuing it from holdings made
    it $0 on the chart and $114,685.51 on the Accounts page."""
    hid = await household_factory()
    conn = await _make_connection(hid)
    await sync.run_connection_sync(
        hid, conn, provider=FakeProvider(script=[scenarios.demo()]), now=NOW
    )
    async with scoped_session(hid) as s:
        savings = (
            await s.execute(select(Account).where(Account.name == scenarios.DEMO_SAVINGS))
        ).scalar_one()
        assert savings.type == "investment"
        # Sync has no holdings to derive a balance from, so the provider's is it.
        assert savings.balance_source == "stated"
        snapshots = (
            await s.execute(
                select(BalanceSnapshot).where(BalanceSnapshot.account_id == savings.id)
            )
        ).scalars().all()
        assert [x.balance for x in snapshots] == [savings.current_balance]

        point, headline = await _series_matches_headline(s, hid)
    assert point == headline


async def test_a_manual_investment_account_with_a_typed_balance_is_in_the_series(
    household_factory,
):
    """Manual investment accounts stay ``derived`` (ADR-0021) — but until a
    holding or a trade exists there is nothing to derive from, and the balance the
    user typed is the only statement of the account's value there is."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        account = await ledger.create_account(s, hid, AccountCreate(
            name="401k", type="investment", currency="USD",
            current_balance=D("80000"), balance_date=date(2026, 9, 1)))
        assert account.balance_source == "derived"
        point, headline = await _series_matches_headline(s, hid)
    assert headline == D("80000.0000")
    assert point == headline


async def test_the_investments_view_agrees_about_a_typed_balance(household_factory):
    """The portfolio shows the typed balance as the account's value — the whole of
    it unaccounted cash, ADR-0021's plug — rather than the $0 of its positions."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        account = await ledger.create_account(s, hid, AccountCreate(
            name="401k", type="investment", currency="USD",
            current_balance=D("80000"), balance_date=date(2026, 9, 1)))
        valuation = await inv.value_account(s, account, date(2026, 9, 1), "USD")
    assert valuation.balance_account == D("80000")
    assert valuation.unaccounted_cash_base == D("80000.0000")


async def test_the_fallback_still_reconciles(household_factory):
    """A typed-balance investment account is valued like any stated account, so a
    change in it is unexplained balance movement — not a market move nobody priced."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        account = await ledger.create_account(s, hid, AccountCreate(
            name="401k", type="investment", currency="USD",
            current_balance=D("80000"), balance_date=date(2026, 1, 1)))
        s.add(BalanceSnapshot(household_id=hid, account_id=account.id,
                              balance_date=date(2026, 1, 31), balance=D("81000"),
                              currency="USD"))
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert r["delta_net_worth"] == D("1000.0000")
    assert r["market_appreciation"] == D("0.0000")
    assert r["unexplained"] == D("1000.0000")
    assert [row["name"] for row in r["unexplained_by_account"]] == ["401k"]


async def test_a_synced_investment_account_keeps_a_history(household_factory):
    """Stated means sync snapshots it — a second sync on a later day adds a point."""
    hid = await household_factory()
    conn = await _make_connection(hid)
    first = scenarios.demo()
    savings = scenarios.account_named(first, scenarios.DEMO_SAVINGS)
    later = scenarios.replace_account(first, dataclasses.replace(
        savings,
        balance=savings.balance + D("1000"),
        balance_date=savings.balance_date.replace(day=savings.balance_date.day + 1),
    ))
    provider = FakeProvider(script=[first, later])
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    async with scoped_session(hid) as s:
        account = (
            await s.execute(select(Account).where(Account.name == scenarios.DEMO_SAVINGS))
        ).scalar_one()
        balances = (
            await s.execute(
                select(BalanceSnapshot.balance)
                .where(BalanceSnapshot.account_id == account.id)
                .order_by(BalanceSnapshot.balance_date)
            )
        ).scalars().all()
    assert balances == [savings.balance, savings.balance + D("1000")]


# ---- one rule for where a balance lands ---------------------------------------
#
# Every writer — the edit dialog, sync, an OFX statement — goes through
# `ledger.record_balance`: the snapshot is written at the balance's *own* date, and
# the account's current balance moves only when that date is not older than the one
# it has. The dialog used to send the account's old `balance_date` back with a new
# balance, which overwrote a past point; sync did the same whenever the provider
# sent no date; an older OFX statement moved the headline backwards.


async def _snapshots(session, account_id) -> list[tuple[date, Decimal]]:
    return list(
        (
            await session.execute(
                select(BalanceSnapshot.balance_date, BalanceSnapshot.balance)
                .where(BalanceSnapshot.account_id == account_id)
                .order_by(BalanceSnapshot.balance_date)
            )
        ).all()
    )


async def test_a_new_balance_without_a_date_is_recorded_today(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Cash", type="depository", currency="USD",
            current_balance=D("1000"), balance_date=date(2026, 1, 1)))
        await ledger.update_account(s, a.id, AccountUpdate(current_balance=D("3000")))
        today = ledger.today()
        assert await _snapshots(s, a.id) == [
            (date(2026, 1, 1), D("1000.0000")), (today, D("3000.0000"))
        ]
        assert (a.current_balance, a.balance_date) == (D("3000"), today)


async def test_a_backdated_balance_corrects_history_without_moving_the_headline(
    household_factory,
):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Cash", type="depository", currency="USD",
            current_balance=D("1000"), balance_date=date(2026, 3, 1)))
        await ledger.update_account(s, a.id, AccountUpdate(
            current_balance=D("700"), balance_date=date(2026, 2, 1)))
        assert await _snapshots(s, a.id) == [
            (date(2026, 2, 1), D("700.0000")), (date(2026, 3, 1), D("1000.0000"))
        ]
        assert (a.current_balance, a.balance_date) == (D("1000"), date(2026, 3, 1))
        assert (await ledger.net_worth(s, hid))["net_worth"] == D("1000.0000")


async def test_a_rename_writes_no_balance(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Cash", type="depository", currency="USD",
            current_balance=D("1000"), balance_date=date(2026, 1, 1)))
        await ledger.update_account(s, a.id, AccountUpdate(name="Wallet"))
        assert await _snapshots(s, a.id) == [(date(2026, 1, 1), D("1000.0000"))]
        assert a.balance_date == date(2026, 1, 1)


async def test_a_sync_with_no_balance_date_does_not_rewrite_the_last_point(
    household_factory,
):
    hid = await household_factory()
    conn = await _make_connection(hid)
    first = scenarios.demo()
    checking = scenarios.account_named(first, scenarios.DEMO_CHECKING)
    undated = scenarios.replace_account(first, dataclasses.replace(
        checking, balance=checking.balance + D("500"), balance_date=None))
    provider = FakeProvider(script=[first, undated])
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    later = NOW.replace(year=NOW.year + 1)
    await sync.run_connection_sync(hid, conn, provider=provider, now=later)
    async with scoped_session(hid) as s:
        account = (
            await s.execute(select(Account).where(Account.name == scenarios.DEMO_CHECKING))
        ).scalar_one()
        assert await _snapshots(s, account.id) == [
            (checking.balance_date, checking.balance),
            (later.date(), checking.balance + D("500")),
        ]
        assert account.balance_date == later.date()


async def test_an_older_statement_does_not_move_the_balance_backwards(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Cash", type="depository", currency="USD",
            current_balance=D("1000"), balance_date=date(2026, 3, 1)))
        older = SimpleNamespace(
            ledger_balance=D("400"), ledger_balance_at=datetime(2026, 1, 31, tzinfo=UTC)
        )
        await imports._statement_balance(s, a, older)
        assert await _snapshots(s, a.id) == [
            (date(2026, 1, 31), D("400.0000")), (date(2026, 3, 1), D("1000.0000"))
        ]
        assert (a.current_balance, a.balance_date) == (D("1000"), date(2026, 3, 1))


# ---- liabilities: one sign convention (ADR-0043) ------------------------------
#
# The foreign-currency cases use currencies no other test does: `fx_rates` is
# global (ARCHITECTURE §2), so a rate written here is visible to every later test.
#
# Every stored balance is the account's signed balance — debt is negative, and a
# balance moves by exactly its transactions' amounts. That is what SimpleFIN and
# OFX send; the report used to *also* flip liabilities, so a synced card's debt
# counted in the household's favour and every payoff dropped net worth by twice
# the balance.


async def _account(session, household_id, name, type_, *, currency="USD"):
    return await ledger.create_account(session, household_id, AccountCreate(
        name=name, type=type_, currency=currency))


def _snap(session, household_id, account, day, balance, currency="USD"):
    session.add(BalanceSnapshot(household_id=household_id, account_id=account.id,
                                balance_date=day, balance=D(balance), currency=currency))


def _txn(session, household_id, account, day, amount, base_amount, currency="USD"):
    at = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    session.add(Transaction(household_id=household_id, account_id=account.id,
                            transacted_at=at, posted_at=at, amount=D(amount),
                            currency=currency, base_amount=D(base_amount), fx_rate_date=day,
                            description="x", source="manual",
                            import_hash=f"{account.id}{day}{amount}"))


async def test_a_synced_card_counts_against_the_household(household_factory):
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    card = dataclasses.replace(checking, external_id="ACT-card", name="Visa Credit Card",
                               balance=D("-1000.00"), transactions=())
    demo = dataclasses.replace(demo, accounts=demo.accounts + (card,))
    await sync.run_connection_sync(hid, conn, provider=FakeProvider(script=[demo]), now=NOW)
    async with scoped_session(hid) as s:
        visa = (
            await s.execute(select(Account).where(Account.name == "Visa Credit Card"))
        ).scalar_one()
        parts = await reports.net_worth_points_by_account(s, [visa.balance_date], "USD")
        assert parts[visa.id] == [D("-1000.0000")]
        headline = await ledger.net_worth(s, hid)
        assert headline["liabilities"] == D("1000.0000")
        point, net = await _series_matches_headline(s, hid)
    assert point == net == headline["assets"] - D("1000")


async def test_paying_off_a_card_does_not_move_net_worth(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        card = await _account(s, hid, "Card", "credit")
        _snap(s, hid, chk, date(2026, 1, 1), "5000")
        _snap(s, hid, card, date(2026, 1, 1), "-1000")
        _snap(s, hid, chk, date(2026, 1, 20), "4000")
        _snap(s, hid, card, date(2026, 1, 20), "0")
        _txn(s, hid, chk, date(2026, 1, 20), "-1000", "-1000")
        _txn(s, hid, card, date(2026, 1, 20), "1000", "1000")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31),
                                           granularity="week")
    assert {p["net_worth"] for p in r["points"]} == {D("4000.0000")}
    assert r["delta_net_worth"] == D("0.0000")
    assert r["unexplained"] == D("0.0000")


async def test_spending_on_a_card_reconciles(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        card = await _account(s, hid, "Card", "credit")
        _snap(s, hid, card, date(2026, 1, 1), "-100")
        _txn(s, hid, card, date(2026, 1, 10), "-50", "-50")
        _snap(s, hid, card, date(2026, 1, 31), "-150")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert (r["delta_net_worth"], r["net_cash_flow"], r["unexplained"]) == (
        D("-50.0000"), D("-50.0000"), D("0.0000"))


async def test_a_foreign_card_reconciles_with_a_flat_rate(household_factory):
    """The revaluation formula no longer flips a liability's sign on top of its
    balance's: at a flat rate, a krone card's spending is all cash flow."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="DKK", quote_ccy="USD",
                                    rate_date=date(2025, 12, 1), rate=D("1.10"))
        card = await _account(s, hid, "DKK card", "credit", currency="DKK")
        _snap(s, hid, card, date(2026, 1, 1), "-100", "DKK")
        _txn(s, hid, card, date(2026, 1, 10), "-50", "-55", "DKK")
        _snap(s, hid, card, date(2026, 1, 31), "-150", "DKK")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert r["delta_net_worth"] == D("-55.0000")
    assert r["currency_revaluation"] == D("0.0000")
    assert r["unexplained"] == D("0.0000")


async def test_a_rate_move_makes_foreign_debt_cost_more(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="PLN", quote_ccy="USD",
                                    rate_date=date(2025, 12, 1), rate=D("1.10"))
        await ledger.upsert_fx_rate(s, hid, base_ccy="PLN", quote_ccy="USD",
                                    rate_date=date(2026, 1, 31), rate=D("1.20"))
        card = await _account(s, hid, "PLN card", "credit", currency="PLN")
        _snap(s, hid, card, date(2026, 1, 1), "-100", "PLN")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert r["delta_net_worth"] == D("-10.0000")
    assert r["currency_revaluation"] == D("-10.0000")
    assert r["unexplained"] == D("0.0000")


async def test_retyping_an_account_keeps_its_balances(household_factory):
    """Sync guesses a type from the account's name; correcting it must not rewrite
    history — which, with signed balances, it no longer has to."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Sapphire", type="other", currency="USD",
            current_balance=D("-700"), balance_date=date(2026, 1, 1)))
        before = await reports.net_worth_points(s, [date(2026, 1, 1)], "USD")
        await ledger.update_account(s, a.id, AccountUpdate(type="credit"))
        assert (a.type, a.is_asset, a.current_balance) == ("credit", False, D("-700"))
        assert await reports.net_worth_points(s, [date(2026, 1, 1)], "USD") == before
        assert (await ledger.net_worth(s, hid))["liabilities"] == D("700.0000")


async def test_an_account_with_positions_cannot_stop_being_an_investment(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await _account(s, hid, "Brokerage", "investment")
        sec = Security(household_id=hid, name="VTI", ticker="VTI", security_type="etf",
                       currency="USD")
        s.add(sec)
        await s.flush()
        s.add(Holding(household_id=hid, account_id=a.id, security_id=sec.id,
                      quantity=D("1")))
        await s.flush()
        with pytest.raises(LedgerError) as err:
            await ledger.update_account(s, a.id, AccountUpdate(type="depository"))
    assert err.value.status == 409


async def test_retyping_into_an_investment_account_keeps_its_stated_history(
    household_factory,
):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await ledger.create_account(s, hid, AccountCreate(
            name="Savings", type="depository", currency="USD",
            current_balance=D("900"), balance_date=date(2026, 1, 1)))
        await ledger.update_account(s, a.id, AccountUpdate(type="investment"))
        assert (a.balance_source, a.is_asset) == ("stated", True)
        await ledger.update_account(s, a.id, AccountUpdate(type="depository"))
        assert a.balance_source is None


async def test_sync_warns_when_a_liability_reports_a_positive_balance(household_factory):
    """A bridge that sends debt as a positive number would count every card for
    the household. The run says so instead of guessing which sign it meant."""
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    card = dataclasses.replace(checking, external_id="ACT-card", name="Visa Credit Card",
                               balance=D("1000.00"), transactions=())
    demo = dataclasses.replace(demo, accounts=demo.accounts + (card,))
    outcome = await sync.run_connection_sync(
        hid, conn, provider=FakeProvider(script=[demo]), now=NOW)
    async with scoped_session(hid) as s:
        events = (
            await s.execute(
                select(SyncRunEvent).where(SyncRunEvent.sync_run_id == outcome.run_id)
            )
        ).scalars().all()
    warned = [e for e in events if e.event == "balance.liability_positive"]
    assert len(warned) == 1 and warned[0].level == "warning"


# ---- before the first balance: derived backwards (ADR-0045) -------------------
#
# A first sync brings weeks of transactions and one balance. The series used to
# read the account as absent until that balance, drawing a cliff at the connection
# date and calling the account's whole pulled history "unexplained".


def _missing(point) -> dict[str, str]:
    return {m["name"]: m["reason"] for m in point["missing"]}


async def test_the_first_sync_has_no_cliff(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        _txn(s, hid, chk, date(2026, 2, 5), "-200", "-200")
        _txn(s, hid, chk, date(2026, 3, 1), "3000", "3000")
        _snap(s, hid, chk, date(2026, 3, 15), "25000")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 2, 1), date(2026, 3, 31),
                                           granularity="month")
        inside = await reports.net_worth_series(s, hid, date(2026, 2, 4), date(2026, 3, 31))
    # Feb 1 is before the day before its first transaction: not known, and said so.
    assert [p["net_worth"] for p in r["points"]] == [
        D("0.0000"), D("22000.0000"), D("25000.0000")]
    assert _missing(r["points"][0]) == {"Checking": "not_started"}
    assert r["points"][1]["missing"] == r["points"][2]["missing"] == []
    # From the first day it is known, the change is all cash flow.
    assert inside["points"][0]["net_worth"] == D("22200.0000")
    assert (inside["delta_net_worth"], inside["net_cash_flow"], inside["unexplained"]) == (
        D("2800.0000"), D("2800.0000"), D("0.0000"))


async def test_deriving_backwards_reproduces_an_earlier_balance(household_factory):
    """With complete transactions, the derivation from a later balance lands on the
    earlier one exactly — the arithmetic is checked against the bank's own number."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        _txn(s, hid, chk, date(2026, 1, 1), "1000", "1000")
        _txn(s, hid, chk, date(2026, 1, 10), "-100", "-100")
        _txn(s, hid, chk, date(2026, 1, 20), "50", "50")
        _snap(s, hid, chk, date(2026, 1, 31), "950")
        await s.flush()
        derived = await reports.net_worth_points(
            s, [date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 15)], "USD")
    assert derived == [D("0.0000"), D("1000.0000"), D("900.0000")]


async def test_a_window_across_the_first_balance_reconciles(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        _txn(s, hid, chk, date(2026, 1, 3), "500", "500")
        _txn(s, hid, chk, date(2026, 1, 20), "-80", "-80")
        _snap(s, hid, chk, date(2026, 1, 31), "1420")
        _txn(s, hid, chk, date(2026, 2, 5), "-20", "-20")
        _snap(s, hid, chk, date(2026, 2, 10), "1400")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 5), date(2026, 2, 10))
    assert r["points"][0]["net_worth"] == D("1500.0000")
    assert (r["delta_net_worth"], r["net_cash_flow"], r["unexplained"]) == (
        D("-100.0000"), D("-100.0000"), D("0.0000"))


async def test_a_hidden_transaction_still_moved_the_balance(household_factory):
    """Hidden from spending, not from the bank: the derivation counts it, so the
    balance before it is the real one, and the reconciliation names the account."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        _txn(s, hid, chk, date(2026, 1, 3), "500", "500")
        _txn(s, hid, chk, date(2026, 1, 10), "-300", "-300")
        await s.flush()
        hidden = (await s.execute(
            select(Transaction).where(Transaction.amount == D("-300")))).scalar_one()
        hidden.is_hidden = True
        _snap(s, hid, chk, date(2026, 1, 31), "200")
        await s.flush()
        (before,) = await reports.net_worth_points(s, [date(2026, 1, 5)], "USD")
        r = await reports.net_worth_series(s, hid, date(2026, 1, 5), date(2026, 1, 31))
    assert before == D("500.0000")
    assert r["unexplained"] == D("-300.0000")
    assert [row["name"] for row in r["unexplained_by_account"]] == ["Checking"]


async def test_an_investment_account_is_not_derived_backwards(household_factory):
    """A brokerage moves with the market too; B_S − Σ would assert a flat market."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        broker = await _account(s, hid, "Brokerage", "investment")
        broker.balance_source = "stated"
        _txn(s, hid, broker, date(2026, 1, 5), "1000", "1000")
        _snap(s, hid, broker, date(2026, 1, 31), "5000")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 10), date(2026, 1, 31))
    assert r["points"][0]["net_worth"] == D("0.0000")
    assert _missing(r["points"][0]) == {"Brokerage": "not_started"}


async def test_an_account_with_no_balance_is_named(household_factory):
    """Opened blank and filled by an import: transactions, and never a balance."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        card = await _account(s, hid, "Imported card", "credit")
        _txn(s, hid, card, date(2026, 1, 5), "-40", "-40")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert all(_missing(p) == {"Imported card": "no_balance"} for p in r["points"])


async def test_a_missing_rate_is_named_on_the_point(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        acct = await _account(s, hid, "HUF savings", "depository", currency="HUF")
        _snap(s, hid, acct, date(2026, 1, 1), "100000", "HUF")
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 1, 31))
    assert all(_missing(p) == {"HUF savings": "no_rate"} for p in r["points"])


async def test_a_position_first_priced_mid_window_is_not_appreciation(household_factory):
    """Session 04's repro: 100 VTI, first price on Mar 1 — the report said the
    market made $25,000 and nothing was unexplained. Unknown is not zero."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        a = await _account(s, hid, "Brokerage", "investment")
        sec = Security(household_id=hid, name="Vanguard Total", ticker="VTI",
                       security_type="etf", currency="USD")
        s.add(sec)
        await s.flush()
        s.add(Holding(household_id=hid, account_id=a.id, security_id=sec.id,
                      quantity=D("100"), as_of=date(2026, 1, 1)))
        s.add(SecurityPrice(household_id=hid, security_id=sec.id,
                            price_date=date(2026, 3, 1), price=D("250"), currency="USD"))
        await s.flush()
        r = await reports.net_worth_series(s, hid, date(2026, 1, 1), date(2026, 3, 31),
                                           granularity="month")
    assert _missing(r["points"][0]) == {"Brokerage": "no_price"}
    assert r["points"][-1]["missing"] == []
    assert r["market_appreciation"] == D("0.0000")
    assert r["unexplained"] == D("25000.0000")
    assert [row["name"] for row in r["unexplained_by_account"]] == ["Brokerage"]
    assert any("VTI has no price" in w for w in r["warnings"])


async def test_the_headline_converts_at_todays_rate(household_factory):
    """A current view uses the latest rate (ARCHITECTURE §2) — the headline used
    each account's balance-date rate, so a foreign balance entered months ago was
    valued at a months-old rate on the Accounts page and today's on the chart."""
    hid = await household_factory()
    today = ledger.today()
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="CZK", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("0.04"))
        await ledger.upsert_fx_rate(s, hid, base_ccy="CZK", quote_ccy="USD",
                                    rate_date=today, rate=D("0.05"))
        await ledger.create_account(s, hid, AccountCreate(
            name="Koruna", type="depository", currency="CZK",
            current_balance=D("1000"), balance_date=date(2026, 1, 1)))
        headline = await ledger.net_worth(s, hid)
        (point,) = await reports.net_worth_points(s, [today], "USD")
    assert headline["net_worth"] == D("50.0000")
    assert point == headline["net_worth"]


async def test_cash_flow_bars_are_what_each_bucket_says_on_its_own(household_factory):
    """`cash_flow_series` loads the window once and buckets in memory; each bar must
    still be exactly `_cash_flow` over that bucket — edges and late-night rows
    included."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        chk = await _account(s, hid, "Checking", "depository")
        for day, amount in [(date(2026, 1, 1), "100"), (date(2026, 1, 31), "-40"),
                            (date(2026, 2, 1), "-15"), (date(2026, 2, 28), "70"),
                            (date(2026, 3, 15), "-5")]:
            _txn(s, hid, chk, day, amount, amount)
        late = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        s.add(Transaction(household_id=hid, account_id=chk.id, transacted_at=late,
                          posted_at=late, amount=D("-1"), currency="USD",
                          base_amount=D("-1"), fx_rate_date=late.date(),
                          description="late", source="manual", import_hash="late"))
        await s.flush()
        _b, _g, bars = await reports.cash_flow_series(
            s, hid, date(2026, 1, 1), date(2026, 3, 31), granularity="month")
        each = [
            await reports._cash_flow(s, a, b, "USD")
            for a, b in [(date(2026, 1, 1), date(2026, 1, 31)),
                         (date(2026, 2, 1), date(2026, 2, 28)),
                         (date(2026, 3, 1), date(2026, 3, 31))]
        ]
    assert [(p["income"], p["expense"], p["net"]) for p in bars] == [e[:3] for e in each]
    assert bars[0]["expense"] == D("-41.0000")


async def test_the_series_stops_at_today(household_factory):
    """A window that runs past today draws no level for days that have not happened."""
    hid = await household_factory()
    today = ledger.today()
    async with scoped_session(hid) as s:
        await ledger.create_account(s, hid, AccountCreate(
            name="Cash", type="depository", currency="USD",
            current_balance=D("10"), balance_date=today))
        r = await reports.net_worth_series(s, hid, today, today.replace(year=today.year + 1))
    assert [p["date"] for p in r["points"]][-1] == today


# ---- accounts the bank stops reporting, and accounts it names alike -----------


async def _events_named(hid, run_id, event) -> list[SyncRunEvent]:
    async with scoped_session(hid) as s:
        return [
            e for e in (
                await s.execute(select(SyncRunEvent).where(SyncRunEvent.sync_run_id == run_id))
            ).scalars()
            if e.event == event
        ]


async def test_an_account_missing_from_a_fetch_is_reported(household_factory):
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    without = dataclasses.replace(demo, accounts=tuple(
        a for a in demo.accounts if a.name != scenarios.DEMO_CHECKING))
    provider = FakeProvider(script=[demo, without])
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    second = await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    warned = await _events_named(hid, second.run_id, "account.not_reported")
    assert [e.detail["name"] for e in warned] == [scenarios.DEMO_CHECKING]


async def test_an_account_the_bank_stopped_reporting_is_stale(household_factory):
    hid = await household_factory()
    conn = await _make_connection(hid)
    await sync.run_connection_sync(
        hid, conn, provider=FakeProvider(script=[scenarios.demo()]), now=NOW)
    async with scoped_session(hid) as s:
        accounts = {a.name: a for a in (await s.execute(select(Account))).scalars()}
        connection = (
            await s.execute(select(AccountConnection).where(AccountConnection.id == conn))
        ).scalar_one()
        synced = accounts[scenarios.DEMO_SAVINGS].balance_date
        connection.last_synced_at = datetime(synced.year, synced.month, synced.day,
                                             tzinfo=UTC) + timedelta(days=3)
        accounts[scenarios.DEMO_CHECKING].balance_date = synced - timedelta(days=10)
        await s.flush()
        stale = await ledger.stale_since(s)
    assert stale == {accounts[scenarios.DEMO_CHECKING].id: synced - timedelta(days=10)}


def _card(template, external_id, balance, txn_ids=()):
    return dataclasses.replace(
        template, external_id=external_id, name="Blue Cash", balance=D(balance),
        transactions=tuple(
            dataclasses.replace(template.transactions[0], external_id=t) for t in txn_ids
        ),
    )


async def test_two_cards_named_alike_are_two_accounts(household_factory):
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    both = dataclasses.replace(demo, accounts=demo.accounts + (
        _card(checking, "ACT-a", "-100"), _card(checking, "ACT-b", "-900")))
    provider = FakeProvider(script=[both, both])
    first = await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    async with scoped_session(hid) as s:
        cards = {
            a.external_id: a
            for a in (await s.execute(select(Account).where(Account.name == "Blue Cash"))).scalars()
        }
    assert {k: v.current_balance for k, v in cards.items()} == {
        "ACT-a": D("-100.0000"), "ACT-b": D("-900.0000")}
    warned = await _events_named(hid, first.run_id, "account.key_collision")
    assert len(warned) == 1 and warned[0].detail["merged"] is False


async def test_a_new_card_named_like_an_existing_one_gets_its_own_account(household_factory):
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    one = dataclasses.replace(demo, accounts=demo.accounts + (_card(checking, "ACT-a", "-100"),))
    two = dataclasses.replace(demo, accounts=demo.accounts + (
        _card(checking, "ACT-a", "-100"), _card(checking, "ACT-b", "-900")))
    provider = FakeProvider(script=[one, two])
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    async with scoped_session(hid) as s:
        original = (
            await s.execute(select(Account).where(Account.name == "Blue Cash"))
        ).scalar_one()
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    async with scoped_session(hid) as s:
        cards = {
            a.external_id: a
            for a in (await s.execute(select(Account).where(Account.name == "Blue Cash"))).scalars()
        }
    # The account that had the row keeps it — and its plain key, and its history.
    assert cards["ACT-a"].id == original.id
    assert cards["ACT-a"].external_key == original.external_key
    assert cards["ACT-b"].current_balance == D("-900.0000")


async def test_a_row_two_cards_already_share_is_reported_not_split(household_factory):
    """Splitting a merged row would start one card's history partway through while
    the row kept both. It is said, for a person to separate."""
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    a = _card(checking, "ACT-a", "-100", txn_ids=("TX-a1",))
    b = _card(checking, "ACT-b", "-900", txn_ids=("TX-b1",))
    provider = FakeProvider(script=[dataclasses.replace(demo, accounts=demo.accounts + (a,))])
    await sync.run_connection_sync(hid, conn, provider=provider, now=NOW)
    async with scoped_session(hid) as s:
        row = (await s.execute(select(Account).where(Account.name == "Blue Cash"))).scalar_one()
        # What the old sync did with B: its transaction on A's row.
        _txn(s, hid, row, date(2026, 9, 1), "-5", "-5")
        await s.flush()
        (await s.execute(select(Transaction).where(Transaction.amount == D("-5")))).scalar_one(
        ).external_id = "TX-b1"
    both = dataclasses.replace(demo, accounts=demo.accounts + (a, b))
    run = await sync.run_connection_sync(
        hid, conn, provider=FakeProvider(script=[both]), now=NOW)
    async with scoped_session(hid) as s:
        rows = (await s.execute(select(Account).where(Account.name == "Blue Cash"))).scalars().all()
    assert len(rows) == 1
    warned = await _events_named(hid, run.run_id, "account.key_collision")
    assert warned[0].detail["merged"] is True


async def test_alike_cards_after_a_reconnect_do_not_orphan_the_old_row(household_factory):
    """A re-claim re-mints provider ids, so neither card owns the row by id. New
    keys for both would leave the old row carrying its balance beside two new
    accounts; they land on the row instead, and it is reported."""
    hid = await household_factory()
    conn = await _make_connection(hid)
    demo = scenarios.demo()
    checking = scenarios.account_named(demo, scenarios.DEMO_CHECKING)
    await sync.run_connection_sync(hid, conn, provider=FakeProvider(script=[
        dataclasses.replace(demo, accounts=demo.accounts + (_card(checking, "OLD-a", "-100"),))
    ]), now=NOW)
    both = dataclasses.replace(demo, accounts=demo.accounts + (
        _card(checking, "NEW-a", "-100"), _card(checking, "NEW-b", "-900")))
    run = await sync.run_connection_sync(hid, conn, provider=FakeProvider(script=[both]), now=NOW)
    async with scoped_session(hid) as s:
        rows = (await s.execute(select(Account).where(Account.name == "Blue Cash"))).scalars().all()
    assert len(rows) == 1
    warned = await _events_named(hid, run.run_id, "account.key_collision")
    assert warned[0].detail["merged"] is True
