"""Owner income: an annual profile plus actual paystubs (ADR-0052).

Foundation for tax modelling later — not read by Insights yet. All
household-scoped (RLS, ADR-0014/0025), like every table here.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin

MONEY = Numeric(18, 2)

PAY_FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "annual")
FILING_STATUSES = ("single", "married_joint", "married_separate", "head_of_household")
PAYSTUB_LINE_KINDS = (
    "earning", "pre_tax_deduction", "tax", "post_tax_deduction", "employer_contribution",
)


class OwnerIncomeProfile(UUIDPkMixin, TimestampMixin, Base):
    """One per owner: what the household states about that owner's income.

    Every field but ``currency`` is nullable — a household may know only some
    of this, and a blank profile (created implicitly by the API on first
    write) is not an error state.
    """

    __tablename__ = "owner_income_profiles"
    __table_args__ = (
        UniqueConstraint("owner_id", name="uq_owner_income_profiles_owner_id"),
        CheckConstraint(
            "pay_frequency IS NULL OR pay_frequency IN "
            "('weekly','biweekly','semimonthly','monthly','annual')",
            name="ck_owner_income_profiles_pay_frequency",
        ),
        CheckConstraint(
            "filing_status IS NULL OR filing_status IN "
            "('single','married_joint','married_separate','head_of_household')",
            name="ck_owner_income_profiles_filing_status",
        ),
        CheckConstraint(
            "annual_gross_income IS NULL OR annual_gross_income >= 0",
            name="ck_owner_income_profiles_gross_nonneg",
        ),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    annual_gross_income: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    pay_frequency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    filing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: e.g. "US-CA". Free-ish rather than a closed set — tax jurisdictions are
    #: not enumerable the way filing status is — but bounded, since it is a
    #: code, not prose.
    tax_region: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Paystub(UUIDPkMixin, TimestampMixin, Base):
    """One actual paystub. ``gross``/``net`` are the header figures — the
    lines are the detail, and when lines are present the service layer checks
    the header agrees with them (ADR-0052)."""

    __tablename__ = "paystubs"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    employer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    lines: Mapped[list[PaystubLine]] = relationship(
        back_populates="paystub", cascade="all, delete-orphan",
        order_by="PaystubLine.position",
    )


class PaystubLine(UUIDPkMixin, TimestampMixin, Base):
    """One line of a paystub, typed by ``kind`` so a tax model can tell an
    employer 401k match (never the owner's money) from a pre-tax deduction
    (the owner's money, just not taxed yet) apart."""

    __tablename__ = "paystub_lines"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('earning','pre_tax_deduction','tax','post_tax_deduction',"
            "'employer_contribution')",
            name="ck_paystub_lines_kind",
        ),
        CheckConstraint("amount >= 0", name="ck_paystub_lines_amount_nonneg"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    paystub_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paystubs.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ytd_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    paystub: Mapped[Paystub] = relationship(back_populates="lines")
