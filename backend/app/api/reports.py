from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.reports import (
    CashFlowPoint,
    NetWorthSeries,
    SpendingReport,
)
from app.services import reports

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/net-worth", response_model=NetWorthSeries)
async def net_worth_series(start: date, end: date, ctx: RequestContext = Depends(get_context)):
    return NetWorthSeries(
        **await reports.net_worth_series(ctx.session, ctx.household_id, start, end)
    )


@router.get("/cash-flow", response_model=list[CashFlowPoint])
async def cash_flow(start: date, end: date, ctx: RequestContext = Depends(get_context)):
    _base, out = await reports.cash_flow_series(ctx.session, ctx.household_id, start, end)
    return [CashFlowPoint(**p) for p in out]


@router.get("/spending", response_model=SpendingReport)
async def spending(start: date, end: date, ctx: RequestContext = Depends(get_context)):
    base, rows, total = await reports.spending_by_category(
        ctx.session, ctx.household_id, start, end
    )
    return SpendingReport(base_currency=base, rows=rows, total=total)
