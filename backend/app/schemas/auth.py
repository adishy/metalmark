from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    invite_token: str
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(owner|member)$")


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: str
    # The raw token is returned ONCE at creation so the inviter can share the link.
    token: str | None = None


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
