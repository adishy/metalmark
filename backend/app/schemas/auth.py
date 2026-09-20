from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)
    # Used only when this signup creates the household (the first one).
    household_name: str | None = Field(default=None, min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    is_admin: bool


class MeResponse(BaseModel):
    user: UserResponse
    household_id: uuid.UUID
    household_name: str
    base_currency: str
    role: str
    csrf_token: str
