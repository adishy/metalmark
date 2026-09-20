"""Investments: securities, prices, positions, trades, and the two derived
views (ADR-0011/0032/0033/0034).

Reads are open to any household member; writes are owner-only, matching
``/owners`` — a security is household-wide, so one member defining instruments is
a decision that changes every other member's allocation view.

The valuation endpoints are reads that happen to be expensive, not reports: they
recompute from the price series on every call rather than reading a stored total,
for the reason in the service's module docstring.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Response

from app.deps import RequestContext, get_context, require_owner
from app.schemas.investments import (
    ALLOCATION_GROUPS,
    AllocationOut,
    HoldingCreate,
    HoldingOut,
    HoldingUpdate,
    InvestmentTransactionCreate,
    InvestmentTransactionOut,
    InvestmentTransactionUpdate,
    PortfolioOut,
    PriceOut,
    PriceUpsert,
    SecurityCreate,
    SecurityOut,
    SecurityUpdate,
)
from app.services import investments as svc
from app.services import ledger as ledger_svc
from app.services.errors import LedgerError

router = APIRouter(prefix="/investments", tags=["investments"])

_GROUP_PATTERN = "^(" + "|".join(ALLOCATION_GROUPS) + ")$"


def _today() -> date:
    # UTC, matching the ledger's own default. A "today" that depends on the
    # server's local zone would make the same request value differently on two
    # machines, and a snapshot written from it could land on the wrong day.
    return datetime.now(UTC).date()


def _holding_out(record: svc.HoldingRecord) -> HoldingOut:
    position = record.position
    return HoldingOut(
        # Null for a position that exists only as recorded trades — a real position
        # with no row behind it. See `positions_for`.
        id=position.holding_id,
        account_id=position.account_id,
        security_id=position.security_id,
        security=SecurityOut.model_validate(record.security),
        quantity=position.quantity,
        cost_basis=position.cost_basis,
        quantity_source=position.source,
        basis_source=position.source,
        manual_quantity=None if record.holding is None else record.holding.quantity,
        manual_cost_basis=None if record.holding is None else record.holding.cost_basis,
        as_of=position.as_of,
    )


def _valuation_out(v: svc.AccountValuation) -> dict:
    return {
        "account_id": v.account_id,
        "name": v.name,
        "currency": v.currency,
        "balance_source": v.balance_source or "stated",
        "balance_account": v.balance_account,
        "market_value_account": v.market_value_account,
        "market_value_base": v.market_value_base,
        "unaccounted_cash_base": v.unaccounted_cash_base or svc.ZERO,
        "holdings": [h.__dict__ for h in v.holdings],
        # Counts, matching the allocation endpoint. The offending positions are
        # not lost — they are in `holdings` above with `reason` set and a null
        # `value_base` — and a count is what a banner needs. Kept as two, never
        # one: "enter a price" and "enter an FX rate" are different fixes
        # (ADR-0032 §5).
        "unpriced": len(v.unpriced),
        "no_rate": len(v.no_rate),
        "oldest_price_date": v.oldest_price_date,
        "max_stale_days": v.max_stale_days,
        "is_fully_valued": v.is_fully_valued,
    }


# ---- securities -------------------------------------------------------------


@router.get("/securities", response_model=list[SecurityOut])
async def list_securities(ctx: RequestContext = Depends(get_context)):
    return [
        SecurityOut.model_validate(s) for s in await svc.list_securities(ctx.session)
    ]


@router.post("/securities", response_model=SecurityOut, status_code=201)
async def create_security(
    data: SecurityCreate, ctx: RequestContext = Depends(require_owner)
):
    """Define an instrument. Household owner role required."""
    security = await svc.create_security(ctx.session, ctx.household_id, data)
    return SecurityOut.model_validate(security)


@router.patch("/securities/{security_id}", response_model=SecurityOut)
async def update_security(
    security_id: uuid.UUID,
    data: SecurityUpdate,
    ctx: RequestContext = Depends(require_owner),
):
    """Rename, re-ticker or re-type. The quote currency is not editable."""
    return SecurityOut.model_validate(
        await svc.update_security(ctx.session, security_id, data)
    )


@router.delete("/securities/{security_id}", status_code=204)
async def delete_security(
    security_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)
) -> Response:
    """Delete a security along with its prices and every position in it.

    Owner-only, and destructive in a way the other deletes are not: it takes the
    household's recorded history in the instrument with it.
    """
    await svc.delete_security(ctx.session, security_id)
    return Response(status_code=204)


# ---- prices -----------------------------------------------------------------


@router.get("/securities/{security_id}/prices", response_model=list[PriceOut])
async def list_prices(
    security_id: uuid.UUID,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    return [
        PriceOut.model_validate(p)
        for p in await svc.list_security_prices(
            ctx.session, security_id=security_id, start=start, end=end
        )
    ]


@router.put("/securities/{security_id}/prices", response_model=PriceOut)
async def upsert_price(
    security_id: uuid.UUID,
    data: PriceUpsert,
    ctx: RequestContext = Depends(require_owner),
):
    """Record (or correct) the price for one day.

    A ``PUT`` because it is an upsert keyed by ``(security, date)`` — correcting a
    price is a normal thing to do, and a second row for the same day would make
    "the latest price on or before D" depend on insertion order.
    """
    price = await svc.upsert_price(
        ctx.session,
        household_id=ctx.household_id,
        security_id=security_id,
        price_date=data.price_date,
        price=data.price,
    )
    return PriceOut.model_validate(price)


# ---- holdings ---------------------------------------------------------------


@router.get("/holdings", response_model=list[HoldingOut])
async def list_holdings(
    account_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    """Positions with their resolved quantity and cost basis (ADR-0034).

    ``quantity`` and ``cost_basis`` are what the household actually holds — from
    history when it exists. ``manual_quantity`` is what was typed in, so a UI can
    show that a hand-entered number is being overridden rather than quietly
    disagreeing with the database.
    """
    records = await svc.list_holdings(ctx.session, account_id=account_id)
    return [_holding_out(r) for r in records]


@router.post("/holdings", response_model=HoldingOut, status_code=201)
async def upsert_holding(
    data: HoldingCreate, ctx: RequestContext = Depends(require_owner)
):
    """Record a position. One per (account, security) — a second call updates.

    409 if the position's number is computed from recorded trades; edit the trades
    instead (ADR-0034).
    """
    holding = await svc.upsert_holding(
        ctx.session,
        household_id=ctx.household_id,
        account_id=data.account_id,
        security_id=data.security_id,
        quantity=data.quantity,
        cost_basis=data.cost_basis,
        as_of=data.as_of,
    )
    return _holding_out(
        await _record_for(
            ctx.session, account_id=holding.account_id, security_id=holding.security_id
        )
    )


@router.patch("/holdings/{holding_id}", response_model=HoldingOut)
async def update_holding(
    holding_id: uuid.UUID,
    data: HoldingUpdate,
    ctx: RequestContext = Depends(require_owner),
):
    """Edit a hand-entered position. 409 when history owns the column."""
    holding = await svc.update_holding(ctx.session, holding_id, data)
    return _holding_out(
        await _record_for(
            ctx.session, account_id=holding.account_id, security_id=holding.security_id
        )
    )


@router.delete("/holdings/{holding_id}", status_code=204)
async def delete_holding(
    holding_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)
) -> Response:
    await svc.delete_holding(ctx.session, holding_id)
    return Response(status_code=204)


async def _record_for(session, *, account_id: uuid.UUID, security_id: uuid.UUID):
    """Re-read one position through the list path, so a write response is built the
    same way a read is — including a position a write just moved from ``manual`` to
    ``history``, which changes its quantity and its source."""
    for record in await svc.list_holdings(session, account_id=account_id):
        if record.position.security_id == security_id:
            return record
    raise LedgerError("Holding not found", 404)


# ---- investment transactions ------------------------------------------------


@router.get("/transactions", response_model=list[InvestmentTransactionOut])
async def list_investment_transactions(
    account_id: uuid.UUID | None = Query(default=None),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    ctx: RequestContext = Depends(get_context),
):
    rows = await svc.list_investment_transactions(
        ctx.session, account_id=account_id, start=start, end=end, limit=limit
    )
    return [InvestmentTransactionOut.model_validate(t) for t in rows]


@router.post("/transactions", response_model=InvestmentTransactionOut, status_code=201)
async def create_investment_transaction(
    data: InvestmentTransactionCreate, ctx: RequestContext = Depends(require_owner)
):
    """Record a trade, dividend, fee or split.

    A buy or sell does not adjust the position by hand: when history exists the
    position *is* the sum of its trades (ADR-0034), so this is the only write.
    """
    txn = await svc.create_investment_transaction(ctx.session, ctx.household_id, data)
    return InvestmentTransactionOut.model_validate(txn)


@router.patch("/transactions/{txn_id}", response_model=InvestmentTransactionOut)
async def update_investment_transaction(
    txn_id: uuid.UUID,
    data: InvestmentTransactionUpdate,
    ctx: RequestContext = Depends(require_owner),
):
    return InvestmentTransactionOut.model_validate(
        await svc.update_investment_transaction(ctx.session, txn_id, data)
    )


@router.delete("/transactions/{txn_id}", status_code=204)
async def delete_investment_transaction(
    txn_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)
) -> Response:
    """Delete a trade. This can move the position it belongs to (ADR-0034)."""
    await svc.delete_investment_transaction(ctx.session, txn_id)
    return Response(status_code=204)


# ---- valuation and allocation ----------------------------------------------


@router.get("/portfolio", response_model=PortfolioOut)
async def portfolio(
    on: date | None = Query(default=None),
    account_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    """Every investment account valued as of a date, with the pieces that could
    not be valued listed rather than counted as zero (ADR-0032 §5)."""
    as_of = on or _today()
    base = await ledger_svc.base_currency(ctx.session, ctx.household_id)
    account_ids = {account_id} if account_id is not None else None
    valuations, total = await svc.value_portfolio(
        ctx.session, on=as_of, base_ccy=base, account_ids=account_ids
    )
    return {
        "as_of": as_of,
        "base_currency": base,
        "total_base": total,
        "accounts": [_valuation_out(v) for v in valuations],
    }


@router.get("/allocation", response_model=AllocationOut)
async def allocation(
    on: date | None = Query(default=None),
    group_by: str = Query(default="security", pattern=_GROUP_PATTERN),
    ctx: RequestContext = Depends(get_context),
):
    """The consolidated cross-account allocation (ADR-0011).

    Unpriced positions are counted separately and never folded into a row as zero.
    """
    as_of = on or _today()
    base = await ledger_svc.base_currency(ctx.session, ctx.household_id)
    result = await svc.allocation(
        ctx.session, on=as_of, base_ccy=base, group_by=group_by
    )
    # The service names the date `on` because it is a parameter there; the wire
    # calls it `as_of`, matching the portfolio endpoint.
    return {**{k: v for k, v in result.items() if k != "on"}, "as_of": result["on"]}
