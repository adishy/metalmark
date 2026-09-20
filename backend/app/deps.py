"""Request dependencies: session resolution, per-request tenant scoping, CSRF.

A single request-scoped transaction: identity is resolved first (identity tables
carry no household RLS), then the ``app.household_id`` GUC is set so every
subsequent financial query in the handler is tenant-scoped (ADR-0014).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import User
from app.services import auth as auth_service

SESSION_COOKIE = "kestrel_session"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class RequestContext:
    session: AsyncSession
    user: User
    household_id: uuid.UUID
    role: str
    csrf_token: str


async def get_context(
    request: Request,
    kestrel_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    """Yield an authenticated, tenant-scoped context for a request.

    Raises 401 if unauthenticated, 403 if the user has no household yet.
    """
    if not kestrel_session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        resolved = await auth_service.resolve_session(session, kestrel_session)
        if resolved is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        sess, user = resolved

        membership = await auth_service.membership_for(session, user.id)
        if membership is None:
            raise HTTPException(status_code=403, detail="User is not in a household")

        # From here on, every query is scoped to this household by RLS.
        await session.execute(
            text("SELECT set_config('app.household_id', :hid, true)"),
            {"hid": str(membership.household_id)},
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": str(user.id)},
        )

        ctx = RequestContext(
            session=session,
            user=user,
            household_id=membership.household_id,
            role=membership.role,
            csrf_token=sess.csrf_token,
        )
        _check_csrf(request, ctx)
        yield ctx


def _check_csrf(request: Request, ctx: RequestContext) -> None:
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER)
        if not header or header != ctx.csrf_token:
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def require_owner(ctx: RequestContext = Depends(get_context)) -> RequestContext:
    if ctx.role != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
    return ctx
