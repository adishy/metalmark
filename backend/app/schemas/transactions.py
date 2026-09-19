from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SplitIn(BaseModel):
    # Exactly one of amount / pct across siblings; validated in the service.
    amount: Decimal | None = None
    pct: Decimal | None = None
    category_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    notes: str | None = None


class SplitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount: Decimal
    base_amount: Decimal | None
    category_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    notes: str | None


class TransactionCreate(BaseModel):
    account_id: uuid.UUID
    amount: Decimal  # + = money in
    transacted_at: datetime
    posted_at: datetime | None = None
    description: str | None = None
    merchant: str | None = None
    category_id: uuid.UUID | None = None
    is_pending: bool = False
    notes: str | None = None
    tag_ids: list[uuid.UUID] = []


class TransactionUpdate(BaseModel):
    amount: Decimal | None = None
    transacted_at: datetime | None = None
    posted_at: datetime | None = None
    description: str | None = None
    merchant: str | None = None
    category_id: uuid.UUID | None = None
    is_pending: bool | None = None
    is_hidden: bool | None = None
    review_status: str | None = Field(default=None, pattern="^(needs_review|reviewed|ignored)$")
    notes: str | None = None
    tag_ids: list[uuid.UUID] | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal
    currency: str
    base_amount: Decimal | None
    fx_rate_date: date | None
    transacted_at: datetime
    posted_at: datetime | None
    description: str | None
    merchant: str | None
    category_id: uuid.UUID | None
    is_pending: bool
    review_status: str
    is_hidden: bool
    is_split_parent: bool
    transfer_group_id: uuid.UUID | None
    field_sources: dict
    notes: str | None
    source: str
    tag_ids: list[uuid.UUID] = []
    splits: list[SplitOut] = []


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    next_cursor: str | None = None


class TransferLink(BaseModel):
    """Manually link two existing transactions as a transfer (ADR-0008/0018)."""
    from_txn_id: uuid.UUID
    to_txn_id: uuid.UUID


class TransferOut(BaseModel):
    transfer_group_id: uuid.UUID
    matched_by: str
    fx_cost_base: Decimal | None
    txn_ids: list[uuid.UUID]
