"""Owner income profile and paystubs (ADR-0052).

A paystub's lines are replace-all, like a transaction's splits
(``services/transactions.set_splits``): the client sends the whole set it
wants stored, keyed by nothing but position, because a paystub is transcribed
from a document a person is looking at, not edited line-by-line against a
server-held id a client would otherwise have to track.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.income import FILING_STATUSES, PAY_FREQUENCIES, PAYSTUB_LINE_KINDS

EMPLOYER_MAX_CHARS = 200
LABEL_MAX_CHARS = 100


# ---- income profile ---------------------------------------------------------


class OwnerIncomeProfileUpdate(BaseModel):
    """``PUT /owners/{id}/income-profile``. Every field is optional — a
    household may only want to state some of this — and absent means "leave
    unchanged" on an existing profile (``is_set``), or "leave null" when the
    profile is being created by this same call.
    """

    currency: str | None = Field(default=None, min_length=3, max_length=3)
    annual_gross_income: Decimal | None = Field(default=None, ge=0)
    pay_frequency: str | None = None
    filing_status: str | None = None
    tax_region: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("pay_frequency")
    @classmethod
    def _valid_frequency(cls, v: str | None) -> str | None:
        if v is not None and v not in PAY_FREQUENCIES:
            raise ValueError(f"pay_frequency must be one of {PAY_FREQUENCIES}")
        return v

    @field_validator("filing_status")
    @classmethod
    def _valid_filing_status(cls, v: str | None) -> str | None:
        if v is not None and v not in FILING_STATUSES:
            raise ValueError(f"filing_status must be one of {FILING_STATUSES}")
        return v


class YtdKindTotal(BaseModel):
    kind: str
    amount: Decimal


class IncomeSummaryOut(BaseModel):
    """Derived at read time from the profile and the owner's paystubs —
    nothing here is stored, so there is nothing to keep in sync (ADR-0052,
    ADR-0035's "read once" discipline).
    """

    #: The profile's own figure if stated; otherwise the latest paystub's
    #: gross times its frequency's annual multiplier. Null when neither exists.
    annualized_gross: Decimal | None
    #: This calendar year's paystubs, summed by line kind. A list rather than a
    #: ``dict[kind, amount]`` — every response model here lists its fields
    #: explicitly (see ``schemas/connections.py``'s own note on this), and a
    #: plain dict also cannot carry its own per-field agent policy.
    ytd: list[YtdKindTotal]
    #: Σ tax / Σ gross over this year's paystubs. Null with no paystubs this year.
    effective_tax_rate: Decimal | None


class OwnerIncomeProfileFields(BaseModel):
    """The profile row itself, as it actually exists. Split out from
    ``OwnerIncomeProfileOut`` so "no profile yet" can be ``profile: null``
    instead of a row of invented zeros and epoch timestamps — an owner with
    nothing stated is a real, first-class state, not an error."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    currency: str
    annual_gross_income: Decimal | None
    pay_frequency: str | None
    filing_status: str | None
    tax_region: str | None
    created_at: datetime
    updated_at: datetime


class OwnerIncomeProfileOut(BaseModel):
    owner_id: uuid.UUID
    #: Null until the household's first ``PUT`` — see ``OwnerIncomeProfileFields``.
    profile: OwnerIncomeProfileFields | None
    #: Always present, even with no profile: it can still summarize from
    #: paystubs alone.
    summary: IncomeSummaryOut


# ---- paystubs -----------------------------------------------------------------


class PaystubLineIn(BaseModel):
    kind: str
    label: str = Field(min_length=1, max_length=LABEL_MAX_CHARS)
    amount: Decimal = Field(ge=0)
    ytd_amount: Decimal | None = Field(default=None, ge=0)
    #: Display order. Defaults to the line's position in the submitted list,
    #: so a client that does not care can simply omit it.
    position: int | None = None

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if v not in PAYSTUB_LINE_KINDS:
            raise ValueError(f"kind must be one of {PAYSTUB_LINE_KINDS}")
        return v


class PaystubLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    label: str
    amount: Decimal
    ytd_amount: Decimal | None
    position: int


class PaystubCreate(BaseModel):
    pay_date: date
    period_start: date | None = None
    period_end: date | None = None
    employer: str | None = Field(default=None, max_length=EMPLOYER_MAX_CHARS)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    gross: Decimal = Field(ge=0)
    net: Decimal = Field(ge=0)
    #: Empty is allowed — a header-only paystub is not second-class
    #: (ADR-0052). When non-empty, the service checks gross/net against them.
    lines: list[PaystubLineIn] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @model_validator(mode="after")
    def _period_order(self) -> PaystubCreate:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        return self


class PaystubPatch(BaseModel):
    """Absent means unchanged, for every field including ``lines`` — sending
    ``"lines": []`` is a real request ("this paystub has no line detail"),
    which is exactly why omitting the key, not an empty list, is what leaves
    the stored lines alone.
    """

    pay_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    employer: str | None = Field(default=None, max_length=EMPLOYER_MAX_CHARS)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    gross: Decimal | None = Field(default=None, ge=0)
    net: Decimal | None = Field(default=None, ge=0)
    lines: list[PaystubLineIn] | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class PaystubOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    pay_date: date
    period_start: date | None
    period_end: date | None
    employer: str | None
    currency: str
    gross: Decimal
    net: Decimal
    lines: list[PaystubLineOut]
    created_at: datetime
    updated_at: datetime
