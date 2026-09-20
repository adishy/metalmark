"""Test harness (WS-T).

Runs integration tests against a REAL Postgres, applies the Alembic migration
(schema + app role + RLS), and exposes fixtures that mirror production: the app
connects as the non-superuser RLS-bound role, scoped per household. Tests thus
exercise the SAME isolation path as the running app.

Two backends, chosen automatically:
  * **external** (default in Docker/CI): when ``METALMARK_TEST_PG_HOST`` is set,
    connect to that server as owner, (re)create a fresh ``metalmark_test`` DB, and
    migrate it. Used by ``docker compose`` on the same network as ``db``.
  * **testcontainers**: otherwise spin an ephemeral ``postgres:16`` container
    (host runs where the Docker socket + simple networking are available).

The database's name is the only thing isolating one run from another, so it is
settable — see ``METALMARK_TEST_DB`` and ``_test_db_name``.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

OWNER_USER = os.getenv("POSTGRES_USER", "metalmark")
OWNER_PW = os.getenv("POSTGRES_PASSWORD", "metalmark_dev_only_change_me")
APP_USER = "metalmark_app_test"
APP_PW = "metalmark_app_test_pw"


def _test_db_name(name: str) -> str:
    """The test database's name, guarded so it cannot be anything else.

    The harness **drops and recreates** this database before every session, so
    the one mistake worth making structurally impossible is pointing it at a
    real one. ``POSTGRES_DB`` names the *development* database (``metalmark``,
    see docker-compose.yml), and a typo that let the two meet would take a
    household's ledger with it. A name that does not end in ``_test`` is
    refused here, before anything connects.

    Overridable so two runs can proceed at once. With one shared name the second
    run drops the first's database mid-test, and the symptom is not a clear
    error — it is whichever run loses the race failing inexplicably, which reads
    as a flaky test and wastes an afternoon.

    Only the *database* is renamed. The app role (``metalmark_app_test``) is
    cluster-wide and deliberately shared: ``0001`` creates it behind a
    ``pg_roles`` existence check, so the second run's attempt is a no-op.
    """
    if not re.fullmatch(r"[a-z0-9_]*_test(_[a-z0-9_]+)?", name):
        raise RuntimeError(
            f"METALMARK_TEST_DB={name!r} does not look like a test database. "
            "The harness drops and recreates this database, so the name must end "
            "in '_test' (optionally with a '_suffix')."
        )
    return name


#: Override with ``METALMARK_TEST_DB`` to run a second suite concurrently; the
#: default is unchanged, so CI and the compose command need no edit.
TEST_DB = _test_db_name(os.getenv("METALMARK_TEST_DB", "metalmark_test"))


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
    external_host = os.getenv("METALMARK_TEST_PG_HOST")
    container = None

    if external_host:
        port = os.getenv("METALMARK_TEST_PG_PORT", "5432")
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
        METALMARK_ENV="test",
        METALMARK_LOG_LEVEL="WARNING",
        POSTGRES_HOST=host,
        POSTGRES_PORT=str(port),
        POSTGRES_DB=TEST_DB,
        POSTGRES_USER=OWNER_USER,
        POSTGRES_PASSWORD=OWNER_PW,
        APP_DB_USER=APP_USER,
        APP_DB_PASSWORD=APP_PW,
        METALMARK_DEFAULT_BASE_CURRENCY="USD",
        METALMARK_SECRET_KEY="test-secret-key-not-for-production-use",
    )

    # Reset cached settings + engine so they pick up the new env.
    import app.db as db
    from app.settings import get_settings

    get_settings.cache_clear()
    db._engine = None
    db._sessionmaker = None

    from alembic.config import Config

    from alembic import command

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
