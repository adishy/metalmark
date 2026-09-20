from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class HouseholdOut(BaseModel):
    id: uuid.UUID
    name: str
    base_currency: str
    timezone: str
    role: str  # the requesting user's role in this household


class HouseholdUpdate(BaseModel):
    """``base_currency`` is absent on purpose: it is immutable once set (ADR-0017).

    ``extra="forbid"`` makes that absence enforceable: without it a PATCH carrying
    ``base_currency`` would be accepted and silently ignored, and the caller would
    believe the currency had changed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: EmailStr
    role: str
