from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel


class CheckItemOut(BaseModel):
    account_id: uuid.UUID
    name: str


class CheckOut(BaseModel):
    """One data check (``services/checks.py``). ``items`` names the accounts behind
    a finding — here, behind the household's session, and never in a log."""

    id: str
    status: Literal["ok", "warn", "fail", "info"]
    count: int
    summary: str
    items: list[CheckItemOut]


class ChecksOut(BaseModel):
    #: The database's migration revision; ``None`` when it cannot be read.
    schema_version: str | None
    checks: list[CheckOut]
