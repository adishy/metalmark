from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort: int | None = None


class OwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sort: int | None = None


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str  # person | shared
    sort: int


class OwnerDeleteResult(BaseModel):
    """What the delete actually moved — the UI tells the user, rather than
    implying that deleting an owner was free."""

    reassigned_accounts: int
    reassigned_transactions: int
    reassigned_splits: int
