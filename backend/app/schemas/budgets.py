"""Budget request and response shapes (ADR-0058).

Money is ``Decimal`` on every field and therefore a JSON **string** on the wire
(ADR-0005): a budget is compared against a sum of stored ledger amounts, and a
float that round-trips through JSON is a number nobody can hold the report to.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class BudgetSet(BaseModel):
    """The body of a ``PUT``: the whole of what a budget is.

    There is no partial form. A budget is one number, and "change it to X" is
    the only edit there is — which is also why the route is idempotent on
    ``(category_id, period)`` rather than a patch of a row id.
    """

    #: ``Numeric(19,4)``, the ledger's scale, and a magnitude: a negative plan
    #: is a typo with no meaning. Zero is allowed — "spend nothing on takeout"
    #: is a real thing to say, and the row's existence is what tells it from
    #: "no budget".
    amount: Decimal = Field(ge=0, max_digits=19, decimal_places=4)


class BudgetOut(BaseModel):
    """One stored plan, as written. Echoes ``period`` so a client that sent a
    month knows the row it got is keyed on the month it meant."""

    model_config = {"from_attributes": True}

    category_id: uuid.UUID
    period: date
    amount: Decimal


class BudgetRow(BaseModel):
    """One category's line in the period: the plan, the spend, or both.

    Every row carries a real category — ``category_id`` is never null. Spend
    that has no category at all (uncategorized, and the investment events the
    spending report names for what they were) is not a line here: it rolls into
    ``BudgetReport.unbudgeted_spent``, because there is no category to show it
    beside and inventing one would be inventing a row.

    ``budget`` is null for a category the household has not planned for. That is
    a **state**, not a missing value: the row is there because the category has
    spend, and the app says "no budget" rather than showing a bar of zero.
    """

    category_id: uuid.UUID
    category_name: str
    category_icon: str | None = None
    budget: Decimal | None = None
    spent: Decimal


class BudgetReport(BaseModel):
    """One period, whole: every plan, and every figure the plans are read against.

    **The window is stated, both ends.** A figure that says what was spent
    without saying over what window is the exact bug this app has been burned
    by, so ``period_start`` and ``period_end`` are required fields and every
    surface that shows a number from here shows the period with it.

    The three totals partition each other:

    * ``total_spent`` — everything ``/reports/spending`` reports for the same
      window, to the cent, including spend no plan can cover.
    * ``budgeted_spent`` — the part of it that falls on a planned category.
    * ``unbudgeted_spent`` — the rest: unplanned categories, uncategorized rows
      and investment events. Surfaced rather than hidden, because a budget
      screen that silently omits spend is a budget screen that lies.
    * ``total_budget`` — the plans added up. Not comparable to ``total_spent``
      by itself: ``total_spent`` includes categories with no plan, which is why
      ``unbudgeted_spent`` sits beside them.
    """

    base_currency: str
    period_start: date
    period_end: date
    rows: list[BudgetRow]
    total_budget: Decimal
    total_spent: Decimal
    budgeted_spent: Decimal
    unbudgeted_spent: Decimal
    #: Always "row", for the same reason ``SpendingReport.attribution`` is: this
    #: counts entries, and the figure it agrees with is the row-scoped report.
    attribution: str = "row"
    #: What the spending report could not convert for want of an FX rate. Passed
    #: through rather than swallowed: a dropped row is spend the reader paid and
    #: this report does not show.
    warnings: list[str] = []
