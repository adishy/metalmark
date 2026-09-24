"""Agent access tokens (ADR-0048).

An identity table, like ``sessions``: a token has to be read before its household
is known, so it carries no ``household_id`` and no household RLS. The household is
the issuer's, resolved through ``household_members`` on every request exactly as a
session's is — so a token cannot outlive its issuer's membership, and it follows
them if that ever changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin

#: Read the structured resource API under ``/agent``.
SCOPE_AGENT_READ = "agent:read"
#: Read the debugging views under ``/anon_debug``.
SCOPE_DEBUG_READ = "debug:read"
SCOPES = (SCOPE_AGENT_READ, SCOPE_DEBUG_READ)


class AgentToken(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # sha256 of the token, as for sessions: the token is high-entropy, so a fast
    # hash is enough and it can be indexed for lookup.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # The first characters of the token, shown in the admin list so a person can
    # tell which token a leaked string is without the server keeping the token.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
