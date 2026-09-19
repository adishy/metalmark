from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

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
    # Signed magnitude; liabilities entered as a positive amount owed.
    current_balance: Decimal = Decimal("0")
    balance_date: date | None = None
    owner_user_id: uuid.UUID | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subtype: str | None = None
    institution: str | None = None
    current_balance: Decimal | None = None
    balance_date: date | None = None
    owner_user_id: uuid.UUID | None = None
    is_hidden: bool | None = None


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
    owner_user_id: uuid.UUID | None
    is_manual: bool
    is_hidden: bool


class NetWorthOut(BaseModel):
    base_currency: str
    assets: Decimal
    liabilities: Decimal
    net_worth: Decimal
    # currencies with balances we could not convert (no rate) — surfaced, not hidden
    unconverted_currencies: list[str] = []


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
