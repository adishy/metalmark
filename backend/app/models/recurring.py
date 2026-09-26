"""Recurring series: an expectation about transactions, not the transactions
themselves (ADR-0053).

A series is what a person says happens regularly — "Netflix, monthly, on the
card" — created either by hand or by accepting one of the detector's
suggestions, which is why it can carry the transaction it was picked from. It
owns no transactions: which ones it matches is derived at read time from the
ledger (account, merchant text, direction), so editing or deleting a series
never rewrites history.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.ledger import MONEY

#: How often a series is expected. Deliberately the same vocabulary as a pay
#: frequency where the two overlap (ADR-0052's ``pay_frequency``), plus the
#: quarter and half-year a bill can keep to and a paycheque cannot.
CADENCES = (
    "weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "annual",
)


class RecurringSeries(UUIDPkMixin, TimestampMixin, Base):
    """One regular thing, as the household states it."""

    __tablename__ = "recurring_series"
    __table_args__ = (
        CheckConstraint(
            "cadence IN ('weekly','biweekly','semimonthly','monthly','quarterly',"
            "'semiannual','annual')",
            name="ck_recurring_series_cadence",
        ),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The text a transaction's merchant must contain to count as one of this
    #: series' occurrences. Null means "any merchant on the account" — a person
    #: who only wants "the rent leaves this account monthly".
    merchant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: SET NULL, not CASCADE: a series is something a person created, and
    #: deleting an account should not silently delete their list. It just stops
    #: matching anything.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True,
    )
    #: Signed, like every other amount in the ledger (ADR-0005): negative is
    #: money out, positive is money in. The monthly total splits on that sign
    #: rather than on a second field that could disagree with it.
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    #: When the next occurrence is expected. Optional: the detector can infer a
    #: period but only the person knows the renewal date, so this is theirs.
    next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: A paused or cancelled series stays for the record and drops out of the
    #: monthly totals. It keeps covering its pattern in the detector, so pausing
    #: is how a person stops counting a charge — not a way to be asked about it
    #: again (ADR-0053).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The transaction this series was picked from, if any. Provenance, and the
    #: seed for the row; SET NULL so deleting that one transaction (a ducked
    #: charge, a mistaken import) does not delete the series.
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True,
    )
