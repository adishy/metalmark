from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.ledger import FxRateOut, FxRateUpsert
from app.services import ledger

router = APIRouter(prefix="/fx-rates", tags=["fx"])


@router.get("", response_model=list[FxRateOut])
async def list_rates(ctx: RequestContext = Depends(get_context)):
    return [FxRateOut.model_validate(r) for r in await ledger.list_fx_rates(ctx.session)]


@router.post("", response_model=FxRateOut, status_code=201)
async def upsert_rate(data: FxRateUpsert, ctx: RequestContext = Depends(get_context)):
    row = await ledger.upsert_fx_rate(
        ctx.session,
        ctx.household_id,
        base_ccy=data.base_currency,
        quote_ccy=data.quote_currency,
        rate_date=data.rate_date,
        rate=data.rate,
        source="manual",
    )
    return FxRateOut.model_validate(row)
