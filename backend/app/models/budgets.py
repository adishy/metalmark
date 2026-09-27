"""Per-category budgets: what the household plans to spend in one period
(ADR-0058).

A budget is a **plan**, not a ledger fact. It is stated by a person, in the
household's base currency, for one category in one calendar period — and the
spend it is compared against is never stored here. That comparison is
``reports.spending_by_category``, read at request time, so the figure beside a
budget is the figure the spending report prints, by construction rather than by
agreement.

The table is deliberately this small. Rollover, per-owner plans and non-monthly
resolutions are all out of scope in ADR-0058, and each of them would be a change
to how a period's *effective* allowance is computed rather than a column here.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.ledger import MONEY


class Budget(UUIDPkMixin, TimestampMixin, Base):
    """One category's amount for one period.

    **``period`` is ``periods.Bucket.period``** — the *unclipped* first day of a
    calendar month, i.e. what ``periods.period_start(day, "month")`` returns.
    The column is named for that role rather than for its current resolution: v1
    writes month starts only, but a later quarter or year plan is a different
    *value* in the same column and not a schema change, and naming it ``month``
    would say otherwise. Keying it on a re-derived date (a request's ``start``,
    say) would key it on a *window* instead, and the same month asked for two
    ways would be two budgets.

    **``category_id`` is ``NOT NULL`` and ``CASCADE``.** ``NOT NULL`` because
    *Uncategorized* is ``category_id IS NULL`` — a sentinel, deliberately not a
    row (:mod:`app.services.default_categories`) — so there is no category to
    point at and a plan for that bucket cannot exist. ``CASCADE`` rather than
    the ``SET NULL`` the transaction tables use, which raises on a ``NOT NULL``
    column; deleting a category taking its budgets with it is the honest
    outcome, since the plan was for that category.

    **``amount`` is ``MONEY`` (19,4) — the ledger's scale, not the income
    tables' ``Numeric(18,2)``.** This column is compared against and subtracted
    from ``spending_by_category``'s totals, which are sums of storage-quantized
    ledger amounts (ADR-0005); a narrower column would round a subtraction the
    report can represent.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        # One plan per category per period. Two rows for one month would make
        # "what did we budget" have two answers, which is the class of bug this
        # whole feature is arranged to avoid.
        UniqueConstraint(
            "household_id", "category_id", "period",
            name="uq_budgets_household_category_period",
        ),
        # A magnitude. Zero is allowed and is not the same state as no row:
        # "spend nothing on takeout this month" is a real thing to say, and the
        # UI tells the two apart by the row's existence, not by the number.
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        # The read path is always "this household, this period"; the unique
        # constraint above leads with `category_id`, so it cannot serve that.
        Index("ix_budgets_household_period", "household_id", "period"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: First day of the calendar period this plan is stated for.
    period: Mapped[date] = mapped_column(Date, nullable=False)
    #: In the household's base currency (ADR-0017).
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
