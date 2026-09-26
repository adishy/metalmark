"""Budgets: set a plan, and read the period it is measured over (ADR-0058).

Three routes and no more. A budget's identity is ``(category_id, period)`` — not
a row id — so the write is a ``PUT`` on that pair and "set this month's groceries
budget to 600" is one instruction whether or not there was a plan already. That
is also why there is no ``POST``: there is nothing a create would mean that the
``PUT`` does not.

``period`` is a **date, not a month string**, and any day in the month names that
month. The value is normalized to the first of the month server-side (the same
value ``periods`` keys a series on) and echoed on every response, so a client that
sent the 15th can see which period it got.

There is no ``owner_id`` on this router, deliberately: a plan is the household's,
and the spend it is read against is the whole household's. Narrowing one side of
the comparison to an owner would compare two different populations.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.deps import RequestContext, get_context
from app.schemas.budgets import BudgetOut, BudgetReport, BudgetSet
from app.services import budgets
from app.services.ledger import today as ledger_today

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=BudgetReport)
async def budget_report(
    period: date | None = Query(
        default=None, description="Any day in the period; defaults to this month"
    ),
    ctx: RequestContext = Depends(get_context),
):
    """The period's plans, and every figure they are read against.

    ``spent`` on each row is the Spending report's figure for the same window,
    and ``total_spent`` is that report's total to the cent — a test holds them
    equal, because a budget bar that disagrees with the report printed beside it
    is the one outcome this feature must not have. Spend with no category at all
    is not a row; it is ``unbudgeted_spent``, named rather than dropped.

    ``period`` defaults to the current month so a reader can open the page and
    see "this month" without working out what month it is in the household's
    timezone — a subscription the server already pays for everywhere else.
    """
    first = budgets.period_of(period or ledger_today())
    base, rows, totals, warnings = await budgets.budget_report(ctx.session, ctx.household_id, first)
    return BudgetReport(
        base_currency=base,
        period_start=first,
        period_end=budgets.period_end(first),
        rows=rows,
        warnings=warnings,
        **totals,
    )


@router.put("/{category_id}", response_model=BudgetOut)
async def set_budget(
    category_id: uuid.UUID,
    data: BudgetSet,
    period: date | None = Query(
        default=None, description="Any day in the period; defaults to this month"
    ),
    ctx: RequestContext = Depends(get_context),
):
    """Set or replace one category's plan for one period.

    Idempotent on the pair, so a retry after a dropped response is not a second
    budget. Only an **expense** category is accepted — ``spending_by_category``
    counts money out, so a plan on an income category would be compared against
    a quantity the report does not compute — and the 422 says which type the
    category actually is rather than a generic refusal.
    """
    row = await budgets.set_budget(
        ctx.session,
        ctx.household_id,
        category_id,
        period or ledger_today(),
        data.amount,
    )
    return BudgetOut.model_validate(row)


@router.delete("/{category_id}", status_code=204)
async def clear_budget(
    category_id: uuid.UUID,
    period: date | None = Query(
        default=None, description="Any day in the period; defaults to this month"
    ),
    ctx: RequestContext = Depends(get_context),
):
    """Remove one category's plan for one period.

    Clearing one that was never set is a **204, not a 404**: the state the caller
    asked for is the state that holds. A category that does not exist is still a
    404, because that is a wrong id rather than a request to remove nothing.

    Nothing in the ledger moves. A plan is a statement about a period, and
    removing it does not un-spend the money.
    """
    await budgets.clear_budget(ctx.session, category_id, period or ledger_today())
    return Response(status_code=204)
