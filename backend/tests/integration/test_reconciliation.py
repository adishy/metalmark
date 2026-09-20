"""The four-term identity with investments in it — ADR-0032 §3, §5 and §6.

    Δ net worth = net cash flow + currency revaluation + market appreciation
                  + unexplained

Every term but the last is computed from data, which is the whole point: the
identity can *fail*. These tests are what would notice if a term were quietly
defined as "whatever is left over" — the failure mode ADR-0032 was written to
prevent, where the residual is always zero and therefore says nothing.

The interesting cases are not "does a price move count" (it does, trivially) but
the ways a value change can be mistaken for a gain: a purchase whose cash came from
inside the account, a purchase funded from nowhere, a fee charged against a flat
position.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import (
    Account,
    InvestmentTransaction,
    Owner,
    Security,
    Transaction,
    TransferGroup,
)
from app.services import investments as inv
from app.services import ledger, reports

pytestmark = pytest.mark.integration

D = Decimal
START = date(2026, 1, 1)
END = date(2026, 1, 31)


class _Data:
    """A stand-in for the Pydantic schemas, ``model_fields_set`` included — the
    services use ``is_set`` to tell absent from explicitly null."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.model_fields_set = set(kw)


async def _account(session, household_id, *, name="Brokerage", atype="investment",
                   source="derived", currency="USD"):
    acc = Account(
        household_id=household_id,
        name=name,
        type=atype,
        currency=currency,
        current_balance=D("0.0000"),
        balance_source=None if source == "derived" and atype != "investment" else source,
        is_asset=True,
        is_manual=True,
        owner_id=(await session.execute(select(Owner.id).limit(1))).scalar_one(),
    )
    session.add(acc)
    await session.flush()
    return acc


async def _security(session, household_id, *, name="Vanguard Total", ticker="VTI",
                    stype="etf"):
    sec = Security(
        household_id=household_id, name=name, ticker=ticker, security_type=stype,
        currency="USD",
    )
    session.add(sec)
    await session.flush()
    return sec


async def _price(session, household_id, security, on, price):
    return await inv.upsert_price(
        session, household_id=household_id, security_id=security.id,
        price_date=on, price=D(price),
    )


async def _balance(session, account, balance, on):
    """A balance snapshot, the way every writer of one does it — the account's own
    ``current_balance``/``balance_date`` are what the upsert reads."""
    account.current_balance = D(balance)
    account.balance_date = on
    await ledger.upsert_balance_snapshot(session, account)


async def _event(session, hh, account, sec, *, type, on, quantity, amount,
                 group_id=None):
    return await inv.create_investment_transaction(
        session, hh,
        _Data(
            account_id=account.id,
            security_id=None if sec is None else sec.id,
            type=type,
            trade_date=on,
            quantity=None if quantity is None else D(quantity),
            price=None,
            amount=None if amount is None else D(amount),
            description=None,
            notes=None,
            transfer_group_id=group_id,
        ),
    )


async def _cash_security(session, hh):
    """The money position inside an investment account, priced at par.

    ADR-0033 §4's model of uninvested cash as a real holding, which is what lets a
    purchase be *funded from inside the account* — the case that separates moving
    value from gaining it. Its quantity is the number of currency units, so it is
    established by an event (a contribution, a dividend) rather than by a scalar.
    """
    cash = await _security(session, hh, name="USD cash", ticker=None, stype="cash")
    await _price(session, hh, cash, date(2025, 12, 1), "1")
    await _price(session, hh, cash, END, "1")
    return cash


# ---- the term itself --------------------------------------------------------


async def test_a_price_move_is_appreciation_and_not_cash_flow(household_factory):
    """The base case, and the one the term exists for: 10 shares whose price goes
    100 → 130 grew the household's net worth by 300, and nobody transacted.

    Cash flow is zero, so if the 300 landed anywhere but ``market_appreciation``
    the identity would be reporting a gift.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id, quantity=D("10")
        )
        await _price(s, hh, sec, START, "100")
        await _price(s, hh, sec, END, "130")

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["delta_net_worth"] == D("300.0000")
    assert series["net_cash_flow"] == D("0.0000")
    assert series["currency_revaluation"] == D("0.0000")
    assert series["market_appreciation"] == D("300.0000")
    assert series["unexplained"] == D("0.0000")


async def test_a_buy_inside_the_window_is_not_credited_with_the_windows_move(
    household_factory,
):
    """ADR-0032 §3's stated reason for the formula, pinned numerically.

    The household held 10 shares at 100 and bought 10 more at 120 out of the
    account's own cash; the price ended at 130. The true gain is 300 on the shares
    it already had plus 100 on the ones it just bought — the new shares cannot gain
    the part of the move that happened before they existed.

    The naive ``quantity × Δprice`` formula gives the new shares the whole 30, and
    a formula that counted the cash sale as money *leaving* the portfolio would
    call the purchase itself a 1,200 gain. Both are why the term is computed from
    values on both sides of the window.
    """
    before = date(2025, 12, 15)
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await _price(s, hh, sec, date(2025, 12, 1), "100")
        cash = await _cash_security(s, hh)
        # The opening position is recorded as history too, and that is ADR-0034
        # rather than convenience: once an account has trades in a security, the
        # trades *are* the position, so a hand-entered scalar for the same holding
        # would be ignored and the 10 shares would silently vanish. A household
        # that starts keeping trade history has to enter its opening position as
        # one — which is why `manual_quantity` is still reported, so a UI can show
        # what the history overrode.
        await _event(
            s, hh, acc, cash, type="transfer", on=before, quantity="2200", amount="2200"
        )
        await _event(
            s, hh, acc, sec, type="buy", on=before, quantity="10", amount="-1000"
        )
        # ... and the window contains only the second purchase.
        await _event(
            s, hh, acc, cash, type="sell", on=date(2026, 1, 15),
            quantity="-1200", amount="1200",
        )
        await _event(
            s, hh, acc, sec, type="buy", on=date(2026, 1, 15),
            quantity="10", amount="-1200",
        )
        await _price(s, hh, sec, END, "130")

        series = await reports.net_worth_series(s, hh, START, END)

    # 10 × 100 = 1000 → 20 × 130 = 2600, of which 1200 was already the household's
    # money sitting in the account's cash position. So 400 of *gain*, not 1600 of
    # movement — and the account's own stored balance never moved with the trade.
    assert series["delta_net_worth"] == D("400.0000")
    assert series["net_cash_flow"] == D("0.0000")
    assert series["market_appreciation"] == D("400.0000")
    assert series["unexplained"] == D("0.0000")


async def test_an_unfunded_buy_is_reported_rather_than_absorbed(household_factory):
    """A recorded buy with nothing funding it is money from nowhere, and the
    identity has to say so.

    This is the case that separates "one residual, computed last" from "one
    residual, defined to be zero": the derived balance really does rise by the
    purchase amount, no cash flow explains it, and no price moved. A report that
    folded the difference into appreciation would be inventing a 1,200 gain on a
    position worth exactly what was paid for it.

    1,200 is the honest number. The day a contribution is modelled as the cash
    holding ADR-0033 §4 describes — rather than left implicit — it becomes 0, which
    is why it is asserted as an exact value and not as a range.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await _price(s, hh, sec, START, "120")
        await _price(s, hh, sec, END, "120")
        await _event(
            s, hh, acc, sec, type="buy", on=date(2026, 1, 15),
            quantity="10", amount="-1200",
        )

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["delta_net_worth"] == D("1200.0000")
    assert series["market_appreciation"] == D("0.0000")
    assert series["unexplained"] == D("1200.0000")


async def test_a_fee_paid_from_the_account_is_expense_not_a_market_loss(
    household_factory,
):
    """ADR-0033 §2: a fee is expense. Charging one against a flat position has to
    land in ``net_cash_flow`` — reporting it as appreciation would relabel the
    household's own costs as the market going down.

    The fee has no instrument of its own (the model says so: ``security_id`` is
    nullable for exactly this), and it is paid in cash out of the account, so the
    value leaves through the cash holding.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id, quantity=D("10")
        )
        await _price(s, hh, sec, START, "100")
        await _price(s, hh, sec, END, "100")
        cash = await _cash_security(s, hh)
        await _event(
            s, hh, acc, cash, type="transfer", on=date(2025, 12, 15),
            quantity="100", amount="100",
        )
        await _event(
            s, hh, acc, cash, type="fee", on=date(2026, 1, 10),
            quantity="-25", amount="-25",
        )

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["delta_net_worth"] == D("-25.0000")
    assert series["net_cash_flow"] == D("-25.0000")
    assert series["market_appreciation"] == D("0.0000")
    assert series["unexplained"] == D("0.0000")


async def test_a_dividend_is_income_and_the_position_still_appreciates(
    household_factory,
):
    """ADR-0033 §2 read against §3 in one window: a dividend is income, a price
    move is appreciation, and neither is allowed to absorb the other.

    The report reads ``investment_transactions`` for the income because an
    investment account has no ``transactions`` rows at all (ADR-0033 §1), so a
    cash-flow report wired only to the transaction table would miss it entirely.
    And the cash arrives: a dividend paid into the account is a rise in its cash
    position (ADR-0033 §4), which is what keeps the 50 out of ``unexplained``.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id, quantity=D("10")
        )
        await _price(s, hh, sec, START, "100")
        await _price(s, hh, sec, END, "130")
        cash = await _cash_security(s, hh)
        await _event(
            s, hh, acc, cash, type="dividend", on=date(2026, 1, 20),
            quantity="50", amount="50",
        )

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["net_cash_flow"] == D("50.0000")
    assert series["market_appreciation"] == D("300.0000")
    assert series["delta_net_worth"] == D("350.0000")
    assert series["unexplained"] == D("0.0000")


async def test_a_contribution_crosses_the_boundary_without_reading_as_spending(
    household_factory,
):
    """ADR-0033 §3, end to end: money moves from checking into a derived
    investment account as one ``transfer_groups`` row with a leg on each side.

    Three things have to be true at once, and only all three together make the
    identity exact — which is the point of testing them in one window:

      * the contribution is **not income** (it is the household's own money),
      * the investment account's balance **does rise** by it (ADR-0033 §4: it lands
        in the cash position, which is why the fold had to learn about transfers),
      * and the two accounts' values move by equal and opposite amounts, so the
        household is neither richer nor poorer for having moved it.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        checking = await _account(
            s, hh, name="Checking", atype="depository", source=None
        )
        acc = await _account(s, hh)
        cash = await _cash_security(s, hh)
        await _balance(s, checking, "5000", START)
        await _balance(s, checking, "3800", END)

        group = TransferGroup(household_id=hh)
        s.add(group)
        await s.flush()
        s.add(
            Transaction(
                household_id=hh,
                account_id=checking.id,
                amount=D("-1200"),
                currency="USD",
                base_amount=D("-1200"),
                transacted_at=date(2026, 1, 15),
                transfer_group_id=group.id,
                source="manual",
            )
        )
        await s.flush()
        contribution = await _event(
            s, hh, acc, cash, type="transfer", on=date(2026, 1, 15),
            quantity="1200", amount="1200", group_id=group.id,
        )

        series = await reports.net_worth_series(s, hh, START, END)
        balance = acc.current_balance

    assert contribution.transfer_group_id == group.id  # one group, two legs
    assert balance == D("1200.0000")  # the money arrived
    assert series["delta_net_worth"] == D("0.0000")
    assert series["net_cash_flow"] == D("0.0000")
    assert series["market_appreciation"] == D("0.0000")
    assert series["unexplained"] == D("0.0000")


# ---- the guard rails --------------------------------------------------------


async def test_a_stated_investment_account_is_not_given_a_market_return(
    household_factory,
):
    """ADR-0032 §6. A ``stated`` account's balance is the provider's number and we
    hold no quantities to compute from, so its change is not appreciation.

    Inventing one would be worse than leaving it unexplained: the household would
    read a market return we made up, on an account where we cannot see what is
    actually held.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh, name="Fidelity", source="stated")
        await _balance(s, acc, "1000", START)
        await _balance(s, acc, "1500", END)

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["delta_net_worth"] == D("500.0000")
    assert series["market_appreciation"] == D("0.0000")
    # Stated, unexplained and *reported*: the account's history is not ours to
    # attribute, and the number says so rather than guessing.
    assert series["unexplained"] == D("500.0000")


async def test_a_hidden_account_is_out_of_every_term_not_just_the_total(
    household_factory,
):
    """An account the household hid is out of the net worth **and** out of the cash
    flow that decomposes it.

    One-sided — hidden from the total but still counted as income — would leave the
    identity unbalanced by exactly the hidden account's activity, and the
    difference would surface as ``unexplained``: a real number wearing the wrong
    name. So the hidden account here has income in the window and a stated balance
    that did not move, which is the shape that exposes it.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        visible = await _account(s, hh, name="Checking", atype="depository", source=None)
        hidden = await _account(s, hh, name="Old bank", atype="depository", source=None)
        hidden.is_hidden = True
        for acc in (visible, hidden):
            await _balance(s, acc, "1000", START)
            await _balance(s, acc, "1000", END)
        s.add(
            Transaction(
                household_id=hh,
                account_id=hidden.id,
                amount=D("700"),
                currency="USD",
                base_amount=D("700"),
                transacted_at=date(2026, 1, 10),
                source="manual",
            )
        )
        await s.flush()

        series = await reports.net_worth_series(s, hh, START, END)

    assert series["delta_net_worth"] == D("0.0000")
    assert series["net_cash_flow"] == D("0.0000")
    assert series["unexplained"] == D("0.0000")


async def test_an_investment_account_has_no_transactions_at_all(household_factory):
    """ADR-0033 §1's invariant, as the one query the ADR says makes it checkable.

    It is load-bearing three times over — the appreciation term, the cash-flow
    term and the boundary transfer all assume the cash world cannot see this
    account's ledger. A row here would be counted by every report that reads
    ``transactions`` *and* by the holding the same money bought.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await _price(s, hh, sec, START, "100")
        await _price(s, hh, sec, END, "120")
        await _event(
            s, hh, acc, sec, type="buy", on=START, quantity="10", amount="-1000"
        )
        rows = (
            await s.execute(
                select(Transaction).where(Transaction.account_id == acc.id)
            )
        ).scalars().all()
        # ... while the events are all there, so the emptiness is the model and not
        # a missing write.
        events = (
            await s.execute(
                select(InvestmentTransaction).where(
                    InvestmentTransaction.account_id == acc.id
                )
            )
        ).scalars().all()

    assert rows == []
    assert len(events) == 1
