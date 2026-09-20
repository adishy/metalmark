from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, get_context
from app.schemas.reports import (
    CashFlowPoint,
    CashFlowSeries,
    NetWorthSeries,
    SpendingReport,
)
from app.services import reports
from app.services.periods import GranularityIn

router = APIRouter(prefix="/reports", tags=["reports"])


# The three reports take the same owner_id but answer different questions with it
# (account-scoped here, row-scoped below) — see the module docstring in
# services/reports.py and ADR-0026.
#
# They also take the same window, and each one echoes it back. `granularity` is on
# the two that have buckets; `/spending` is a single total over the window and
# takes none, because a parameter that provably changes nothing is worse than an
# absent one — a caller would be right to assume it did something.


@router.get("/net-worth", response_model=NetWorthSeries)
async def net_worth_series(
    start: date,
    end: date,
    owner_id: uuid.UUID | None = Query(default=None),
    granularity: GranularityIn = Query(default="auto"),
    ctx: RequestContext = Depends(get_context),
):
    return NetWorthSeries(
        start=start,
        end=end,
        **await reports.net_worth_series(
            ctx.session, ctx.household_id, start, end, owner_id, granularity
        ),
    )


@router.get("/cash-flow", response_model=CashFlowSeries)
async def cash_flow(
    start: date,
    end: date,
    owner_id: uuid.UUID | None = Query(default=None),
    granularity: GranularityIn = Query(default="auto"),
    ctx: RequestContext = Depends(get_context),
):
    base, resolved, out = await reports.cash_flow_series(
        ctx.session, ctx.household_id, start, end, owner_id, granularity
    )
    return CashFlowSeries(
        base_currency=base,
        start=start,
        end=end,
        granularity=resolved,
        points=[CashFlowPoint(**p) for p in out],
    )


@router.get("/spending", response_model=SpendingReport)
async def spending(
    start: date,
    end: date,
    owner_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    base, rows, total = await reports.spending_by_category(
        ctx.session, ctx.household_id, start, end, owner_id
    )
    return SpendingReport(base_currency=base, start=start, end=end, rows=rows, total=total)
