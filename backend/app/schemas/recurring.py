"""Recurring series (ADR-0053).

A series as the API takes it and as it answers with. The out model carries
three fields the row does not store — ``occurrences``, ``last_seen_date`` and
``monthly_amount`` — because they are derived at read time from the ledger and
from the cadence, the same "read once" discipline the income summary follows
(ADR-0052/ADR-0035).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.recurring import CADENCES

NAME_MAX_CHARS = 120
MERCHANT_MAX_CHARS = 300


def _valid_cadence(v: str) -> str:
    if v not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    return v


class RecurringCreate(BaseModel):
    """``POST /recurring``.

    ``transaction_id`` is the "pick a transaction" path: when it is sent, the
    server fills whatever the body left blank from that transaction (name,
    merchant, amount, account, category, currency), so a client that picked a
    row sends only the cadence it inferred and the id. The fields it sends
    explicitly always win — the person's edit is not overwritten by the seed.
    """

    name: str | None = Field(default=None, max_length=NAME_MAX_CHARS)
    merchant: str | None = Field(default=None, max_length=MERCHANT_MAX_CHARS)
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    cadence: str
    next_due_date: date | None = None
    is_active: bool = True
    transaction_id: uuid.UUID | None = None

    @field_validator("cadence")
    @classmethod
    def _cadence(cls, v: str) -> str:
        return _valid_cadence(v)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class RecurringUpdate(BaseModel):
    """``PATCH /recurring/{id}``. Absent means unchanged, like every other
    patch here (``schemas/patch.py``); an explicit ``null`` clears the field."""

    name: str | None = Field(default=None, max_length=NAME_MAX_CHARS)
    merchant: str | None = Field(default=None, max_length=MERCHANT_MAX_CHARS)
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    cadence: str | None = None
    next_due_date: date | None = None
    is_active: bool | None = None

    @field_validator("cadence")
    @classmethod
    def _cadence(cls, v: str | None) -> str | None:
        return _valid_cadence(v) if v is not None else v

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class RecurringOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant: str | None
    account_id: uuid.UUID | None
    category_id: uuid.UUID | None
    amount: Decimal
    currency: str
    cadence: str
    next_due_date: date | None
    is_active: bool
    transaction_id: uuid.UUID | None
    #: What the cadence makes this worth per month, so a weekly bill and an
    #: annual one are comparable in one list.
    monthly_amount: Decimal = Decimal(0)
    #: How many of the household's transactions this series matches, and when
    #: the last one landed. Derived from the ledger, never stored.
    occurrences: int = 0
    last_seen_date: date | None = None
    created_at: datetime
    updated_at: datetime


class RecurringTotalsOut(BaseModel):
    """Per currency, because summing dollars and euros into one figure would be
    a conversion nobody asked for (ADR-0005/ADR-0035). One entry per currency
    the filtered series use, the household's base currency first."""

    currency: str
    monthly_in: Decimal
    monthly_out: Decimal
    net_monthly: Decimal


class RecurringListOut(BaseModel):
    items: list[RecurringOut]
    totals: list[RecurringTotalsOut]


class RecurringSuggestionOut(BaseModel):
    """One thing the ledger says happens regularly, before anyone confirms it.

    Nothing is stored: suggestions are recomputed from transactions on every
    read, and they stop being offered once a series covers the same account and
    merchant (whether the person accepted one or entered it by hand). Every
    field here is what ``POST /recurring`` needs, so accepting is sending this
    back.
    """

    name: str
    merchant: str | None
    account_id: uuid.UUID | None
    category_id: uuid.UUID | None
    amount: Decimal
    currency: str
    cadence: str
    monthly_amount: Decimal
    occurrences: int
    first_date: date
    last_date: date
    next_due_date: date
    #: The most recent matching transaction — what a client seeds from when the
    #: suggestion is accepted.
    transaction_id: uuid.UUID
