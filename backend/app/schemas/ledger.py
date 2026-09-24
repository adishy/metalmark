from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.patch import is_set

ACCOUNT_TYPES = {"depository", "credit", "investment", "loan", "other"}
ASSET_TYPES = {"depository", "investment", "other"}  # liabilities: credit, loan


def is_asset_for(account_type: str) -> bool:
    return account_type in ASSET_TYPES


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern="^(depository|credit|investment|loan|other)$")
    currency: str = Field(min_length=3, max_length=3)
    subtype: str | None = None
    institution: str | None = None
    # The account's signed balance (ADR-0043): a card or loan's debt is negative.
    # Omitted means "not given" — the account opens with no balance history.
    current_balance: Decimal = Decimal("0")
    balance_date: date | None = None
    # Omitted or null both land on the household's Shared owner (ADR-0026).
    owner_id: uuid.UUID | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Correctable because sync can only guess it from the account's name; balances
    # are signed, so a retype changes the label and not the history (ADR-0043).
    type: str | None = Field(default=None, pattern="^(depository|credit|investment|loan|other)$")
    subtype: str | None = None
    institution: str | None = None
    current_balance: Decimal | None = None
    balance_date: date | None = None
    owner_id: uuid.UUID | None = None
    is_hidden: bool | None = None

    @model_validator(mode="after")
    def _required_fields_not_clearable(self) -> AccountUpdate:
        # There is no "unowned account" state — unset resolves to Shared, which is a
        # real owner the client can name. Rejecting null here is better than letting
        # the NOT NULL constraint answer with a 500, and the same goes for the other
        # required fields: an explicit null is a client bug, not a request to clear.
        for field in ("name", "type", "is_hidden", "owner_id", "current_balance"):
            if is_set(self, field) and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    type: str
    currency: str
    subtype: str | None
    institution: str | None
    current_balance: Decimal
    balance_date: date | None
    is_asset: bool
    owner_id: uuid.UUID
    is_manual: bool
    is_hidden: bool
    # Set when the bank has stopped reporting this synced account: the date of its
    # last balance, which the net-worth line is still carrying forward.
    stale_since: date | None = None


class NetWorthOut(BaseModel):
    base_currency: str
    assets: Decimal
    liabilities: Decimal
    net_worth: Decimal
    # currencies with balances we could not convert (no rate) — surfaced, not hidden
    unconverted_currencies: list[str] = []
    # "account": an owner filter here selects whole accounts, not individual rows —
    # see the attribution note on NetWorthSeries and ADR-0026.
    attribution: str = "account"


class CategoryGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(pattern="^(income|expense|transfer)$")
    sort: int = 0


class CategoryCreate(BaseModel):
    group_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    icon: str | None = None
    color: str | None = None
    sort: int = 0


class CategoryUpdate(BaseModel):
    group_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = None
    sort: int | None = None

    @model_validator(mode="after")
    def _required_fields_not_clearable(self) -> CategoryUpdate:
        for field in ("group_id", "name", "sort"):
            if is_set(self, field) and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AutoCategorizeResult(BaseModel):
    """What "auto-categorize all" did, for the admin who asked."""

    examined: int
    categorized: int
    changed: int
    left_blank: int
    transfers_linked: int


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    group_id: uuid.UUID
    name: str
    icon: str | None
    color: str | None
    sort: int


class CategoryGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    type: str
    sort: int


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = None


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    color: str | None


class FxRateUpsert(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)
    quote_currency: str = Field(min_length=3, max_length=3)
    rate_date: date
    rate: Decimal = Field(gt=0)


class FxRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    base_currency: str
    quote_currency: str
    rate_date: date
    rate: Decimal
    source: str
