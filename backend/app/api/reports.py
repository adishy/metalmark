from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import RequestContext, get_context
from app.schemas.reports import (
    CashFlowPoint,
    CashFlowSankey,
    CashFlowSeries,
    NetWorthSeries,
    SpendingReport,
)
from app.services import reports
from app.services.periods import GranularityIn

router = APIRouter(prefix="/reports", tags=["reports"])


# The reports take the same owner_id but answer different questions with it
# (account-scoped for net worth, row-scoped for the rest) — see the module
# docstring in services/reports.py and ADR-0026.
#
# They also take the same window, and each one echoes it back resolved. `start` is
# optional so a reader can ask for *all of it* — the one range a client cannot
# compute, because only the server knows where the data begins. `granularity` is
# on the two that have buckets; `/spending` is a single total over the window and
# `/cash-flow/sankey` is a single graph over it, and both take none, because a
# parameter that provably changes nothing is worse than an absent one: a caller
# would be right to assume it did something.


async def _window(ctx: RequestContext, start: date | None, end: date) -> tuple[date, date]:
    """Resolve an open range and refuse an inverted one.

    An inverted window is not an empty report — `periods.buckets` would return no
    buckets and every chart would draw an empty state that looks like a household
    with no money in it. A client that sent `start` after `end` has a bug, and the
    only honest answer is to say so. A household with no data at all is a
    different thing and gets a real one-day window, because that one is true.
    """
    if start is None:
        start = await reports.earliest_activity(ctx.session) or end
    if start > end:
        raise HTTPException(status_code=422, detail="start is after end")
    return start, end


@router.get("/net-worth", response_model=NetWorthSeries)
async def net_worth_series(
    end: date,
    start: date | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    granularity: GranularityIn = Query(default="auto"),
    ctx: RequestContext = Depends(get_context),
):
    start, end = await _window(ctx, start, end)
    return NetWorthSeries(
        start=start,
        end=end,
        **await reports.net_worth_series(
            ctx.session, ctx.household_id, start, end, owner_id, granularity
        ),
    )


@router.get("/cash-flow", response_model=CashFlowSeries)
async def cash_flow(
    end: date,
    start: date | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    granularity: GranularityIn = Query(default="auto"),
    ctx: RequestContext = Depends(get_context),
):
    start, end = await _window(ctx, start, end)
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


@router.get("/cash-flow/sankey", response_model=CashFlowSankey)
async def cash_flow_sankey(
    end: date,
    start: date | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    start, end = await _window(ctx, start, end)
    return CashFlowSankey(
        **await reports.cash_flow_sankey(ctx.session, ctx.household_id, start, end, owner_id)
    )


@router.get("/spending", response_model=SpendingReport)
async def spending(
    end: date,
    start: date | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_context),
):
    start, end = await _window(ctx, start, end)
    base, rows, total, warnings = await reports.spending_by_category(
        ctx.session, ctx.household_id, start, end, owner_id
    )
    return SpendingReport(
        base_currency=base, start=start, end=end, rows=rows, total=total, warnings=warnings
    )
