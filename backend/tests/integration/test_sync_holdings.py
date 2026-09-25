"""Sync lands the bank's holdings as positions (ADR-0051).

The capture's "SimpleFIN Savings" holds 550 AAPL. The properties protected here
are the ones a user would notice going wrong: the position appears, a re-sync
changes nothing, the bank's changes are mirrored, and a position a human entered
is never touched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import Account, AccountConnection, SyncRunEvent
from app.models.investments import Holding, Security, SecurityPrice
from app.schemas.investments import HoldingUpdate
from app.security.crypto import SecretBox
from app.services import investments as inv
from app.services import sync
from app.services.aggregator import ProviderHolding
from app.services.errors import LedgerError
from app.services.fake_simplefin import FAKE_ACCESS_URL, FakeProvider
from app.settings import get_settings
from tests.fakes import simplefin as scenarios

pytestmark = pytest.mark.integration

D = Decimal
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
#: The capture's balance date for every account.
DAY = date(2026, 9, 21)
SAVINGS = scenarios.DEMO_SAVINGS


@pytest.fixture
async def hh(household_factory):
    return await household_factory()


async def _connection(household_id) -> uuid.UUID:
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id,
            provider="fake",
            access_url_encrypted=SecretBox(get_settings().secret_key).encrypt(FAKE_ACCESS_URL),
            org_name="SimpleFIN Bridge",
        )
        session.add(connection)
        await session.flush()
        return connection.id


async def _sync(household_id, connection_id, *fetches):
    provider = FakeProvider(script=scenarios.scenario(*fetches))
    outcomes = []
    for _ in fetches:
        outcomes.append(
            await sync.run_connection_sync(
                household_id, connection_id, provider=provider, now=NOW
            )
        )
    return outcomes


async def _savings(session) -> Account:
    return (
        await session.execute(select(Account).where(Account.name == SAVINGS))
    ).scalar_one()


async def _positions(session, account_id) -> dict[str, Holding]:
    rows = (
        await session.execute(
            select(Holding, Security)
            .join(Security, Security.id == Holding.security_id)
            .where(Holding.account_id == account_id)
        )
    ).all()
    return {(s.ticker or s.name): h for h, s in rows}


def _holding(symbol, shares, value, *, basis=None, description=None) -> ProviderHolding:
    return ProviderHolding(
        market_value=D(value),
        currency="USD",
        symbol=symbol,
        description=description,
        shares=None if shares is None else D(shares),
        cost_basis=None if basis is None else D(basis),
    )


def _holdings(*lines: ProviderHolding):
    return scenarios.with_holdings(scenarios.demo(), SAVINGS, list(lines))


async def test_a_first_sync_lands_the_banks_holding_as_a_position(hh) -> None:
    connection_id = await _connection(hh)
    [outcome] = await _sync(hh, connection_id, scenarios.demo())

    assert outcome.counts.holdings_seen == 1
    assert outcome.counts.holdings_written == 1
    async with scoped_session(hh) as session:
        savings = await _savings(session)
        assert savings.balance_source == "stated"
        positions = await _positions(session, savings.id)
        assert set(positions) == {"AAPL"}
        aapl = positions["AAPL"]
        assert aapl.quantity == D(550)
        assert aapl.cost_basis == D("55.00")
        assert aapl.source == "simplefin"
        assert aapl.as_of == DAY
        security = await session.get(Security, aapl.security_id)
        assert security.is_manual is False
        assert security.name == "Shares of Apple"
        price = (
            await session.execute(
                select(SecurityPrice).where(SecurityPrice.security_id == security.id)
            )
        ).scalar_one()
        assert price.price_date == DAY
        assert price.source == "auto"
        assert price.price * 550 == pytest.approx(D("105884.8"), abs=D("0.0001"))

        # The balance is still the bank's; the position explains most of it and
        # the rest is the remainder the account view calls unaccounted cash.
        valuation = await inv.value_account(session, savings, DAY, "USD")
        assert valuation.stated_balance_account == D("114685.51")
        assert valuation.market_value_account == pytest.approx(D("105884.8"), abs=D("0.01"))
        assert valuation.unaccounted_cash_base == pytest.approx(D("8800.71"), abs=D("0.01"))

        events = (
            await session.execute(
                select(SyncRunEvent).where(
                    SyncRunEvent.sync_run_id == outcome.run_id,
                    SyncRunEvent.event == "holdings.synced",
                )
            )
        ).scalars().all()
        assert [e.detail["written"] for e in events] == [1]


async def test_a_re_sync_writes_the_same_position_and_no_second_price(hh) -> None:
    connection_id = await _connection(hh)
    await _sync(hh, connection_id, scenarios.demo(), scenarios.demo())

    async with scoped_session(hh) as session:
        savings = await _savings(session)
        assert list(await _positions(session, savings.id)) == ["AAPL"]
        assert len((await session.execute(select(Security))).scalars().all()) == 1
        assert len((await session.execute(select(SecurityPrice))).scalars().all()) == 1


async def test_lots_are_summed_and_a_cash_line_is_a_cash_position(hh) -> None:
    connection_id = await _connection(hh)
    await _sync(
        hh,
        connection_id,
        _holdings(
            _holding("VTI", "10", "2500", basis="2000"),
            _holding("VTI", "5", "1250", basis="1100"),
            _holding(None, None, "300.25", description="Cash"),
            _holding("USD", None, "100"),
        ),
    )

    async with scoped_session(hh) as session:
        savings = await _savings(session)
        positions = await _positions(session, savings.id)
        assert set(positions) == {"VTI", "USD cash"}
        assert positions["VTI"].quantity == D(15)
        assert positions["VTI"].cost_basis == D(3100)
        assert positions["USD cash"].quantity == D("400.25")
        cash = await session.get(Security, positions["USD cash"].security_id)
        assert cash.security_type == "cash"
        valuation = await inv.value_account(session, savings, DAY, "USD")
        assert valuation.market_value_account == D("4150.25")


async def test_the_bank_dropping_a_position_removes_it_and_leaves_hand_entered_ones(
    hh,
) -> None:
    connection_id = await _connection(hh)
    await _sync(
        hh, connection_id, _holdings(_holding("VTI", "10", "2500"), _holding("AAPL", "1", "200"))
    )

    async with scoped_session(hh) as session:
        savings = await _savings(session)
        gold = Security(household_id=hh, name="Gold bar", security_type="other",
                        currency="USD")
        session.add(gold)
        await session.flush()
        await inv.upsert_holding(session, household_id=hh, account_id=savings.id,
                                 security_id=gold.id, quantity=D(1))

    [outcome] = await _sync(hh, connection_id, _holdings(_holding("VTI", "12", "3000")))

    assert outcome.counts.holdings_removed == 1
    async with scoped_session(hh) as session:
        savings = await _savings(session)
        positions = await _positions(session, savings.id)
        assert set(positions) == {"VTI", "Gold bar"}
        assert positions["VTI"].quantity == D(12)
        assert positions["Gold bar"].source == "manual"


async def test_a_hand_entered_position_in_the_same_security_is_not_overwritten(hh) -> None:
    connection_id = await _connection(hh)
    await _sync(hh, connection_id, _holdings(_holding("VTI", "1", "100")))

    async with scoped_session(hh) as session:
        savings = await _savings(session)
        aapl = Security(household_id=hh, name="Apple", ticker="AAPL", security_type="stock",
                        currency="USD")
        session.add(aapl)
        await session.flush()
        await inv.upsert_holding(session, household_id=hh, account_id=savings.id,
                                 security_id=aapl.id, quantity=D(3))
        await inv.upsert_price(session, household_id=hh, security_id=aapl.id,
                               price_date=DAY, price=D(150))
        aapl_id = aapl.id

    [outcome] = await _sync(hh, connection_id, scenarios.demo())

    assert outcome.counts.holdings_seen == 1
    assert outcome.counts.holdings_written == 0
    assert outcome.counts.holdings_removed == 1  # VTI, which the bank stopped reporting
    async with scoped_session(hh) as session:
        savings = await _savings(session)
        positions = await _positions(session, savings.id)
        assert set(positions) == {"AAPL"}
        assert positions["AAPL"].quantity == D(3)
        assert positions["AAPL"].source == "manual"
        prices = (
            await session.execute(
                select(SecurityPrice).where(SecurityPrice.security_id == aapl_id)
            )
        ).scalars().all()
        assert [(p.price, p.source) for p in prices] == [(D(150), "manual")]
        event = (
            await session.execute(
                select(SyncRunEvent).where(
                    SyncRunEvent.sync_run_id == outcome.run_id,
                    SyncRunEvent.event == "holdings.synced",
                )
            )
        ).scalar_one()
        assert event.level == "warning"
        assert event.detail["skipped"] == {"hand_entered": 1}


async def test_a_shared_security_takes_the_banks_price_but_not_over_a_hand_entered_one(
    hh,
) -> None:
    """The bank's price is newer information about a security a human also holds
    elsewhere, so it is written — except on a day a human priced it themselves."""
    connection_id = await _connection(hh)
    await _sync(hh, connection_id, _holdings(_holding("VTI", "10", "2500")))
    async with scoped_session(hh) as session:
        vti = (await session.execute(select(Security))).scalar_one()
        await inv.upsert_price(session, household_id=hh, security_id=vti.id,
                               price_date=DAY, price=D(240))

    await _sync(hh, connection_id, _holdings(_holding("VTI", "10", "2600")))

    async with scoped_session(hh) as session:
        price = (await session.execute(select(SecurityPrice))).scalar_one()
        assert (price.price, price.source) == (D(240), "manual")


async def test_a_line_without_a_share_count_is_left_to_the_remainder(hh) -> None:
    connection_id = await _connection(hh)
    [outcome] = await _sync(
        hh, connection_id, _holdings(_holding("VTI", None, "2500"), _holding("AAPL", "1", "200"))
    )

    assert (outcome.counts.holdings_seen, outcome.counts.holdings_written) == (2, 1)
    async with scoped_session(hh) as session:
        savings = await _savings(session)
        assert set(await _positions(session, savings.id)) == {"AAPL"}
        valuation = await inv.value_account(session, savings, DAY, "USD")
        assert valuation.unaccounted_cash_base == D("114485.51")


async def test_a_synced_position_cannot_be_edited_by_hand_and_reads_as_provider(hh) -> None:
    connection_id = await _connection(hh)
    await _sync(hh, connection_id, scenarios.demo())

    async with scoped_session(hh) as session:
        savings = await _savings(session)
        [record] = await inv.list_holdings(session, account_id=savings.id)
        assert record.position.source == inv.PROVIDER
        with pytest.raises(LedgerError) as exc:
            await inv.update_holding(
                session, record.holding.id, HoldingUpdate(quantity=D(1))
            )
        assert exc.value.status == 409


async def test_an_account_retyped_by_hand_takes_no_positions(hh) -> None:
    connection_id = await _connection(hh)
    await _sync(hh, connection_id, scenarios.demo())
    async with scoped_session(hh) as session:
        savings = await _savings(session)
        savings.type = "depository"
        for held in (await _positions(session, savings.id)).values():
            await session.delete(held)

    [outcome] = await _sync(hh, connection_id, scenarios.demo())

    assert (outcome.counts.holdings_seen, outcome.counts.holdings_written) == (1, 0)
    async with scoped_session(hh) as session:
        assert (await session.execute(select(Holding))).scalars().all() == []
