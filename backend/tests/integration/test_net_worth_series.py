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
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import Account, BalanceSnapshot
from app.schemas.ledger import AccountCreate
from app.services import ledger, reports, sync
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
