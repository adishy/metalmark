from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.patch import is_set


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort: int | None = None


class OwnerUpdate(BaseModel):
    """Send only the fields you want to change; an explicit null is rejected."""

    # Neither field is nullable in the database — an owner always has a name and a
    # position — so a null here is a client bug, and saying so beats the silent 200
    # a bare `is not None` check produced for it. Hence `is_set`: absent and null
    # have to mean different things, or clearing is impossible to express.
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sort: int | None = None

    @model_validator(mode="after")
    def _fields_not_clearable(self) -> OwnerUpdate:
        for field in ("name", "sort"):
            if is_set(self, field) and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str  # person | shared
    sort: int


class OwnerDeleteResult(BaseModel):
    """How many rows the delete moved onto ``reassign_to``."""

    # Returned rather than implied: deleting an owner silently re-attributes
    # history, and the UI has to be able to say how much moved.
    reassigned_accounts: int
    reassigned_transactions: int
    reassigned_splits: int
