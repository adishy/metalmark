"""The daily FX fetch — ARCHITECTURE §2's "rates are pulled daily", made true (ADR-0046).

Until this existed a rate was whatever a person typed, so a foreign account had no
value on any day before the first one and a months-old value after it. This pulls
daily mid-market rates from Frankfurter (ECB and other central-bank sources) into
``fx_rates`` as ``source='auto'``, for every non-base currency a household uses,
from the first day it needs one.

The rules, each for a reason:

* **Never overwrites a row.** Inserted with ``ON CONFLICT DO NOTHING`` on the
  pair's date, so a rate a person entered for a day stays that day's rate. On
  other days the more recently stated rate wins, in either direction
  (``fx._multiplier``).
* **Backfills once, then fills the edges.** A currency with no auto rate is
  fetched from the earliest day anything in the ledger is denominated in it; after
  that, the days after its latest auto rate — and the days before its first, when
  older history has since been imported.
* **One recompute per household, after the inserts** — not ``ledger.upsert_fx_rate``
  per day, which recomputes every foreign transaction each call and would make a
  multi-year backfill quadratic. What changed is counted and logged: the first
  fetch after a deploy rewrites cached ``base_amount``\\s, and that should be
  visible. Deleting the auto rows and recomputing restores the previous values.
* **One request per currency**, so a symbol the source does not know (a 422)
  costs that currency, not the household's other rates; a failed request is
  logged and retried on the next run, never raised into the scheduler.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import Date, func, select, text, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scoped_session, unscoped_session
from app.logging import get_logger
from app.models import (
    Account,
    BalanceSnapshot,
    FxRate,
    InvestmentTransaction,
    Security,
    SecurityPrice,
    Transaction,
)
from app.services.ledger import recompute_base_amounts
from app.services.ledger import today as ledger_today

log = get_logger(__name__)

#: Rows per INSERT. A ten-year daily backfill of one currency is ~3,650 rows.
INSERT_CHUNK = 1000
TIMEOUT_SECONDS = 30.0


@dataclass
class HouseholdFetch:
    """What one household's run did."""

    household_id: uuid.UUID
    base: str
    #: Rows inserted, per quote currency.
    written: dict[str, int] = field(default_factory=dict)
    #: Currencies the run could not fetch, with why.
    failed: dict[str, str] = field(default_factory=dict)


async def currencies_needed(session: AsyncSession, base: str) -> dict[str, date]:
    """Each non-base currency this household uses → the first day it needs a rate.

    Everything in the ledger that carries a currency and a day: balances,
    transactions, prices and trades — and an account's own currency, from its
    balance date, so an account with nothing else still gets today's rate.
    """
    base = base.upper()
    sources = union_all(
        select(BalanceSnapshot.currency.label("c"), BalanceSnapshot.balance_date.label("d")),
        select(Transaction.currency, Transaction.transacted_at.cast(Date)),
        select(SecurityPrice.currency, SecurityPrice.price_date),
        select(InvestmentTransaction.currency, InvestmentTransaction.trade_date),
        select(Account.currency, func.coalesce(Account.balance_date, func.current_date())),
        select(Security.currency, func.current_date()),
    ).subquery()
    rows = (
        await session.execute(
            select(func.upper(sources.c.c), func.min(sources.c.d))
            .where(func.upper(sources.c.c) != base)
            .group_by(func.upper(sources.c.c))
        )
    ).all()
    return {ccy: first for ccy, first in rows if ccy}


async def _auto_span(session: AsyncSession, base: str, quote: str) -> tuple[date, date] | None:
    """The first and last day this pair has an auto rate for, if any."""
    first, last = (
        await session.execute(
            select(func.min(FxRate.rate_date), func.max(FxRate.rate_date)).where(
                FxRate.base_currency == base,
                FxRate.quote_currency == quote,
                FxRate.source == "auto",
            )
        )
    ).one()
    return None if first is None else (first, last)


def _missing_ranges(
    first_needed: date, span: tuple[date, date] | None, today: date
) -> list[tuple[date, date]]:
    """The day ranges to fetch: everything, the first time; after that, whatever
    lies outside what is held — later days, and earlier ones when older history
    (an import) has moved the first day needed back."""
    if span is None:
        return [(first_needed, today)] if first_needed <= today else []
    ranges = []
    if first_needed < span[0]:
        ranges.append((first_needed, span[0] - timedelta(days=1)))
    if span[1] < today:
        ranges.append((span[1] + timedelta(days=1), today))
    return ranges


async def fetch_series(
    client: httpx.AsyncClient, url: str, *, base: str, quote: str, start: date, end: date
) -> list[tuple[date, Decimal]]:
    """``(day, rate)`` for ``1 base = rate quote`` over ``[start, end]``.

    Parsed with ``Decimal`` for every number (ADR-0005): ``response.json()`` would
    read the rates as floats first.
    """
    response = await client.get(
        url,
        params={"from": start.isoformat(), "to": end.isoformat(), "base": base, "quotes": quote},
    )
    response.raise_for_status()
    body = json.loads(response.text, parse_float=Decimal, parse_int=Decimal)
    if not isinstance(body, list):
        raise ValueError(f"unexpected response shape: {type(body).__name__}")
    out: list[tuple[date, Decimal]] = []
    for item in body:
        if item.get("base") != base or item.get("quote") != quote:
            continue
        rate = item.get("rate")
        if not isinstance(rate, Decimal) or rate <= 0:
            continue
        out.append((date.fromisoformat(item["date"]), rate))
    return out


async def _insert(session: AsyncSession, base: str, quote: str,
                  series: Iterable[tuple[date, Decimal]]) -> int:
    rows = [
        {"base_currency": base, "quote_currency": quote, "rate_date": day, "rate": rate,
         "source": "auto"}
        for day, rate in series
    ]
    written = 0
    for i in range(0, len(rows), INSERT_CHUNK):
        result = await session.execute(
            pg_insert(FxRate)
            .values(rows[i : i + INSERT_CHUNK])
            .on_conflict_do_nothing(
                index_elements=["base_currency", "quote_currency", "rate_date"]
            )
        )
        written += result.rowcount or 0
    await session.flush()
    return written


async def refresh_household(
    session: AsyncSession,
    household_id: uuid.UUID,
    base: str,
    client: httpx.AsyncClient,
    *,
    url: str,
    today: date | None = None,
) -> HouseholdFetch:
    """Fetch every rate this household is missing. Recomputing is the caller's."""
    today = today or ledger_today()
    base = base.upper()
    result = HouseholdFetch(household_id=household_id, base=base)
    for quote, first in sorted((await currencies_needed(session, base)).items()):
        span = await _auto_span(session, base, quote)
        for start, end in _missing_ranges(first, span, today):
            try:
                series = await fetch_series(
                    client, url, base=base, quote=quote, start=start, end=end
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                result.failed[quote] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning(
                    "fx.fetch_failed", base=base, quote=quote, error=result.failed[quote]
                )
                break
            written = await _insert(session, base, quote, series)
            if written:
                result.written[quote] = result.written.get(quote, 0) + written
    return result


async def _recompute_counting(session: AsyncSession, household_id: uuid.UUID) -> int:
    """Recompute this household's cached base amounts; how many actually changed."""
    before = dict(
        (await session.execute(select(Transaction.id, Transaction.base_amount))).all()
    )
    await recompute_base_amounts(session, household_id)
    after = dict(
        (await session.execute(select(Transaction.id, Transaction.base_amount))).all()
    )
    return sum(1 for tid, amount in after.items() if before.get(tid) != amount)


async def recompute_all() -> dict[uuid.UUID, int]:
    """Recompute every household's cached base amounts; how many changed in each.

    Run once when the worker starts, whether or not the fetch is on. The cache is
    a function of the rate table *and* of the rule that reads it, and ADR-0046
    changed the rule: a pair stored both ways round can now convert at a
    different rate than when its amounts were cached. Idempotent — on a database
    whose caches already agree, it changes nothing.
    """
    async with unscoped_session() as session:
        ids = [r[0] for r in (await session.execute(text("SELECT id FROM households"))).all()]
    changed: dict[uuid.UUID, int] = {}
    for household_id in ids:
        async with scoped_session(household_id) as session:
            changed[household_id] = await _recompute_counting(session, household_id)
        if changed[household_id]:
            log.info("fx.recomputed", household_id=str(household_id),
                     base_amounts_changed=changed[household_id])
    return changed


async def refresh_all(
    *, url: str, transport: httpx.AsyncBaseTransport | None = None, today: date | None = None
) -> list[HouseholdFetch]:
    """The worker's daily job: every household, then every recompute it needs.

    ``fx_rates`` is shared by every household (ARCHITECTURE §2), so a rate one
    household's run writes can change another's conversions. Recomputing is
    therefore a second pass over every household whose currencies were written by
    anyone, not only by its own run.
    """
    async with unscoped_session() as session:
        households = (
            await session.execute(text("SELECT id, base_currency FROM households"))
        ).all()

    results: list[HouseholdFetch] = []
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport) as client:
        for household_id, base in households:
            async with scoped_session(household_id) as session:
                results.append(
                    await refresh_household(
                        session, household_id, base, client, url=url, today=today
                    )
                )

    written_pairs = {(r.base, q) for r in results for q in r.written}
    for r in results:
        changed = 0
        async with scoped_session(r.household_id) as session:
            needed = await currencies_needed(session, r.base)
            if any((r.base, q) in written_pairs for q in needed):
                changed = await _recompute_counting(session, r.household_id)
        log.info(
            "fx.refreshed",
            household_id=str(r.household_id),
            base=r.base,
            rates_written=r.written,
            base_amounts_changed=changed,
            failed=r.failed,
        )
    return results
