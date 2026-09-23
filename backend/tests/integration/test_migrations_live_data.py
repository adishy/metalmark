"""Data migrations, run against a database that already holds a household.

The rest of the suite migrates an *empty* database to head once per session, which
proves the schema and says nothing about what a migration does to rows a
household has spent years entering. A data migration is only as good as its
behaviour on those rows, so each one here is exercised the way it meets a real
install: a scratch database is migrated to the revision before it, filled with the
shapes of data it must change **and the shapes it must leave alone**, upgraded,
checked, downgraded — and then compared, row for row, with what it held before.

Alembic runs in a subprocess so the scratch database cannot disturb the session's
own (settings and engines are process-cached).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from tests.conftest import OWNER_PW, OWNER_USER, TEST_DB

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRATCH_DB = f"{TEST_DB}_migrations"

#: Every row of these, in a stable order, is what "unchanged" means.
FINGERPRINT_QUERIES = {
    "accounts": "SELECT * FROM accounts ORDER BY id",
    "balance_snapshots": "SELECT * FROM balance_snapshots ORDER BY id",
}


def _dsn(db: str) -> str:
    return (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={db} user={OWNER_USER} password={OWNER_PW}"
    )


@pytest.fixture
def scratch_db():
    with psycopg.connect(_dsn("postgres"), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        admin.execute(f'CREATE DATABASE "{SCRATCH_DB}" OWNER "{OWNER_USER}"')
    yield SCRATCH_DB
    with psycopg.connect(_dsn("postgres"), autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (SCRATCH_DB,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


def _alembic(db: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "POSTGRES_DB": db},
        check=True,
        capture_output=True,
    )


def _fingerprint(conn) -> dict[str, list[tuple]]:
    return {name: conn.execute(q).fetchall() for name, q in FINGERPRINT_QUERIES.items()}


def _household(conn) -> tuple[uuid.UUID, uuid.UUID]:
    hid = conn.execute(
        "INSERT INTO households (name, base_currency, timezone) "
        "VALUES ('Home', 'USD', 'UTC') RETURNING id"
    ).fetchone()[0]
    owner = conn.execute(
        "INSERT INTO owners (household_id, name, kind, sort) "
        "VALUES (%s, 'Shared', 'shared', 0) RETURNING id",
        (hid,),
    ).fetchone()[0]
    return hid, owner


def _account(conn, hid, owner, name, *, type_="investment", source="derived",
             synced=True, balance="0", balance_date="2026-09-20", is_asset=True):
    return conn.execute(
        "INSERT INTO accounts (household_id, name, type, currency, current_balance, "
        "balance_date, is_asset, owner_id, is_manual, is_hidden, balance_source, "
        "external_key, external_id) "
        "VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, %s, false, %s, %s, %s) RETURNING id",
        (hid, name, type_, balance, balance_date, is_asset, owner, not synced, source,
         f"bank:{name.lower()}" if synced else None, f"ACT-{name}" if synced else None),
    ).fetchone()[0]


def _snapshot(conn, hid, account_id, day, balance):
    conn.execute(
        "INSERT INTO balance_snapshots (household_id, account_id, balance_date, balance, "
        "currency) VALUES (%s, %s, %s, %s, 'USD')",
        (hid, account_id, day, balance),
    )


def _source(conn, account_id) -> str | None:
    return conn.execute(
        "SELECT balance_source FROM accounts WHERE id = %s", (account_id,)
    ).fetchone()[0]


def _snapshots(conn, account_id) -> list[tuple]:
    return conn.execute(
        "SELECT balance_date::text, balance::text FROM balance_snapshots "
        "WHERE account_id = %s ORDER BY balance_date",
        (account_id,),
    ).fetchall()


# ---- 0006: synced investment accounts with nothing to derive from ------------


def test_0006_moves_only_synced_positionless_accounts_and_reverses_exactly(scratch_db):
    _alembic(scratch_db, "upgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        hid, owner = _household(conn)
        # Changed: synced, derived, nothing to derive from, never snapshotted.
        bare = _account(conn, hid, owner, "Savings", balance="114685.51")
        # Changed, but it already has history — nothing is seeded over it.
        with_history = _account(conn, hid, owner, "Brokerage", balance="5000")
        _snapshot(conn, hid, with_history, "2026-09-01", "4800")
        # Left alone: a human entered a position, so its history is its holdings'.
        held = _account(conn, hid, owner, "IRA", balance="20000")
        security = conn.execute(
            "INSERT INTO securities (household_id, name, security_type, currency, "
            "is_manual) VALUES (%s, 'VTI', 'etf', 'USD', true) RETURNING id",
            (hid,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO holdings (household_id, account_id, security_id, quantity) "
            "VALUES (%s, %s, %s, 10)",
            (hid, held, security),
        )
        # Left alone: never synced — ADR-0021's manual default stands.
        manual = _account(conn, hid, owner, "401k", synced=False, balance="80000")
        # Left alone: not an investment account at all.
        checking = _account(conn, hid, owner, "Checking", type_="depository", source=None,
                            balance="2500")
        before = _fingerprint(conn)

    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _source(conn, bare) == "stated"
        assert _snapshots(conn, bare) == [("2026-09-20", "114685.5100")]
        assert _source(conn, with_history) == "stated"
        assert _snapshots(conn, with_history) == [("2026-09-01", "4800.0000")]
        assert _source(conn, held) == "derived"
        assert _source(conn, manual) == "derived"
        assert _source(conn, checking) is None

    _alembic(scratch_db, "downgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _fingerprint(conn) == before
        assert conn.execute(
            "SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = 'migration_backup'"
        ).fetchone() is None


def test_0006_backup_is_out_of_the_app_roles_reach(scratch_db):
    """The backup names accounts across households, so the RLS-bound role must not
    be able to read it at all."""
    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        app_user = os.environ["APP_DB_USER"]
        can_use = conn.execute(
            "SELECT has_schema_privilege(%s, 'migration_backup', 'USAGE')", (app_user,)
        ).fetchone()[0]
    assert can_use is False
