"""Request dependencies: session resolution, per-request tenant scoping, CSRF.

A single request-scoped transaction: identity is resolved first (identity tables
carry no household RLS), then the ``app.household_id`` GUC is set so every
subsequent financial query in the handler is tenant-scoped (ADR-0014).

**The agent grant (ADR-0048).** The ``/agent`` and ``/anon_debug`` routes answer by
running an app route in-process on the agent's behalf (``app/agent/dispatch.py``).
That sub-request carries an ``AgentPrincipal`` in its ASGI scope under
``AGENT_GRANT_KEY`` instead of a cookie, and ``get_context`` builds the context from
it — in a ``READ ONLY`` transaction, and for ``GET`` only. A client cannot set that
key: it is not a header or a cookie, it exists only in a scope the app builds for
itself. A bearer token sent to an ordinary route is ignored, because nothing here
reads ``Authorization``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker, set_scope
from app.models import User
from app.services import auth as auth_service

SESSION_COOKIE = "metalmark_session"
#: ASGI scope key for an in-process agent sub-request (see the module docstring).
AGENT_GRANT_KEY = "metalmark.agent_grant"
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
    metalmark_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    """Yield an authenticated, tenant-scoped context for a request.

    Raises 401 if unauthenticated, 403 if the user has no household yet.
    """
    grant = request.scope.get(AGENT_GRANT_KEY)
    if grant is not None:
        # A context manager, not a nested generator, so an exception raised by
        # the handler reaches the transaction and rolls it back.
        async with _agent_context(request, grant) as ctx:
            yield ctx
        return

    if not metalmark_session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        resolved = await auth_service.resolve_session(session, metalmark_session)
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


@asynccontextmanager
async def _agent_context(request: Request, grant) -> AsyncIterator[RequestContext]:
    """The context for an agent sub-request: the issuer's household and role, in a
    transaction the database itself refuses to write in."""
    if request.method not in ("GET", "HEAD"):
        raise HTTPException(status_code=405, detail="Agent access is read-only")
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        # First statement of the transaction, as Postgres requires. From here a
        # write fails in the database, whatever the route below tries to do.
        await session.execute(text("SET TRANSACTION READ ONLY"))
        user = await session.get(User, grant.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        await set_scope(session, household_id=grant.household_id, user_id=user.id)
        yield RequestContext(
            session=session,
            user=user,
            household_id=grant.household_id,
            role=grant.role,
            csrf_token="",
        )


def _check_csrf(request: Request, ctx: RequestContext) -> None:
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER)
        if not header or header != ctx.csrf_token:
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def require_owner(ctx: RequestContext = Depends(get_context)) -> RequestContext:
    if ctx.role != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
    return ctx


def require_admin(ctx: RequestContext = Depends(get_context)) -> RequestContext:
    """Administrators, and the household's owner (who holds every other
    owner-only control already). Checked on the server: the Admin page being
    hidden from a member is a convenience, not the control."""
    if not (ctx.user.is_admin or ctx.role == "owner"):
        raise HTTPException(status_code=403, detail="Administrator or owner role required")
    return ctx
