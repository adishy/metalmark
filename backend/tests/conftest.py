"""Test harness (WS-T).

Runs integration tests against a REAL Postgres, applies the Alembic migration
(schema + app role + RLS), and exposes fixtures that mirror production: the app
connects as the non-superuser RLS-bound role, scoped per household. Tests thus
exercise the SAME isolation path as the running app.

Two backends, chosen automatically:
  * **external** (default in Docker/CI): when ``KESTREL_TEST_PG_HOST`` is set,
    connect to that server as owner, (re)create a fresh ``kestrel_test`` DB, and
    migrate it. Used by ``docker compose`` on the same network as ``db``.
  * **testcontainers**: otherwise spin an ephemeral ``postgres:16`` container
    (host runs where the Docker socket + simple networking are available).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

OWNER_USER = os.getenv("POSTGRES_USER", "kestrel")
OWNER_PW = os.getenv("POSTGRES_PASSWORD", "kestrel_dev_only_change_me")
APP_USER = "kestrel_app_test"
APP_PW = "kestrel_app_test_pw"
TEST_DB = "kestrel_test"


def _recreate_external_db(host: str, port: str) -> None:
    """As owner, drop+create a clean test database and its app role state."""
    import psycopg

    admin_dsn = (
        f"host={host} port={port} dbname=postgres user={OWNER_USER} password={OWNER_PW}"
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        conn.execute(f'CREATE DATABASE "{TEST_DB}" OWNER "{OWNER_USER}"')


@pytest.fixture(scope="session", autouse=True)
def _configure():
    external_host = os.getenv("KESTREL_TEST_PG_HOST")
    container = None

    if external_host:
        port = os.getenv("KESTREL_TEST_PG_PORT", "5432")
        _recreate_external_db(external_host, port)
        host = external_host
    else:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer(
            "postgres:16", username=OWNER_USER, password=OWNER_PW, dbname=TEST_DB
        )
        container.start()
        host = container.get_container_host_ip()
        port = str(container.get_exposed_port(5432))

    os.environ.update(
        KESTREL_ENV="test",
        KESTREL_LOG_LEVEL="WARNING",
        POSTGRES_HOST=host,
        POSTGRES_PORT=str(port),
        POSTGRES_DB=TEST_DB,
        POSTGRES_USER=OWNER_USER,
        POSTGRES_PASSWORD=OWNER_PW,
        APP_DB_USER=APP_USER,
        APP_DB_PASSWORD=APP_PW,
        KESTREL_DEFAULT_BASE_CURRENCY="USD",
        KESTREL_SECRET_KEY="test-secret-key-not-for-production-use",
    )

    # Reset cached settings + engine so they pick up the new env.
    import app.db as db
    from app.settings import get_settings

    get_settings.cache_clear()
    db._engine = None
    db._sessionmaker = None

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    yield

    if container is not None:
        container.stop()


@pytest.fixture(autouse=True)
async def _fresh_engine():
    """Each test gets a fresh async engine bound to its own event loop
    (pytest-asyncio uses a new loop per test)."""
    import app.db as db

    db._engine = None
    db._sessionmaker = None
    yield
    if db._engine is not None:
        await db._engine.dispose()
    db._engine = None
    db._sessionmaker = None


@pytest.fixture
async def household_factory():
    """Create a household + owner and return its id (identity tables, unscoped)."""
    from app.db import unscoped_session
    from app.services import auth as svc

    async def _make(name: str = "H", base: str = "USD", email: str | None = None):
        email = email or f"{uuid.uuid4().hex[:8]}@example.com"
        async with unscoped_session() as session:
            household, _user = await svc.bootstrap_household(
                session,
                name=name,
                base_currency=base,
                owner_email=email,
                owner_name="Owner",
                owner_password="password123",
            )
            return household.id

    return _make
