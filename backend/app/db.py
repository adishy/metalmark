"""Database engine, session, and the mandatory tenant-scoping layer (ADR-0014).

Tenant isolation lives in **one place**, not per-handler. The app connects as a
non-superuser role that does NOT bypass RLS. Every household-scoped table has a
Row-Level Security policy keyed on the ``app.household_id`` GUC. Both the API
(per request) and the worker (per sync job) call :func:`scoped_session` to open a
transaction with that GUC set; an unscoped query sees **no** household rows
(fail-closed), because ``current_setting('app.household_id', true)`` is NULL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings

# Consistent constraint naming so Alembic autogenerate + RLS are predictable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().app_dsn,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def _set_scope(session: AsyncSession, household_id: uuid.UUID | None,
                     user_id: uuid.UUID | None) -> None:
    """Set transaction-local GUCs that RLS policies read (fail-closed)."""
    await session.execute(
        text("SELECT set_config('app.household_id', :hid, true)"),
        {"hid": str(household_id) if household_id else ""},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": str(user_id) if user_id else ""},
    )


@asynccontextmanager
async def scoped_session(
    household_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a session bound to a household for RLS. Commits on success.

    Used by the worker (per job) and by request dependencies (per request).
    """
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            await _set_scope(session, household_id, user_id)
            yield session


@asynccontextmanager
async def unscoped_session() -> AsyncIterator[AsyncSession]:
    """Session for identity/auth flows (users, sessions, invites) that run
    before a household context exists. These tables are protected at the
    application layer, not by household RLS."""
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            yield session
