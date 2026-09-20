from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class NetWorthPoint(BaseModel):
    date: date
    net_worth: Decimal


class CashFlowPoint(BaseModel):
    month: str  # YYYY-MM
    income: Decimal
    expense: Decimal
    net: Decimal


class CashFlowSeries(BaseModel):
    base_currency: str
    points: list[CashFlowPoint]
    # Always "row": an owner filter selects entries, not accounts, so this is the
    # mirror of NetWorthSeries.attribution. The two reports take the same owner_id
    # and answer different questions with it, so the payload says which one it
    # answered rather than leaving a reader to assume they add up (ADR-0026).
    attribution: str = "row"


class CategorySpendRow(BaseModel):
    category_id: uuid.UUID | None
    category_name: str
    total: Decimal


class UnexplainedAccount(BaseModel):
    """One account's share of the residual — the name behind the number.

    A residual with no name is not a finding, so the report says which accounts the
    unexplained change came from rather than printing one figure and leaving the
    reader to guess whether the app is broken. Usually they are accounts whose
    balance is an *observation* the flows we hold do not cover — a first bank sync,
    whose balance is the bank's number from today while the history starts where the
    pull window starts.
    """

    account_id: uuid.UUID
    name: str
    amount: Decimal


class NetWorthSeries(BaseModel):
    """The net-worth series and the reconciliation of its change (ADR-0032).

    ``extra="forbid"`` is load-bearing rather than tidy: the API builds this from
    the service's dict, and Pydantic's default is to *drop* a key it does not know
    about — so a term added to the identity but forgotten here would vanish from the
    response with nothing failing, and the terms that did land would no longer add
    up to the delta. Forbidding extras turns that into a loud error instead.
    """

    model_config = ConfigDict(extra="forbid")

    base_currency: str
    points: list[NetWorthPoint]

    # Δ net worth = net cash flow + currency revaluation + market appreciation
    #               + unexplained
    #
    # The first three are each computed from data; `unexplained` is the remainder by
    # construction and is therefore the only term that can be *wrong*. A non-zero
    # value is not an error to hide — it is the report telling the truth about a gap,
    # and `warnings` says what could not be converted or priced (ADR-0032 §5).
    delta_net_worth: Decimal
    net_cash_flow: Decimal
    currency_revaluation: Decimal
    market_appreciation: Decimal
    unexplained: Decimal
    # Which accounts the residual came from, largest first, and only the material
    # ones: this is what turns `unexplained` from an accusation into a lead.
    unexplained_by_account: list[UnexplainedAccount] = []
    warnings: list[str] = []

    # Always "account": an owner filter on this report selects accounts, not rows
    # (ADR-0026). Carried in the payload so the UI can say so instead of implying
    # the figures here add up with the row-scoped reports.
    attribution: str = "account"


class SpendingReport(BaseModel):
    base_currency: str
    rows: list[CategorySpendRow]
    total: Decimal
    # Always "row", for the same reason as CashFlowSeries.attribution.
    attribution: str = "row"
