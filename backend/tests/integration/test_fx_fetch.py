"""The daily FX fetch (ADR-0046), against a stubbed Frankfurter — never the network.

Rates here are in RON and ISK, which no other test uses: ``fx_rates`` is global
(ARCHITECTURE §2), so a rate written here is visible to every later test. For the
same reason the household-level functions are driven directly; ``refresh_all``
walks every household the session has created.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import scoped_session
from app.models import FxRate, Transaction
from app.schemas.ledger import AccountCreate
from app.services import fx, fx_fetch, ledger
from app.settings import Settings

pytestmark = pytest.mark.integration

D = Decimal
URL = "https://fx.test/v2/rates"
TODAY = date(2026, 3, 10)


def _source(requests: list[dict], rates: dict[str, Decimal]):
    """A Frankfurter v2 stand-in: one row per day in the range, per known quote.
    An unknown quote is a 422, as the real service answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        requests.append(params)
        quote = params["quotes"]
        if quote not in rates:
            return httpx.Response(
                422, json={"status": 422, "message": f"invalid currency: {quote}"}
            )
        day = date.fromisoformat(params["from"])
        end = date.fromisoformat(params["to"])
        rows = []
        while day <= end:
            rows.append({"date": day.isoformat(), "base": params["base"], "quote": quote,
                         "rate": float(rates[quote])})
            day += timedelta(days=1)
        return httpx.Response(200, text=json.dumps(rows))

    return httpx.MockTransport(handler)


async def _ron_household(household_factory, *, txn_day=date(2026, 3, 1)):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        acct = await ledger.create_account(s, hid, AccountCreate(
            name="Lei", type="depository", currency="RON",
            current_balance=D("5000"), balance_date=date(2026, 3, 5)))
        at = datetime(txn_day.year, txn_day.month, txn_day.day, 12, tzinfo=UTC)
        s.add(Transaction(household_id=hid, account_id=acct.id, transacted_at=at, posted_at=at,
                          amount=D("-500"), currency="RON", description="x", source="manual",
                          import_hash=f"ron{txn_day}"))
    return hid


async def _run(hid, requests, rates, today=TODAY):
    async with (
        httpx.AsyncClient(transport=_source(requests, rates)) as client,
        scoped_session(hid) as s,
    ):
        result = await fx_fetch.refresh_household(s, hid, "USD", client, url=URL, today=today)
        changed = await fx_fetch._recompute_counting(s, hid)
    return result, changed


async def _base_amount(hid) -> Decimal | None:
    async with scoped_session(hid) as s:
        return (await s.execute(select(Transaction.base_amount))).scalar_one()


@pytest.fixture(autouse=True)
async def _clean_rates():
    """Leave no RON/ISK rate behind for the next test."""
    yield
    from app.db import unscoped_session
    async with unscoped_session() as s:
        await s.execute(delete(FxRate).where(FxRate.quote_currency.in_(["RON", "ISK"])))
        await s.execute(delete(FxRate).where(FxRate.base_currency.in_(["RON", "ISK"])))


async def test_the_first_run_backfills_from_the_first_day_needed(household_factory):
    hid = await _ron_household(household_factory)
    requests: list[dict] = []
    result, changed = await _run(hid, requests, {"RON": D("4.5")})
    assert requests == [{"from": "2026-03-01", "to": "2026-03-10", "base": "USD", "quotes": "RON"}]
    assert result.written == {"RON": 10}
    # The transaction had no rate before; it now converts at 1 USD = 4.5 RON.
    assert changed == 1
    assert await _base_amount(hid) == D("-111.1111")


async def test_a_later_run_fetches_only_what_is_missing(household_factory):
    hid = await _ron_household(household_factory)
    await _run(hid, [], {"RON": D("4.5")})
    requests: list[dict] = []
    result, _ = await _run(hid, requests, {"RON": D("4.5")}, today=TODAY + timedelta(days=2))
    assert [(r["from"], r["to"]) for r in requests] == [("2026-03-11", "2026-03-12")]
    assert result.written == {"RON": 2}
    # Nothing missing: nothing asked.
    requests.clear()
    await _run(hid, requests, {"RON": D("4.5")}, today=TODAY + timedelta(days=2))
    assert requests == []


async def test_older_history_is_backfilled_too(household_factory):
    hid = await _ron_household(household_factory)
    await _run(hid, [], {"RON": D("4.5")})
    async with scoped_session(hid) as s:
        acct_id = (await s.execute(select(Transaction.account_id))).scalars().first()
        at = datetime(2026, 2, 20, 12, tzinfo=UTC)
        s.add(Transaction(household_id=hid, account_id=acct_id, transacted_at=at, posted_at=at,
                          amount=D("-1"), currency="RON", description="old", source="manual",
                          import_hash="old"))
    requests: list[dict] = []
    await _run(hid, requests, {"RON": D("4.5")})
    assert [(r["from"], r["to"]) for r in requests] == [("2026-02-20", "2026-02-28")]


async def test_a_rate_a_person_entered_is_never_overwritten(household_factory):
    hid = await _ron_household(household_factory)
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="USD", quote_ccy="RON",
                                    rate_date=date(2026, 3, 3), rate=D("5"))
    result, _ = await _run(hid, [], {"RON": D("4.5")})
    assert result.written == {"RON": 9}
    async with scoped_session(hid) as s:
        kept = (await s.execute(select(FxRate.rate, FxRate.source).where(
            FxRate.quote_currency == "RON", FxRate.rate_date == date(2026, 3, 3)))).one()
    assert (kept.rate, kept.source) == (D("5.00000000"), "manual")


async def test_an_unknown_currency_costs_only_itself(household_factory):
    hid = await _ron_household(household_factory)
    async with scoped_session(hid) as s:
        await ledger.create_account(s, hid, AccountCreate(
            name="Krona", type="depository", currency="ISK",
            current_balance=D("1"), balance_date=date(2026, 3, 5)))
    result, _ = await _run(hid, [], {"RON": D("4.5")})
    assert result.written == {"RON": 10}
    assert set(result.failed) == {"ISK"} and "422" in result.failed["ISK"]


async def test_removing_the_fetched_rates_restores_the_cached_amounts(household_factory):
    """ADR-0046's way back: the fetch only adds `auto` rows, so deleting them and
    recomputing returns every cached amount to what it was."""
    hid = await _ron_household(household_factory)
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="RON", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("0.25"))
    before = await _base_amount(hid)
    await _run(hid, [], {"RON": D("4.5")})
    assert await _base_amount(hid) != before
    async with scoped_session(hid) as s:
        await s.execute(delete(FxRate).where(FxRate.source == "auto",
                                             FxRate.quote_currency == "RON"))
        await ledger.recompute_base_amounts(s, hid)
    assert await _base_amount(hid) == before


async def test_a_fresher_rate_the_other_way_round_wins(household_factory):
    """A typed RON→USD rate from January must not shadow March's USD→RON rates."""
    hid = await household_factory()
    async with scoped_session(hid) as s:
        await ledger.upsert_fx_rate(s, hid, base_ccy="RON", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("0.25"))
        await ledger.upsert_fx_rate(s, hid, base_ccy="USD", quote_ccy="RON",
                                    rate_date=date(2026, 3, 1), rate=D("5"), source="auto")
        in_feb = await fx.get_multiplier(s, from_ccy="RON", to_ccy="USD",
                                         on=date(2026, 2, 1), base_ccy="USD")
        in_mar = await fx.get_multiplier(s, from_ccy="RON", to_ccy="USD",
                                         on=date(2026, 3, 2), base_ccy="USD")
    assert in_feb == D("0.25")
    assert in_mar == D("0.2")


def test_the_fetch_is_off_outside_prod_unless_asked():
    assert Settings(METALMARK_ENV="dev").fx_fetch_enabled is False
    assert Settings(METALMARK_ENV="test").fx_fetch_enabled is False
    assert Settings(METALMARK_ENV="prod").fx_fetch_enabled is True
    assert Settings(METALMARK_ENV="prod", METALMARK_FX_FETCH="false").fx_fetch_enabled is False
    assert Settings(METALMARK_ENV="dev", METALMARK_FX_FETCH="true").fx_fetch_enabled is True


async def test_the_daily_job_walks_every_household(household_factory):
    """The worker's entry point, wired end to end — against a source with nothing
    to give, so it writes no rate the rest of the session could see."""
    hid = await _ron_household(household_factory)
    seen: list[str] = []

    def empty(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["quotes"])
        return httpx.Response(200, text="[]")

    results = await fx_fetch.refresh_all(url=URL, transport=httpx.MockTransport(empty),
                                         today=TODAY)
    ours = next(r for r in results if r.household_id == hid)
    assert (ours.written, ours.failed) == ({}, {})
    assert "RON" in seen
