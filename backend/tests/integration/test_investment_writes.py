"""The investment write path: what a write moves, and what it refuses.

The bars, all of them stated somewhere in the ADRs and none of them obvious from
the code:

  * a write that can move a derived balance recomputes it — ADR-0011 makes the
    balance a function of the holdings, so a skipped recompute leaves
    ``current_balance`` describing a portfolio that no longer exists;
  * a position can exist as recorded trades alone (ADR-0034), so recording a buy
    in a security the account has never held produces a *position*, not silence;
  * a manual write to a history-owned column is refused with 409, not ignored;
  * deleting a security recomputes the accounts it was held in, collected
    *before* the delete and from both writers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import Account, Holding, InvestmentTransaction, Owner, Security
from app.services import investments as inv
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

ON = date(2026, 9, 20)
BASE = "USD"


async def _owner(session) -> object:
    return (await session.execute(select(Owner.id).limit(1))).scalar_one()


async def _account(session, household_id, *, name="Brokerage", atype="investment",
                   source="derived", balance=Decimal("0.0000")):
    acc = Account(
        household_id=household_id,
        name=name,
        type=atype,
        currency="USD",
        current_balance=balance,
        balance_source=source,
        is_asset=True,
        is_manual=True,
        owner_id=await _owner(session),
    )
    session.add(acc)
    await session.flush()
    return acc


async def _security(session, household_id, *, name="VTI", ticker="VTI"):
    sec = Security(
        household_id=household_id, name=name, ticker=ticker, security_type="etf",
        currency="USD",
    )
    session.add(sec)
    await session.flush()
    return sec


class _Data:
    """Stand-in for the Pydantic schemas, so a service test does not have to build
    one of those to exercise the service.

    ``model_fields_set`` is the load-bearing part: the services use ``is_set`` to
    tell "absent" from "explicitly null", so a stub without it would silently make
    every field look absent.
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.model_fields_set = set(kw)


async def _buy(session, hh, account, security, *, qty="10", amount="-1000", on=ON):
    return await inv.create_investment_transaction(
        session,
        hh,
        _Data(
            account_id=account.id,
            security_id=security.id,
            type="buy",
            trade_date=on,
            quantity=Decimal(qty),
            price=None,
            amount=Decimal(amount),
            description=None,
            notes=None,
        ),
    )


# ---- the recompute ---------------------------------------------------------


async def test_a_price_write_lands_in_the_derived_balance(household_factory):
    """The whole reason `upsert_price` recomputes: without it the account's stored
    balance keeps describing the old price, and net worth reads the stored one."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("10"),
        )
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("100")
        )
        assert acc.current_balance == Decimal("1000.0000")

        # A correction to the same day, not a second row.
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("110")
        )
        assert acc.current_balance == Decimal("1100.0000")

        rows = (
            await s.execute(
                select(Holding).where(Holding.account_id == acc.id)
            )
        ).scalars().all()
        assert len(rows) == 1


async def test_a_price_upsert_replaces_rather_than_appending(household_factory):
    """(security, date) is unique, so "the latest price on or before D" has exactly
    one candidate. A second row for the same day would make it insertion-ordered."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("1"),
        )
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("5")
        )
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("7")
        )
        points = await inv.list_security_prices(s, security_id=sec.id)
        assert [(p.price_date, p.price) for p in points] == [(ON, Decimal("7.00000000"))]


# ---- a position from history alone (ADR-0034) ------------------------------


async def test_a_recorded_buy_is_a_position_with_no_holding_row(household_factory):
    """A buy in a security the account has never held is the position (ADR-0034).

    Requiring a hand-created row as well would be the double-write the ADR exists
    to kill, and it would fail towards a position the household owns being valued
    at nothing."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("50")
        )
        await _buy(s, hh, acc, sec, qty="10", amount="-500")

        records = await inv.list_holdings(s, account_id=acc.id)
        assert len(records) == 1
        record = records[0]
        assert record.position.quantity == Decimal("10")
        assert record.position.cost_basis == Decimal("500.0000")
        assert record.position.source == inv.HISTORY
        assert record.holding is None  # no row: the trades are the position
        assert record.position.holding_id is None

        # And it is valued, not merely listed.
        assert acc.current_balance == Decimal("500.0000")


async def test_a_buy_on_a_manual_holding_hands_the_quantity_to_history(
    household_factory,
):
    """Before: a hand-entered 10, source `manual`. After: the fold, source
    `history`. The stored scalar stays where it was — ignored, and still readable
    so the UI can show what was overridden."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("10"), cost_basis=Decimal("900"),
        )
        before = (await inv.list_holdings(s, account_id=acc.id))[0].position
        assert (before.quantity, before.source) == (Decimal("10"), inv.MANUAL)

        await _buy(s, hh, acc, sec, qty="4", amount="-400")

        after = (await inv.list_holdings(s, account_id=acc.id))[0]
        assert after.position.quantity == Decimal("4")       # the trades, not the row
        assert after.position.cost_basis == Decimal("400.0000")
        assert after.position.source == inv.HISTORY
        assert after.holding is not None                      # the row survived
        assert after.holding.quantity == Decimal("10")        # untouched, not clobbered


async def test_a_fully_sold_position_disappears(household_factory):
    """ADR-0034's sharper case: the trades say it is gone, so it is gone — rather
    than reporting phantom shares that `quantity × price` turns into real money."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("100")
        )
        await _buy(s, hh, acc, sec, qty="10", amount="-1000")
        await inv.create_investment_transaction(
            s, hh,
            _Data(
                account_id=acc.id, security_id=sec.id, type="sell", trade_date=ON,
                quantity=Decimal("-10"), price=None, amount=Decimal("1200"),
                description=None, notes=None,
            ),
        )

        assert await inv.list_holdings(s, account_id=acc.id) == []
        # And the value went with it, rather than lingering at the last price.
        assert acc.current_balance == Decimal("0.0000")


async def test_a_split_moves_the_share_count_and_not_the_basis(household_factory):
    """A split changes how many shares represent the same money. Taking a *ratio*
    instead of the delta would be the one row whose sign and scale mean something
    different, and Σ(quantity) — the sum ADR-0034 derives a position from — would
    be wrong for it."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await _buy(s, hh, acc, sec, qty="10", amount="-1000")
        await inv.create_investment_transaction(
            s, hh,
            _Data(
                account_id=acc.id, security_id=sec.id, type="split", trade_date=ON,
                quantity=Decimal("10"),  # 2-for-1: +10 shares, not "2"
                price=None, amount=Decimal("0"), description=None, notes=None,
            ),
        )
        position = (await inv.list_holdings(s, account_id=acc.id))[0].position
        assert position.quantity == Decimal("20")
        assert position.cost_basis == Decimal("1000.0000")


# ---- refusals --------------------------------------------------------------


async def test_a_manual_write_to_a_history_owned_position_is_refused(
    household_factory,
):
    """409, not a silent drop. A client that believes it stored a value and reads
    back a different one has no way to tell that from a bug."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("10"),
        )
        await _buy(s, hh, acc, sec, qty="4", amount="-400")
        holding = (
            await s.execute(select(Holding).where(Holding.account_id == acc.id))
        ).scalar_one()

        with pytest.raises(LedgerError) as exc:
            await inv.update_holding(
                s, holding.id, _Data(quantity=Decimal("99"))
            )
        assert exc.value.status == 409

        # And the same for basis on its own, which has the same two writers.
        with pytest.raises(LedgerError) as exc:
            await inv.update_holding(
                s, holding.id, _Data(cost_basis=Decimal("1234"))
            )
        assert exc.value.status == 409

        # `as_of` is not derived from anything, so it stays writable.
        await inv.update_holding(s, holding.id, _Data(as_of=ON))
        assert holding.as_of == ON


async def test_a_zero_quantity_is_refused(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        with pytest.raises(LedgerError) as exc:
            await inv.upsert_holding(
                s, household_id=hh, account_id=acc.id, security_id=sec.id,
                quantity=Decimal("0"),
            )
        assert exc.value.status == 422


async def test_a_duplicate_ticker_is_a_409_not_a_500(household_factory):
    """The partial unique index on (household, ticker, currency). "You already have
    this" is something the household can act on; a 500 is not."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await inv.create_security(
            s, hh, _Data(name="Vanguard Total", ticker="vti", security_type="etf", currency="USD")
        )
        with pytest.raises(LedgerError) as exc:
            await inv.create_security(
                s, hh,
                _Data(name="Again", ticker="VTI", security_type="etf", currency="USD"),
            )
        assert exc.value.status == 409
        # Normalised, so `vti` and `VTI` collide rather than both existing.
        assert "VTI" in exc.value.message


async def test_positions_belong_to_investment_accounts_only(household_factory):
    """ADR-0033 §1's boundary, enforced from both sides: a depository account's
    balance is its transaction sum, so a position hanging off one would be valued
    by nobody."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        cash = await _account(s, hh, name="Checking", atype="depository", source=None)
        sec = await _security(s, hh)
        with pytest.raises(LedgerError) as exc:
            await inv.upsert_holding(
                s, household_id=hh, account_id=cash.id, security_id=sec.id,
                quantity=Decimal("1"),
            )
        assert exc.value.status == 422

        with pytest.raises(LedgerError) as exc:
            await _buy(s, hh, cash, sec)
        assert exc.value.status == 422


# ---- deletes ---------------------------------------------------------------


async def test_deleting_a_trade_moves_the_position_it_belongs_to(
    household_factory,
):
    """The cost ADR-0034 names explicitly: deleting a trade can move a position's
    quantity. Correct, but it is a write path and has to be tested as one."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("10")
        )
        await _buy(s, hh, acc, sec, qty="10", amount="-100")
        second = await _buy(s, hh, acc, sec, qty="5", amount="-50")
        assert acc.current_balance == Decimal("150.0000")

        await inv.delete_investment_transaction(s, second.id)

        position = (await inv.list_holdings(s, account_id=acc.id))[0].position
        assert position.quantity == Decimal("10")
        assert position.cost_basis == Decimal("100.0000")
        assert acc.current_balance == Decimal("100.0000")


async def test_deleting_a_security_recomputes_from_both_writers(household_factory):
    """Rows cascade away with the security, but trades do not — the FK is ON DELETE
    SET NULL, so the position's quantity changes and the account still needs a
    recompute. Collecting the affected accounts after the delete would find
    neither, and collecting only `holdings` would miss the second writer."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        from_history = await _account(s, hh, name="History only")
        from_row = await _account(s, hh, name="Row only")
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("10")
        )

        # One account holds it as trades alone; the other has a row.
        await _buy(s, hh, from_history, sec, qty="3", amount="-30")
        await inv.upsert_holding(
            s, household_id=hh, account_id=from_row.id, security_id=sec.id,
            quantity=Decimal("7"),
        )
        assert from_history.current_balance == Decimal("30.0000")
        assert from_row.current_balance == Decimal("70.0000")

        await inv.delete_security(s, sec.id)

        assert from_history.current_balance == Decimal("0.0000")
        assert from_row.current_balance == Decimal("0.0000")
        # Nothing was silently left behind.
        assert (
            await s.execute(
                select(InvestmentTransaction).where(
                    InvestmentTransaction.account_id == from_history.id
                )
            )
        ).scalars().all() != []


async def test_deleting_a_holding_recomputes_the_account(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(s, hh)
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("20")
        )
        holding = await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("5"),
        )
        assert acc.current_balance == Decimal("100.0000")

        await inv.delete_holding(s, holding.id)
        assert acc.current_balance == Decimal("0.0000")


async def test_a_stated_account_is_never_recomputed(household_factory):
    """ADR-0021: a provider's balance is the authoritative number there. Deriving
    it would overwrite what the household's broker actually says with our own
    arithmetic."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acc = await _account(
            s, hh, name="Fidelity", source="stated", balance=Decimal("5000.0000")
        )
        sec = await _security(s, hh)
        await inv.upsert_price(
            s, household_id=hh, security_id=sec.id, price_date=ON, price=Decimal("20")
        )
        # The position alone would be 100; the stated balance must survive.
        await inv.upsert_holding(
            s, household_id=hh, account_id=acc.id, security_id=sec.id,
            quantity=Decimal("5"),
        )
        assert acc.current_balance == Decimal("5000.0000")

        # ... and the plug is what reconciles the two.
        valuation = await inv.value_account(s, acc, ON, BASE)
        assert valuation.unaccounted_cash_base == Decimal("4900.0000")
