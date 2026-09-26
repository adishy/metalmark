"""Per-category budgets: the plan, and the spend it is read against (ADR-0058).

Two halves, and the second one is the whole point.

The **plan** is a row: one amount, one category, one calendar period, written by
a person. That half is trivial — it is a table with three columns that matter.

The **comparison** is not, and it is deliberately not implemented here. ``spent``
comes from :func:`app.services.reports.spending_by_category` over exactly the
period's own window, so the number beside a budget is the number the Spending
report prints for the same month, including the rows no budget can cover. A
budget that summed the ledger itself would be a second answer to "what did we
spend", and two reports of one quantity that disagree are worse than one — which
is why there is no query in this module that touches ``transactions``.

The period is ``periods.period_start(day, "month")`` — the *unclipped* first of
the month, the value the periods module calls "the right thing to key a series
on". Every function here normalizes through it rather than trusting the caller:
a request for any day in September names September, and the response echoes the
first of the month so the caller can see which period it got.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_storage
from app.models.budgets import Budget
from app.models.ledger import Category, CategoryGroup
from app.schemas.budgets import BudgetRow
from app.services import periods, reports
from app.services.errors import LedgerError

#: Zero *at the storage scale*, and it matters that it is spelled this way.
#: Every ``spent`` the report returns is a sum of storage-quantized ledger
#: amounts, so an unspent category's ``0.0000`` must be the same shape as a spent
#: one's ``450.0000`` — a bare ``Decimal("0")`` would reach the wire as ``"0"``
#: and put two scales in one column, which a client comparing or formatting the
#: figures would have to normalize away.
ZERO = Decimal("0.0000")


def period_of(day: date) -> date:
    """The calendar month ``day`` falls in, as its first day.

    The one place this feature decides what a period is, so a write and a read
    cannot disagree about which month a budget belongs to.
    """
    return periods.period_start(day, "month")


def period_end(period: date) -> date:
    """The last day of ``period``'s month — the other end of the window the
    spend is summed over, and a field the report has to state."""
    return periods.period_end(period_of(period), "month")


async def _expense_category(session: AsyncSession, category_id: uuid.UUID) -> Category:
    """The category, refused unless its group makes it an expense one.

    A category's type *is* its group's (ADR-0049, ARCHITECTURE §2), so this is a
    join away rather than a column. It matters because
    ``spending_by_category`` counts money **out**: a plan on an income category
    would be compared against a quantity the report does not compute, and an
    income target is a floor to reach rather than a cap to stay under. Refusing
    at write time is what keeps every stored budget comparable.
    """
    cat = (
        await session.execute(select(Category).where(Category.id == category_id))
    ).scalar_one_or_none()
    if cat is None:
        raise LedgerError("Category not found", 404)
    grp = await session.get(CategoryGroup, cat.group_id)
    if grp is None or grp.type != "expense":
        raise LedgerError(
            "Budgets are for expense categories; this category is "
            f"{grp.type if grp else 'unknown'}", 422,
        )
    return cat


async def set_budget(
    session: AsyncSession,
    household_id: uuid.UUID,
    category_id: uuid.UUID,
    period: date,
    amount: Decimal,
) -> Budget:
    """Set or replace one category's plan for one period.

    An upsert on ``(category_id, period)``, not a create: the natural key *is*
    the row's identity, so "set this month's groceries budget to 600" is the
    same instruction whether or not there was one already, and sending it twice
    leaves one row rather than two. That is also what lets the route be a ``PUT``
    with no id in the body.

    ``amount`` is quantized to the ledger's storage scale (19,4) here rather than
    left as the caller wrote it, and not to a currency minor unit: the figure it
    is subtracted from is a sum of storage-quantized ledger amounts, and rounding
    the plan harder than the actual would make ``remaining`` differ from the
    reader's own arithmetic. Quantizing at the *write* is what makes the response
    echo the stored value: the row is not re-read from the database before it is
    returned, so without this a ``PUT`` of ``600`` would answer ``600`` while the
    ``GET`` beside it answered ``600.0000`` for the same row.
    """
    cat = await _expense_category(session, category_id)
    first = period_of(period)
    stored = quantize_storage(amount)

    row = (
        await session.execute(
            select(Budget).where(
                Budget.category_id == cat.id, Budget.period == first
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = Budget(
            household_id=household_id, category_id=cat.id, period=first, amount=stored
        )
        session.add(row)
    else:
        row.amount = stored
    await session.flush()
    return row


async def clear_budget(
    session: AsyncSession, category_id: uuid.UUID, period: date
) -> None:
    """Remove one category's plan for one period.

    **Idempotent**, and deliberately not a 404 when there is nothing to remove.
    The two cases are not the same question: a category that does not exist is a
    client bug and raises (via :func:`_expense_category`), while a category with
    no plan *is* the state the caller asked for — the second of two tabs pressing
    "Clear" should not be told it failed.

    The category's spend is untouched. Clearing a plan is a statement about the
    future, not about the past, and nothing in the ledger moves.
    """
    await _expense_category(session, category_id)
    row = (
        await session.execute(
            select(Budget).where(
                Budget.category_id == category_id, Budget.period == period_of(period)
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


async def budget_report(
    session: AsyncSession, household_id: uuid.UUID, period: date
) -> tuple[str, list[BudgetRow], dict[str, Decimal], list[str]]:
    """One period, whole — plans and the spend they are read against.

    Returns ``(base_currency, rows, totals, warnings)``; ``totals`` carries
    ``total_budget``, ``total_spent``, ``budgeted_spent`` and ``unbudgeted_spent``
    so the route can hand them straight to the response model.

    **``total_spent`` is the spending report's ``total``, not a sum of the rows
    here.** Rows carry a category and the spend with no category does not — the
    Uncategorized bucket, and the investment events the report names for what
    they were — so a total taken from these rows would be quietly smaller than
    the figure on ``/reports/spending`` for the same month. ``unbudgeted_spent``
    is the difference, which is where that spend is accounted for and said out
    loud rather than dropped. A test holds the two totals equal.

    **Row order is stated, not incidental:** categories with a plan first (most
    spent first), then categories with spend and no plan (most spent first), ties
    broken by name. A reader's budgets are the thing they came for; "and here is
    spending you have not planned for" follows.
    """
    first = period_of(period)
    last = period_end(first)

    base, spend_rows, total_spent, warnings = await reports.spending_by_category(
        session, household_id, first, last
    )

    # Only rows with a real category can meet a plan. The rest — uncategorized,
    # investment fees and income — has no `category_id` to match on and is
    # exactly what `unbudgeted_spent` is for.
    spent_by_cat: dict[uuid.UUID, Decimal] = {}
    for r in spend_rows:
        cat_id = r["category_id"]
        if cat_id is None:
            continue
        # One row per category today (`_flow_node` keys a transaction row on
        # `cat:<id>`), but summed rather than assigned so a future report that
        # splits a category across keys stays correct here instead of silently
        # keeping the last one.
        spent_by_cat[cat_id] = spent_by_cat.get(cat_id, ZERO) + r["total"]

    planned = (
        await session.execute(
            select(Budget, Category)
            .join(Category, Category.id == Budget.category_id)
            .where(Budget.period == first)
        )
    ).all()

    rows: list[BudgetRow] = []
    planned_ids: set[uuid.UUID] = set()
    for budget, cat in planned:
        planned_ids.add(cat.id)
        rows.append(
            BudgetRow(
                category_id=cat.id,
                category_name=cat.name,
                category_icon=cat.icon,
                budget=budget.amount,
                spent=spent_by_cat.get(cat.id, ZERO),
            )
        )

    # A category can carry spend with no plan. Those rows are the answer to
    # "what am I not budgeting for", and they are why `budget` is nullable
    # rather than the row simply being absent.
    unplanned_ids = [cid for cid in spent_by_cat if cid not in planned_ids]
    if unplanned_ids:
        names = {
            c.id: c
            for c in (
                await session.execute(select(Category).where(Category.id.in_(unplanned_ids)))
            ).scalars()
        }
        for cid in unplanned_ids:
            cat = names.get(cid)
            if cat is None:
                # The FK from a transaction's category is SET NULL and a
                # category delete moves its rows, so a spend row naming a
                # category that is gone should be impossible. Skipping rather
                # than raising keeps a stale report readable, and the spend is
                # still in `total_spent` so nothing is lost from the arithmetic.
                continue
            rows.append(
                BudgetRow(
                    category_id=cat.id,
                    category_name=cat.name,
                    category_icon=cat.icon,
                    budget=None,
                    spent=spent_by_cat[cat.id],
                )
            )

    planned_rows = [r for r in rows if r.budget is not None]
    unplanned_rows = [r for r in rows if r.budget is None]
    planned_rows.sort(key=lambda r: (-r.spent, r.category_name))
    unplanned_rows.sort(key=lambda r: (-r.spent, r.category_name))

    total_budget = sum((r.budget for r in planned_rows if r.budget is not None), ZERO)
    budgeted_spent = sum((r.spent for r in planned_rows), ZERO)

    totals = {
        "total_budget": total_budget,
        "total_spent": total_spent,
        # Subtracted rather than summed from the unplanned rows, so the two
        # sides of this identity are exact for any row set the report returns —
        # including the sentinel spend that is not a row above.
        "budgeted_spent": budgeted_spent,
        "unbudgeted_spent": total_spent - budgeted_spent,
    }
    return base, planned_rows + unplanned_rows, totals, warnings
