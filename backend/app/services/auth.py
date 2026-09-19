"""Auth service: signup (invite-only), login, sessions, invites.

Identity tables are not household-RLS-scoped; this module runs on
``unscoped_session`` and enforces access at the application layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Household, HouseholdMember, Invite, Session, User
from app.security.passwords import (
    hash_password,
    hash_token,
    new_token,
    verify_password,
)
from app.settings import get_settings


class AuthError(Exception):
    """Raised for any auth failure; mapped to 401/400 at the API boundary."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


async def signup(session: AsyncSession, *, invite_token: str, email: str,
                 display_name: str, password: str) -> User:
    email = email.lower().strip()
    invite = (
        await session.execute(
            select(Invite).where(Invite.token_hash == hash_token(invite_token))
        )
    ).scalar_one_or_none()

    if invite is None:
        raise AuthError("Invalid invite token", 400)
    if invite.accepted_at is not None:
        raise AuthError("Invite already used", 400)
    if invite.expires_at < _now():
        raise AuthError("Invite expired", 400)
    if invite.email.lower() != email:
        raise AuthError("Invite was issued for a different email", 400)

    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise AuthError("A user with that email already exists", 400)

    user = User(email=email, password_hash=hash_password(password), display_name=display_name)
    session.add(user)
    await session.flush()

    session.add(
        HouseholdMember(
            household_id=invite.household_id,
            user_id=user.id,
            role=invite.role,
            joined_at=_now(),
        )
    )
    invite.accepted_at = _now()
    await session.flush()
    return user


async def login(session: AsyncSession, *, email: str, password: str) -> tuple[str, Session, User]:
    email = email.lower().strip()
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    # Constant-ish work whether or not the user exists.
    if user is None or not verify_password(user.password_hash, password):
        raise AuthError("Invalid email or password", 401)

    settings = get_settings()
    raw_token = new_token()
    csrf = new_token(16)
    now = _now()
    sess = Session(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        csrf_token=csrf,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
    )
    session.add(sess)
    await session.flush()
    return raw_token, sess, user


async def resolve_session(session: AsyncSession, raw_token: str) -> tuple[Session, User] | None:
    """Validate a session cookie: exists, not expired (idle + absolute)."""
    sess = (
        await session.execute(
            select(Session).where(Session.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()
    if sess is None:
        return None

    now = _now()
    settings = get_settings()
    idle_deadline = sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if sess.expires_at < now or idle_deadline < now:
        await session.delete(sess)
        return None

    sess.last_seen_at = now
    user = (await session.execute(select(User).where(User.id == sess.user_id))).scalar_one()
    return sess, user


async def logout(session: AsyncSession, raw_token: str) -> None:
    sess = (
        await session.execute(
            select(Session).where(Session.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()
    if sess is not None:
        await session.delete(sess)


async def membership_for(session: AsyncSession, user_id: uuid.UUID) -> HouseholdMember | None:
    return (
        await session.execute(
            select(HouseholdMember).where(HouseholdMember.user_id == user_id)
        )
    ).scalar_one_or_none()


async def create_invite(session: AsyncSession, *, household_id: uuid.UUID, email: str,
                        role: str) -> tuple[Invite, str]:
    raw = new_token()
    invite = Invite(
        household_id=household_id,
        email=email.lower().strip(),
        token_hash=hash_token(raw),
        role=role,
        expires_at=_now() + timedelta(days=7),
    )
    session.add(invite)
    await session.flush()
    return invite, raw


async def bootstrap_household(session: AsyncSession, *, name: str, base_currency: str,
                              owner_email: str, owner_name: str, owner_password: str,
                              timezone: str = "UTC") -> tuple[Household, User]:
    """Create the first household + owner (self-host first-run; no invite)."""
    household = Household(name=name, base_currency=base_currency.upper(), timezone=timezone)
    session.add(household)
    await session.flush()

    user = User(
        email=owner_email.lower().strip(),
        password_hash=hash_password(owner_password),
        display_name=owner_name,
        is_admin=True,
    )
    session.add(user)
    await session.flush()

    session.add(
        HouseholdMember(
            household_id=household.id, user_id=user.id, role="owner", joined_at=_now()
        )
    )
    await session.flush()
    return household, user
