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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import convert
from app.models import FxRate


async def _latest_rate(session: AsyncSession, base: str, quote: str,
                       on: date) -> Decimal | None:
    row = (
        await session.execute(
            select(FxRate.rate)
            .where(
                FxRate.base_currency == base,
                FxRate.quote_currency == quote,
                FxRate.rate_date <= on,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def get_multiplier(
    session: AsyncSession, *, from_ccy: str, to_ccy: str, on: date, base_ccy: str
) -> Decimal | None:
    """Return M such that ``amount_to = amount_from * M`` for ``on`` (latest ≤).

    Resolution order: identity → direct → inverse → triangulate through
    ``base_ccy``. Returns ``None`` if no rate chain is available ("no rate").
    """
    from_ccy, to_ccy, base_ccy = from_ccy.upper(), to_ccy.upper(), base_ccy.upper()
    if from_ccy == to_ccy:
        return Decimal(1)

    # direct: 1 from = M to
    direct = await _latest_rate(session, from_ccy, to_ccy, on)
    if direct is not None:
        return direct

    # inverse: 1 to = r from  ->  1 from = 1/r to
    inverse = await _latest_rate(session, to_ccy, from_ccy, on)
    if inverse is not None and inverse != 0:
        return Decimal(1) / inverse

    # triangulate through base: from->base * base->to
    if base_ccy not in (from_ccy, to_ccy):
        from_to_base = await get_multiplier(
            session, from_ccy=from_ccy, to_ccy=base_ccy, on=on, base_ccy=base_ccy
        )
        base_to_to = await get_multiplier(
            session, from_ccy=base_ccy, to_ccy=to_ccy, on=on, base_ccy=base_ccy
        )
        if from_to_base is not None and base_to_to is not None:
            return from_to_base * base_to_to
    return None


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
