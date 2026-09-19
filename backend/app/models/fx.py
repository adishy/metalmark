"""FX rates — the single source of truth for currency conversion (ADR-0017).

High precision NUMERIC(19,8): 4dp is wrong for low-value currencies.
Unique on (base_currency, quote_currency, rate_date). Not household-scoped:
rates are reference data shared across households.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class FxRate(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "rate_date"),
    )

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 1 unit of base_currency = `rate` units of quote_currency.
    rate: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")  # auto|manual
