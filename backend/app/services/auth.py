"""Auth service: open signup, login, sessions.

Identity tables are not household-RLS-scoped; this module runs on
``unscoped_session`` and enforces access at the application layer.

The whole access model is "join the one household" (ADR-0027): no invites, no
approval step. The first signup creates the household and owns it; everyone after
that joins as a member.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import set_scope
from app.models import Household, HouseholdMember, Session, User
from app.security.passwords import (
    hash_password,
    hash_token,
    new_token,
    verify_password,
)
from app.services.default_categories import install_defaults as install_default_categories
from app.services.owners import ensure_shared_owner
from app.settings import get_settings

# Rendezvous point for concurrent first-signups, not a resource id: two requests
# racing to be "first" would both see an empty users table and both create a
# household, and one of them would be stranded.
SIGNUP_LOCK_KEY = 2_026_092_001


class AuthError(Exception):
    """Raised for any auth failure; mapped to 401/400 at the API boundary."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


async def signup(session: AsyncSession, *, email: str, display_name: str, password: str,
                 household_name: str | None = None) -> User:
    """Register a user, creating the household if this is the first one.

    Open by design (ADR-0027) — anyone who can reach the instance can join it,
    which is only acceptable because it is LAN/VPN-only (ADR-0002). Set
    ``METALMARK_OPEN_SIGNUP=false`` to close it.
    """
    settings = get_settings()
    if not settings.open_signup:
        raise AuthError("Signup is closed on this instance", 403)

    email = email.lower().strip()
    await session.execute(select(func.pg_advisory_xact_lock(SIGNUP_LOCK_KEY)))

    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        # 409, matching the owners path: the request is well-formed, it just
        # conflicts with something that exists. 400 would blame the input.
        raise AuthError("A user with that email already exists", 409)

    # The oldest household is the household. There is exactly one in practice; the
    # ordering makes which one it is a fact rather than whatever the planner returns.
    household = (
        await session.execute(
            select(Household).order_by(Household.created_at, Household.id).limit(1)
        )
    ).scalar_one_or_none()

    if household is None:
        household = Household(
            name=household_name or f"{display_name}'s Household",
            base_currency=settings.default_base_currency.upper(),
            timezone="UTC",
        )
        session.add(household)
        await session.flush()
        role, is_admin = "owner", True
        # Created with the household so unattributed data always has somewhere to
        # land (ADR-0026) — the client never creates Shared. `owners` is RLS-scoped,
        # so this session has to say which household it is writing for; it has only
        # touched identity tables up to here. Transaction-local, and the household
        # exists, so the FK and the policy agree.
        await set_scope(session, household_id=household.id)
        await ensure_shared_owner(session, household.id)
        # The starter categories, typed, so the first sync already has somewhere
        # to file a transfer to an account the app does not hold (session 06).
        await install_default_categories(session, household.id)
    else:
        role, is_admin = "member", False

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        is_admin=is_admin,
    )
    session.add(user)
    await session.flush()

    session.add(
        HouseholdMember(
            household_id=household.id, user_id=user.id, role=role, joined_at=_now()
        )
    )
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


async def list_members(session: AsyncSession, household_id: uuid.UUID) -> list[dict]:
    rows = (
        await session.execute(
            select(User.id, User.display_name, User.email, HouseholdMember.role)
            .join(HouseholdMember, HouseholdMember.user_id == User.id)
            .where(HouseholdMember.household_id == household_id)
            .order_by(HouseholdMember.role, User.display_name)
        )
    ).all()
    return [
        {"user_id": uid, "display_name": name, "email": email, "role": role}
        for uid, name, email, role in rows
    ]


async def update_household(session: AsyncSession, household_id: uuid.UUID, *,
                           name: str | None = None,
                           timezone: str | None = None) -> Household:
    """Rename the household or set its timezone.

    ``base_currency`` is deliberately not editable: every stored amount, rate and
    base_amount cache is denominated in it (ADR-0017), so changing it is a
    migration, not a settings toggle.
    """
    household = (
        await session.execute(select(Household).where(Household.id == household_id))
    ).scalar_one()
    if name is not None:
        household.name = name
    if timezone is not None:
        household.timezone = timezone
    await session.flush()
    return household


async def bootstrap_household(session: AsyncSession, *, name: str, base_currency: str,
                              owner_email: str, owner_name: str, owner_password: str,
                              timezone: str = "UTC") -> tuple[Household, User]:
    """Create the first household + owner (self-host first-run; no invite)."""
    household = Household(name=name, base_currency=base_currency.upper(), timezone=timezone)
    session.add(household)
    await session.flush()
    # See signup(): `owners` is RLS-scoped and this session came in unscoped.
    await set_scope(session, household_id=household.id)
    await ensure_shared_owner(session, household.id)

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
