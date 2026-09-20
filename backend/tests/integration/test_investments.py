"""Investment valuation, allocation and cost basis (ADR-0011/0020/0021).

The bars these pin, in the order the ADRs argue for them:
  * a derived balance is Σ(quantity × price) and nothing else (ADR-0011);
  * "unpriced" is reported, never silently zero (ADR-0032 §5);
  * a stated account's plug makes the allocation view reconcile (ADR-0021);
  * cost basis comes from history when history exists and the manual scalar
    otherwise, never both (ADR-0020).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import (
    Account,
    BalanceSnapshot,
    FxRate,
    Holding,
    InvestmentTransaction,
    Owner,
    Security,
    SecurityPrice,
)
from app.services import investments as inv
from app.services import reports

pytestmark = pytest.mark.integration

ON = date(2026, 9, 20)
BASE = "USD"


async def _owner(session) -> object:
    return (await session.execute(select(Owner.id).limit(1))).scalar_one()


async def _account(session, household_id, *, name="Brokerage", currency="USD", source="derived",
                   balance=Decimal("0.0000")):
    acc = Account(
        household_id=household_id,
        name=name,
        type="investment",
        currency=currency,
        current_balance=balance,
        balance_source=source,
        is_asset=True,
        is_manual=True,
        owner_id=await _owner(session),
    )
    session.add(acc)
    await session.flush()
    return acc


async def _security(session, household_id, *, name="VTI", ticker="VTI", ccy="USD",
                    stype="etf"):
    sec = Security(
        household_id=household_id, name=name, ticker=ticker, security_type=stype, currency=ccy
    )
    session.add(sec)
    await session.flush()
    return sec


async def _price(session, household_id, security, on, value, ccy=None):
    session.add(
        SecurityPrice(
            household_id=household_id,
            security_id=security.id,
            price_date=on,
            price=Decimal(value),
            currency=ccy or security.currency,
        )
    )
    await session.flush()


async def _holding(session, household_id, account, security, qty, basis=None):
    h = Holding(
        household_id=household_id,
        account_id=account.id,
        security_id=security.id,
        quantity=Decimal(qty),
        cost_basis=None if basis is None else Decimal(basis),
    )
    session.add(h)
    await session.flush()
    return h


# ---- valuation -------------------------------------------------------------


async def test_latest_price_is_the_latest_on_or_before_and_never_the_future(
    household_factory,
):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        sec = await _security(s, hh)
        await _price(s, hh, sec, date(2026, 1, 1), "100")
        await _price(s, hh, sec, date(2026, 6, 1), "110")
        await _price(s, hh, sec, date(2026, 12, 1), "999")  # future

        got = await inv.latest_prices(s, [sec.id], date(2026, 9, 20))
        assert got[sec.id].price == Decimal("110.00000000")
        assert got[sec.id].price_date == date(2026, 6, 1)

        # A date before any price has none at all — not the earliest one.
        assert await inv.latest_prices(s, [sec.id], date(2025, 1, 1)) == {}


async def test_derived_balance_is_the_sum_of_quantity_times_price(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        a = await _security(s, hh, name="A", ticker="AAA")
        b = await _security(s, hh, name="B", ticker="BBB")
        await _price(s, hh, a, ON, "10")
        await _price(s, hh, b, ON, "50")
        await _holding(s, hh, acc, a, "100")
        await _holding(s, hh, acc, b, "2")

        v = await inv.value_account(s, acc, ON, BASE)
        assert v.market_value_base == Decimal("1100.0000")
        assert v.is_fully_valued
        assert v.max_stale_days == 0


async def test_an_unpriced_position_is_reported_not_zeroed(household_factory):
    """The bar ADR-0032 §5 sets: a flat line that means "no data" and one that
    means "the market was flat" must not render identically."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        priced = await _security(s, hh, name="Priced", ticker="PRI")
        unpriced = await _security(s, hh, name="Unpriced", ticker="UNP")
        await _price(s, hh, priced, ON, "10")
        await _holding(s, hh, acc, priced, "10")
        await _holding(s, hh, acc, unpriced, "5")

        v = await inv.value_account(s, acc, ON, BASE)

        assert v.market_value_base == Decimal("100.0000")
        assert not v.is_fully_valued
        assert [h.ticker for h in v.unpriced] == ["UNP"]
        assert v.unpriced[0].value_base is None
        assert v.unpriced[0].reason == inv.NO_PRICE
        # And the staleness of the prices that *were* used is still stated.
        assert v.oldest_price_date == ON


async def test_a_stale_price_is_dated_not_hidden(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await _price(s, hh, sec, ON - timedelta(days=200), "10")
        await _holding(s, hh, acc, sec, "10")

        v = await inv.value_account(s, acc, ON, BASE)
        # The value is computed from the stale price (freezing is the correct
        # arithmetic) — and the age travels with it.
        assert v.market_value_base == Decimal("100.0000")
        assert v.max_stale_days == 200


async def test_a_position_valued_in_another_currency_converts(household_factory):
    # JPY, deliberately, and not EUR: ``fx_rates`` is global reference data — no
    # household_id, unique on (base, quote, date) — so a rate written by another
    # test is visible here and would decide this conversion by collection order.
    # EUR is what test_ledger.py and the demo seed reach for, GBP test_ledger and
    # CHF test_transfers (both say so in comments of their own). JPY appears only
    # in ``test_money``'s pure functions, which never touch the table.
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh, name="Yen fund", ticker="YENF", ccy="JPY")
        await _price(s, hh, sec, ON, "1000")
        await _holding(s, hh, acc, sec, "10")
        s.add(
            FxRate(
                base_currency="JPY",
                quote_currency="USD",
                rate_date=ON,
                rate=Decimal("0.00650000"),
                source="manual",
            )
        )
        await s.flush()

        v = await inv.value_account(s, acc, ON, BASE)
        assert v.market_value_base == Decimal("65.0000")
        assert v.holdings[0].value_native == Decimal("10000.00000000")


async def test_a_missing_rate_is_reported_as_such_rather_than_unpriced(
    household_factory,
):
    """'No price' and 'no rate' are different failures with different repairs, so
    they are different states (ADR-0017 §3's "no rate" is never a zero).

    ``XTS`` is ISO 4217's code *reserved for testing*, which is what makes this
    assertion order-independent: a real currency's rate could be written by any
    other test in the session (see the note above on global ``fx_rates``) and the
    test would then be asserting the opposite of what it says.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh, name="Test fund", ticker="XTSF", ccy="XTS")
        await _price(s, hh, sec, ON, "100")
        await _holding(s, hh, acc, sec, "10")

        v = await inv.value_account(s, acc, ON, BASE)
        assert v.market_value_base == Decimal("0.0000")
        assert [h.ticker for h in v.no_rate] == ["XTSF"]
        assert v.unpriced == []


# ---- the stated-account plug (ADR-0021) ------------------------------------


async def test_a_stated_account_reports_the_gap_as_unaccounted_cash(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, source="stated", balance=Decimal("1000.0000"))
        sec = await _security(s, hh)
        await _price(s, hh, sec, ON, "10")
        await _holding(s, hh, acc, sec, "60")

        v = await inv.value_account(s, acc, ON, BASE)
        assert v.market_value_base == Decimal("600.0000")
        assert v.unaccounted_cash_base == Decimal("400.0000")

        # And the allocation view reconciles to the stated balance, with the plug
        # folded into cash rather than dropped.
        result = await inv.allocation(s, on=ON, base_ccy=BASE)
        assert result["total_base"] == Decimal("1000.0000")
        cash = [r for r in result["rows"] if r["label"] == "Unaccounted cash"]
        assert len(cash) == 1
        assert cash[0]["value_base"] == Decimal("400.0000")
        assert cash[0]["percent"] == Decimal("40.0000")


async def test_a_derived_account_has_no_plug(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, source="derived", balance=Decimal("999.0000"))
        sec = await _security(s, hh)
        await _price(s, hh, sec, ON, "10")
        await _holding(s, hh, acc, sec, "3")

        v = await inv.value_account(s, acc, ON, BASE)
        assert v.unaccounted_cash_base is None
        # The stored balance is not consulted at all: the holdings are the truth.
        assert v.market_value_base == Decimal("30.0000")


# ---- the balance writer ----------------------------------------------------


async def test_recompute_writes_the_balance_and_a_snapshot(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, source="derived")
        sec = await _security(s, hh)
        await _price(s, hh, sec, ON, "25")
        await _holding(s, hh, acc, sec, "4")

        new_balance = await inv.recompute_derived_balance(s, acc, on=ON, base_ccy=BASE)
        assert new_balance == Decimal("100.0000")
        assert acc.current_balance == Decimal("100.0000")
        assert acc.balance_date == ON

        snaps = (
            await s.execute(
                select(BalanceSnapshot.balance).where(BalanceSnapshot.account_id == acc.id)
            )
        ).scalars().all()
        assert snaps == [Decimal("100.0000")]


async def test_recompute_refuses_to_snapshot_a_partially_valued_position(
    household_factory,
):
    """A snapshot is keyed by date and looks authoritative forever, so a partial
    sum must not become history: the position would be frozen at the wrong value
    and no later recompute would revisit that date."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, source="derived")
        priced = await _security(s, hh, name="Priced", ticker="PRI")
        unpriced = await _security(s, hh, name="Unpriced", ticker="UNP")
        await _price(s, hh, priced, ON, "10")
        await _holding(s, hh, acc, priced, "10")
        await _holding(s, hh, acc, unpriced, "5")

        await inv.recompute_derived_balance(s, acc, on=ON, base_ccy=BASE)

        snaps = (
            await s.execute(
                select(BalanceSnapshot.balance).where(BalanceSnapshot.account_id == acc.id)
            )
        ).scalars().all()
        assert snaps == []


async def test_recompute_leaves_a_stated_account_alone(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, source="stated", balance=Decimal("777.0000"))
        sec = await _security(s, hh)
        await _price(s, hh, sec, ON, "10")
        await _holding(s, hh, acc, sec, "5")

        assert await inv.recompute_derived_balance(s, acc, on=ON, base_ccy=BASE) is None
        assert acc.current_balance == Decimal("777.0000")


# ---- cost basis (ADR-0020) -------------------------------------------------


async def test_basis_is_derived_from_history_when_history_exists(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        # Two buys: 10 @ $10 and 10 @ $20 -> basis 300, quantity 20, avg $15.
        s.add_all(
            [
                InvestmentTransaction(
                    household_id=hh, account_id=acc.id, security_id=sec.id, type="buy",
                    trade_date=date(2026, 1, 5), quantity=Decimal("10"),
                    price=Decimal("10"), amount=Decimal("-100"), currency="USD",
                ),
                InvestmentTransaction(
                    household_id=hh, account_id=acc.id, security_id=sec.id, type="buy",
                    trade_date=date(2026, 2, 5), quantity=Decimal("10"),
                    price=Decimal("20"), amount=Decimal("-200"), currency="USD",
                ),
            ]
        )
        await s.flush()

        basis = await inv.cost_basis_from_history(s, account_id=acc.id, security_id=sec.id)
        assert basis == Decimal("300.0000")

        # Sell 10 of 20: half the basis leaves, half stays.
        s.add(
            InvestmentTransaction(
                household_id=hh, account_id=acc.id, security_id=sec.id, type="sell",
                trade_date=date(2026, 3, 5), quantity=Decimal("-10"),
                price=Decimal("30"), amount=Decimal("300"), currency="USD",
            )
        )
        await s.flush()
        basis = await inv.cost_basis_from_history(s, account_id=acc.id, security_id=sec.id)
        assert basis == Decimal("150.0000")


async def test_income_and_fees_do_not_move_basis(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        s.add_all(
            [
                InvestmentTransaction(
                    household_id=hh, account_id=acc.id, security_id=sec.id, type="buy",
                    trade_date=date(2026, 1, 5), quantity=Decimal("10"),
                    price=Decimal("10"), amount=Decimal("-100"), currency="USD",
                ),
                InvestmentTransaction(
                    household_id=hh, account_id=acc.id, security_id=sec.id, type="dividend",
                    trade_date=date(2026, 2, 5), amount=Decimal("7"), currency="USD",
                ),
                InvestmentTransaction(
                    household_id=hh, account_id=acc.id, security_id=sec.id, type="fee",
                    trade_date=date(2026, 3, 5), amount=Decimal("-3"), currency="USD",
                ),
            ]
        )
        await s.flush()
        basis = await inv.cost_basis_from_history(s, account_id=acc.id, security_id=sec.id)
        assert basis == Decimal("100.0000")


async def test_no_history_falls_back_to_the_manual_scalar(household_factory):
    """ADR-0020's single-authority rule: exactly one of the two is used, and the
    discriminator is whether history exists — not which was written last."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        holding = await _holding(s, hh, acc, sec, "10", basis="250")

        assert (
            await inv.cost_basis_from_history(s, account_id=acc.id, security_id=sec.id)
            is None
        )
        basis, source = await inv.effective_cost_basis(s, holding)
        assert (basis, source) == (Decimal("250"), "manual")

        # Once history exists it wins outright, and the scalar is ignored — not
        # blended, which would be an answer neither ADR describes.
        s.add(
            InvestmentTransaction(
                household_id=hh, account_id=acc.id, security_id=sec.id, type="buy",
                trade_date=date(2026, 1, 5), quantity=Decimal("10"),
                price=Decimal("9"), amount=Decimal("-90"), currency="USD",
            )
        )
        await s.flush()
        basis, source = await inv.effective_cost_basis(s, holding)
        assert (basis, source) == (Decimal("90.0000"), "history")


# ---- allocation ------------------------------------------------------------


async def test_allocation_aggregates_one_security_across_accounts(household_factory):
    """ADR-0011's whole point: the same instrument in two accounts is one line."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        one = await _account(s, hh, name="Brokerage")
        two = await _account(s, hh, name="IRA")
        shared = await _security(s, hh, name="VTI", ticker="VTI")
        other = await _security(s, hh, name="BND", ticker="BND")
        await _price(s, hh, shared, ON, "100")
        await _price(s, hh, other, ON, "50")
        await _holding(s, hh, one, shared, "10")
        await _holding(s, hh, two, shared, "5")
        await _holding(s, hh, one, other, "10")

        result = await inv.allocation(s, on=ON, base_ccy=BASE)

        assert result["total_base"] == Decimal("2000.0000")  # 1500 + 500
        by_label = {r["label"]: r for r in result["rows"]}
        assert by_label["VTI"]["value_base"] == Decimal("1500.0000")
        assert by_label["VTI"]["holdings"] == 2
        assert by_label["VTI"]["percent"] == Decimal("75.0000")
        assert by_label["BND"]["percent"] == Decimal("25.0000")
        assert sum(r["percent"] for r in result["rows"]) == Decimal("100.0000")


async def test_allocation_by_type_puts_cash_in_its_own_group(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        stock = await _security(s, hh, name="VTI", ticker="VTI", stype="etf")
        cash = await _security(s, hh, name="USD cash", ticker=None, stype="cash")
        await _price(s, hh, stock, ON, "100")
        await _price(s, hh, cash, ON, "1")
        await _holding(s, hh, acc, stock, "10")
        await _holding(s, hh, acc, cash, "500")  # ADR-0033 §4: real, not the plug

        result = await inv.allocation(s, on=ON, base_ccy=BASE, group_by="type")
        by_label = {r["label"]: r for r in result["rows"]}
        assert by_label["etf"]["value_base"] == Decimal("1000.0000")
        assert by_label["cash"]["value_base"] == Decimal("500.0000")


# ---- what a buy does to the balance (ADR-0033) -----------------------------


async def test_a_buy_moves_value_between_holdings_and_not_the_account(
    household_factory,
):
    """ADR-0033 §4's table, as an assertion. This is the property that makes
    ADR-0032's appreciation term computable, so it is worth pinning directly."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        stock = await _security(s, hh, name="VTI", ticker="VTI", stype="etf")
        cash = await _security(s, hh, name="USD cash", ticker=None, stype="cash")
        await _price(s, hh, stock, ON, "100")
        await _price(s, hh, cash, ON, "1")
        held = await _holding(s, hh, acc, cash, "1000")

        before = (await inv.value_account(s, acc, ON, BASE)).market_value_base
        assert before == Decimal("1000.0000")

        # Buy 5 shares: cash holding down 500, new stock position up 500.
        held.quantity = Decimal("500")
        s.add(
            Holding(
                household_id=hh,
                account_id=acc.id,
                security_id=stock.id,
                quantity=Decimal("5"),
            )
        )
        await s.flush()

        after = (await inv.value_account(s, acc, ON, BASE)).market_value_base
        assert after == before, "a buy must not move the account's value"


async def test_an_owner_filtered_cash_flow_does_not_total_the_households_dividends(
    household_factory,
):
    """Row-scoping is *total*, and an investment event's row is its account's.

    A dividend has no per-row owner — there is no column for one — so it used to
    skip the ``owner_id`` filter entirely, on the reasoning that this term feeds
    the account-scoped net-worth decomposition and row-scoping half of it would
    make the identity assert something false. The reasoning was right and the
    conclusion was wrong: it holds for the net-worth call site, which passes
    ``account_ids``, and not for ``/reports/cash-flow``, which declares
    ``attribution: "row"`` — where the carve-out made one owner's report include
    every other owner's dividends.

    Both halves are asserted, because "stop counting dividends" would pass the
    first assertion on its own.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = Owner(household_id=hh, name="Alex", kind="person", sort=1)
        sam = Owner(household_id=hh, name="Sam", kind="person", sort=2)
        s.add_all([alex, sam])
        await s.flush()

        alex_acct = await _account(s, hh, name="Alex Brokerage")
        sam_acct = await _account(s, hh, name="Sam Brokerage")
        alex_acct.owner_id = alex.id
        sam_acct.owner_id = sam.id
        s.add_all(
            [
                InvestmentTransaction(
                    household_id=hh, account_id=alex_acct.id, type="dividend",
                    trade_date=date(2026, 2, 5), amount=Decimal("7"), currency="USD",
                ),
                InvestmentTransaction(
                    household_id=hh, account_id=sam_acct.id, type="dividend",
                    trade_date=date(2026, 2, 6), amount=Decimal("90"), currency="USD",
                ),
            ]
        )
        await s.flush()

        window = (date(2026, 1, 1), date(2026, 2, 28))

        # Unfiltered, the household's income is both dividends.
        _b, _g, all_points = await reports.cash_flow_series(s, hh, *window, None, "month")
        assert [p["income"] for p in all_points] == [Decimal("0.0000"), Decimal("97.0000")]

        # Filtered to Alex, it is Alex's only.
        _b, _g, alex_points = await reports.cash_flow_series(s, hh, *window, alex.id, "month")
        assert [p["income"] for p in alex_points] == [Decimal("0.0000"), Decimal("7.0000")]

        # And the net-worth decomposition is untouched: it scopes by *account*, so
        # it already saw one side. This is the invariant the old carve-out was
        # protecting, and it never needed the carve-out to hold.
        series = await reports.net_worth_series(s, hh, *window, alex.id, "month")
        assert series["net_cash_flow"] == Decimal("7.0000")
