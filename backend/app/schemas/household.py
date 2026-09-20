from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr


class HouseholdOut(BaseModel):
    id: uuid.UUID
    name: str
    base_currency: str
    timezone: str
    role: str  # the requesting user's role in this household


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: EmailStr
    role: str
