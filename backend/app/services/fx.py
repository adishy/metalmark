"""FX conversion — the ONE dated-conversion service everyone calls (WS-FX).

No ad-hoc conversion anywhere else. Rules (ARCHITECTURE §2, ADR-0017):
  * ``fx_rates`` is the single source of truth; a row means
    ``1 base_currency = rate quote_currency`` (NUMERIC(19,8)).
  * Convert at the rate for the **latest rate_date ≤ target date**.
  * If no rate is resolvable, return ``None`` — a "no rate" flag, never 0.
  * Cross pairs triangulate through the household base currency, in one place
    with controlled rounding.

``transactions.base_amount`` is a cache computed from this service; it is
recomputed whenever a rate it depends on changes (invalidation lives with the
rate-write path).

Two forms, one rule: ``to_base`` converts one amount and queries per lookup, and
``converter()`` builds a ``Converter`` that holds the rates already — use the
latter for anything that converts in a loop. Both go through ``_multiplier``, so
the resolution order (identity → the more recent of direct and inverse → triangulate) has one
definition.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Awaitable, Callable, Iterable
from datetime import date
from decimal import Decimal
from functools import partial

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import convert
from app.models import FxRate

# One directed pair's latest ``(rate_date, rate)`` on or before a date, or ``None``
# for "no rate on or before it". A *source* rather than a function so that the
# resolution order below can be written once and asked of either the database or
# a `Converter`'s loaded table. The date comes back with the rate because the
# order compares the two directions by it (ADR-0046).
RateLookup = Callable[[str, str, date], Awaitable[tuple[date, Decimal] | None]]


async def _latest_rate(session: AsyncSession, base: str, quote: str,
                       on: date) -> tuple[date, Decimal] | None:
    row = (
        await session.execute(
            select(FxRate.rate_date, FxRate.rate)
            .where(
                FxRate.base_currency == base,
                FxRate.quote_currency == quote,
                FxRate.rate_date <= on,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def _multiplier(
    rates: RateLookup, from_ccy: str, to_ccy: str, on: date, base_ccy: str
) -> Decimal | None:
    """Return M such that ``amount_to = amount_from * M`` for ``on`` (latest ≤).

    Resolution order: identity → the more recently stated of direct and inverse
    → triangulate through ``base_ccy``. Returns ``None`` if no rate chain is
    available ("no rate").

    ``async`` although half its callers have the rates in memory already: the
    order *is* the rule, it has exactly one definition, and one of its two
    sources is a database. A second copy of this for the in-memory case is the
    thing that would drift.
    """
    from_ccy, to_ccy, base_ccy = from_ccy.upper(), to_ccy.upper(), base_ccy.upper()
    if from_ccy == to_ccy:
        return Decimal(1)

    # direct: 1 from = M to; inverse: 1 to = r from  ->  1 from = 1/r to.
    #
    # **Whichever was stated more recently** (ADR-0046), not "direct if any". A
    # pair can be written either way round — a person types EUR→USD, the daily
    # fetch writes USD→EUR — and preferring direct whenever it existed at all let a
    # year-old typed rate shadow every fresher rate the other way round. On the
    # same date direct wins, which is the order this always used.
    direct = await rates(from_ccy, to_ccy, on)
    inverse = await rates(to_ccy, from_ccy, on)
    if inverse is not None and inverse[1] == 0:
        inverse = None
    if direct is not None and (inverse is None or direct[0] >= inverse[0]):
        return direct[1]
    if inverse is not None:
        return Decimal(1) / inverse[1]

    # triangulate through base: from->base * base->to
    if base_ccy not in (from_ccy, to_ccy):
        from_to_base = await _multiplier(rates, from_ccy, base_ccy, on, base_ccy)
        base_to_to = await _multiplier(rates, base_ccy, to_ccy, on, base_ccy)
        if from_to_base is not None and base_to_to is not None:
            return from_to_base * base_to_to
    return None


class Converter:
    """A dated-conversion source with the rates already in hand.

    ``to_base`` is the right shape for one amount and the wrong shape for a
    report. It is up to four queries per amount, and a net-worth series asks about
    the same few currencies at every point of the series and for every account —
    a 15-year monthly series over 24 accounts cost 7,108 queries and 1.4 s of
    round trips to read a table the household already holds in full. This answers
    from memory instead, at ~1 query to build.

    **Built for one request and then discarded, never cached.** A rate edited
    while a chart is being drawn must not make two points of that chart disagree
    about what a currency is worth; a Converter that outlived the request would be
    a second source of truth for exactly that, and a stale second source at that.

    Same contracts as the module functions, deliberately: ``latest`` is
    ``_latest_rate`` applied to rows in memory (latest ``rate_date`` ≤ ``on``, and
    ``None`` — never zero — when there is none), and ``to_base`` returns the same
    ``(base_amount, fx_rate_date)`` pair through the same ``convert``.

    ``to_base`` converts *into* the base, so the order's triangulation branch
    cannot fire for it — one hop, direct or inverted. That is the whole surface
    this class is asked about today, and the reason ``converter`` only needs the
    currencies a caller will convert **from**.
    """

    def __init__(self, rates: dict[tuple[str, str], list[tuple[date, Decimal]]]) -> None:
        self._rates = rates

    async def latest(self, base: str, quote: str, on: date) -> tuple[date, Decimal] | None:
        rows = self._rates.get((base.upper(), quote.upper()))
        if not rows:
            return None
        i = bisect_right(rows, on, key=lambda r: r[0]) - 1
        return rows[i] if i >= 0 else None

    async def to_base(
        self, *, amount: Decimal, currency: str, on: date, base_ccy: str
    ) -> tuple[Decimal | None, date | None]:
        if currency.upper() == base_ccy.upper():
            return amount, on
        m = await _multiplier(self.latest, currency, base_ccy, on, base_ccy)
        if m is None:
            return None, None
        return convert(amount, m), on


async def converter(
    session: AsyncSession, *, base_ccy: str, currencies: Iterable[str], until: date
) -> Converter:
    """Build a ``Converter`` for the currencies a report will convert **from**.

    ``currencies`` sizes the load and nothing else — pass the set a caller will
    actually convert from and it reads the fewest rows. Every pair touching a
    named currency is kept, so the filter is an over-approximation, and passing too
    many is only a bigger read. **Passing too few cannot be wrong**: ``to_base``
    only ever asks about ``(from, base_ccy)``, and ``base_ccy`` is always in the
    set, so no row an answer depends on can be filtered out. ``None`` therefore
    still means "no rate exists", never "you did not ask for it".

    ``until`` is the last date that can be asked about. A rate dated after the last
    point of a chart cannot inform it, and a household with a long history has
    years of them. It is not a soft preference: a report that asks past its own
    bound is answered from the older rates it did load, which is that report's bug
    and the reason the parameter is named for the dates rather than for a limit.
    """
    ccy = {base_ccy.upper()} | {c.upper() for c in currencies}
    rows = (
        await session.execute(
            select(
                FxRate.base_currency, FxRate.quote_currency, FxRate.rate_date, FxRate.rate
            )
            .where(
                FxRate.rate_date <= until,
                or_(FxRate.base_currency.in_(ccy), FxRate.quote_currency.in_(ccy)),
            )
            .order_by(FxRate.base_currency, FxRate.quote_currency, FxRate.rate_date)
        )
    ).all()
    table: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
    for base, quote, rate_date, rate in rows:
        table.setdefault((base, quote), []).append((rate_date, rate))
    return Converter(table)


async def get_multiplier(
    session: AsyncSession, *, from_ccy: str, to_ccy: str, on: date, base_ccy: str
) -> Decimal | None:
    """``_multiplier`` against the database — the one-shot form.

    Build a ``Converter`` instead when converting more than a couple of amounts;
    this pays a query per lookup, which is right for one amount and wrong for a
    series.
    """
    return await _multiplier(
        partial(_latest_rate, session), from_ccy, to_ccy, on, base_ccy
    )


async def to_base(
    session: AsyncSession, *, amount: Decimal, currency: str, on: date, base_ccy: str
) -> tuple[Decimal | None, date | None]:
    """Convert a native amount to the household base currency.

    Returns ``(base_amount, fx_rate_date)``; ``(None, None)`` when no rate
    exists (the caller stores NULL base_amount and the UI flags "no rate").
    """
    if currency.upper() == base_ccy.upper():
        return amount, on
    m = await get_multiplier(
        session, from_ccy=currency, to_ccy=base_ccy, on=on, base_ccy=base_ccy
    )
    if m is None:
        return None, None
    return convert(amount, m), on
