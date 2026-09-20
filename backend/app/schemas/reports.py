from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class NetWorthPoint(BaseModel):
    date: date
    net_worth: Decimal


class CashFlowPoint(BaseModel):
    month: str  # YYYY-MM
    income: Decimal
    expense: Decimal
    net: Decimal


class CategorySpendRow(BaseModel):
    category_id: uuid.UUID | None
    category_name: str
    total: Decimal


class NetWorthSeries(BaseModel):
    base_currency: str
    points: list[NetWorthPoint]
    # reconciliation over the whole range: Δ net worth = cash flow + revaluation
    delta_net_worth: Decimal
    net_cash_flow: Decimal
    currency_revaluation: Decimal
    # Always "account": an owner filter on this report selects accounts, not rows
    # (ADR-0026). Carried in the payload so the UI can say so instead of implying
    # the figures here add up with the row-scoped reports.
    attribution: str = "account"


class SpendingReport(BaseModel):
    base_currency: str
    rows: list[CategorySpendRow]
    total: Decimal
