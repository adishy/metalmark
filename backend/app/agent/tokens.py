"""Agent tokens: issue, list, revoke, resolve (ADR-0048).

Runs on ``unscoped_session``, like the session code in ``services/auth``: the
table is an identity table with no household RLS, so the household is enforced
here — a token is listed and revoked only through its issuer's membership.

**Who may hold one.** A token acts with its issuer's household and role, and only
while the issuer may still manage tokens (an administrator, or the household's
owner). That is checked on every request, not only at issue, so demoting or
removing the issuer switches their tokens off without anyone having to remember
them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HouseholdMember, User
from app.models.agent import SCOPES, AgentToken
from app.security.passwords import hash_token, new_token
from app.services import auth as auth_service
from app.services.errors import LedgerError

#: Every token starts with this, so a secret scanner (and a person) can tell one
#: when it turns up in a paste or a log.
TOKEN_PREFIX = "mmk_"
#: How many characters of the token the admin list shows.
DISPLAY_PREFIX_LEN = 12
#: ``last_used_at`` is written at most this often, so an agent walking the whole
#: API does not turn every read into a write to ``agent_tokens``.
LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class AgentPrincipal:
    """Who a request made with an agent token acts as."""

    token_id: uuid.UUID
    user_id: uuid.UUID
    household_id: uuid.UUID
    role: str
    scopes: frozenset[str]


def _now() -> datetime:
    return datetime.now(UTC)


def can_manage(user: User, role: str) -> bool:
    """Administrators and the household's owner may issue, list and revoke."""
    return bool(user.is_admin) or role == "owner"


def status(token: AgentToken, now: datetime | None = None) -> str:
    now = now or _now()
    if token.revoked_at is not None:
        return "revoked"
    if token.expires_at is not None and token.expires_at <= now:
        return "expired"
    return "active"


async def create(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    scopes: list[str],
    expires_in_days: int,
) -> tuple[str, AgentToken]:
    """Issue a token. Returns the raw token — the only time it exists outside the
    caller — and the stored row, which holds its hash."""
    unknown = set(scopes) - set(SCOPES)
    if unknown:
        raise LedgerError(f"Unknown scope(s): {', '.join(sorted(unknown))}", 422)
    raw = TOKEN_PREFIX + new_token(32)
    token = AgentToken(
        user_id=user.id,
        name=name.strip(),
        token_hash=hash_token(raw),
        prefix=raw[:DISPLAY_PREFIX_LEN],
        scopes=sorted(set(scopes)),
        expires_at=_now() + timedelta(days=expires_in_days),
    )
    session.add(token)
    await session.flush()
    await session.refresh(token)
    return raw, token


async def list_for_household(
    session: AsyncSession, household_id: uuid.UUID
) -> list[tuple[AgentToken, str]]:
    """Every token issued by a member of ``household_id``, newest first, with the
    issuer's display name."""
    rows = (
        await session.execute(
            select(AgentToken, User.display_name)
            .join(User, User.id == AgentToken.user_id)
            .join(HouseholdMember, HouseholdMember.user_id == AgentToken.user_id)
            .where(HouseholdMember.household_id == household_id)
            .order_by(AgentToken.created_at.desc(), AgentToken.id)
        )
    ).all()
    return [(t, name) for t, name in rows]


async def revoke(
    session: AsyncSession, household_id: uuid.UUID, token_id: uuid.UUID
) -> tuple[AgentToken, str]:
    """Revoke one of the household's tokens. Idempotent: revoking a revoked token
    keeps its first ``revoked_at``. Another household's id is a 404."""
    for token, name in await list_for_household(session, household_id):
        if token.id == token_id:
            if token.revoked_at is None:
                token.revoked_at = _now()
                await session.flush()
            return token, name
    raise LedgerError("Token not found", 404)


async def resolve(session: AsyncSession, raw: str) -> AgentPrincipal | None:
    """The principal behind a raw token, or ``None`` if it does not authorize
    anything: unknown, revoked, expired, or its issuer can no longer manage tokens
    (demoted, or no longer in a household)."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    token = (
        await session.execute(select(AgentToken).where(AgentToken.token_hash == hash_token(raw)))
    ).scalar_one_or_none()
    now = _now()
    if token is None or status(token, now) != "active":
        return None
    user = (
        await session.execute(select(User).where(User.id == token.user_id))
    ).scalar_one_or_none()
    if user is None:
        return None
    membership = await auth_service.membership_for(session, user.id)
    if membership is None or not can_manage(user, membership.role):
        return None
    if token.last_used_at is None or now - token.last_used_at >= LAST_USED_RESOLUTION:
        token.last_used_at = now
    return AgentPrincipal(
        token_id=token.id,
        user_id=user.id,
        household_id=membership.household_id,
        role=membership.role,
        scopes=frozenset(token.scopes),
    )
