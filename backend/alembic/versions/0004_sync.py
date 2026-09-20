"""sync: jobs queue, runs, run events, and the control-panel knobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20

Adds ``sync_jobs`` (the claimable queue), ``sync_runs`` (one execution's counts and
timings) and ``sync_run_events`` (ordered, sanitized run logs) — all household-scoped
and RLS-protected like every other household table (ADR-0014/0025). Also adds the
sync cadence knobs to ``account_connections`` and ``pending_since`` to
``transactions``.

**This migration must be shape-detecting**, for the reason 0003 documents and now
for the second time: 0001 builds the schema with ``Base.metadata.create_all`` from
the *live* ORM metadata, so on a database created after this change 0001 already
produces all three tables and the migrated path is pure policy work. 0001's frozen
``HOUSEHOLD_TABLES`` list does not know these names, so it enables no policy for
them and this file must.

Supported: ``upgrade head`` from empty, and from a data-bearing 0003.

Known residual debt, now compounding rather than merely inherited: because 0001
derives its DDL from live metadata, a database created by the *new* metadata is
granted DML on these three tables by 0001 before this file adds their policies.
That window is inside a single ``upgrade head`` on an empty database, which is why
it is accepted here as it was in 0003 and 0002. Freezing 0001 to a literal DDL
snapshot is the real fix; it was deferred in ADR-0026 and is now two migrations
overdue, because 0005 will inherit it a third time.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.models.ledger import (
    SYNC_INTERVAL_DEFAULT_MINUTES,
    SYNC_INTERVAL_MAX_MINUTES,
    SYNC_INTERVAL_MIN_MINUTES,
)
from app.settings import get_settings

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Repeated verbatim from 0001/0002/0003 rather than imported: a migration has to
# keep meaning what it meant on the day it ran, so it must not follow a future edit
# to a shared constant.
HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

SYNC_TABLES = ("sync_jobs", "sync_runs", "sync_run_events")

# Columns this migration owns on pre-existing tables. ``ADD COLUMN IF NOT EXISTS``
# is self-guarding, so no probe is needed; ``NOT NULL DEFAULT`` backfills existing
# rows in the same statement and is safe because both defaults are constants.
CONNECTION_COLUMNS = (
    ("is_enabled", "boolean NOT NULL DEFAULT true"),
    ("sync_interval_minutes", f"integer NOT NULL DEFAULT {SYNC_INTERVAL_DEFAULT_MINUTES}"),
    ("next_sync_at", "timestamptz NULL"),
)
TRANSACTION_COLUMNS = (("pending_since", "timestamptz NULL"),)

# The added columns whose ``DEFAULT`` is temporary — it exists only to backfill the
# rows already in the table, and is dropped immediately afterwards.
#
# The ORM declares both with a Python-side ``default=`` and no ``server_default``,
# which is this repo's convention for nearly every column. So on the *fresh* path —
# where 0001's ``create_all`` builds the column from live metadata — the DDL carries
# no default at all, and leaving one here would make a migrated database and a fresh
# one disagree. ``alembic check`` cannot see that disagreement: autogenerate does not
# compare server defaults, so CI would stay green while the two paths drifted.
#
# Harmless today because the ORM always supplies the value, which is exactly why it
# would go unnoticed: the divergence would only surface in raw SQL, long after the
# migration that caused it is unread.
BACKFILL_DEFAULTS = ("is_enabled", "sync_interval_minutes")

PENDING_INDEX = "ix_transactions_household_pending_since"
INTERVAL_CONSTRAINT = "ck_account_connections_sync_interval_range"


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}
    ).scalar()


def _policy_exists(conn, table: str, name: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = :t AND policyname = :n
                """
            ),
            {"t": table, "n": name},
        ).scalar()
        is not None
    )


def _index_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = :n
                """
            ),
            {"n": name},
        ).scalar()
        is not None
    )


def _constraint_exists(conn, table: str, name: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema()
                  AND t.relname = :t AND c.conname = :n
                """
            ),
            {"t": table, "n": name},
        ).scalar()
        is not None
    )


def _create_sync_table(conn, name: str) -> None:
    """Create one sync table from the live metadata, exactly as create_all would.

    Going through the metadata (rather than a literal ``CREATE TABLE``) is what
    keeps the fresh-database path and the migrated path in agreement, which is what
    ``alembic check`` verifies. It is also why every index and constraint name must
    match the ORM exactly.
    """
    Base.metadata.tables[name].create(bind=conn)


def _secure_sync_table(conn, table: str, app_user: str) -> None:
    """RLS policy, then grants — never the other way round.

    ``ALTER DEFAULT PRIVILEGES`` from 0001 means a table created below is reachable
    by the app role the moment it exists. If the grant went first, a policy-less
    ``sync_jobs`` would be readable across households — and this table names the
    connections of every tenant, so that is the one leak that must not happen.
    """
    policy = f"{table}_household_isolation"
    if not _policy_exists(conn, table, policy):
        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"""
                CREATE POLICY {policy} ON {table}
                USING ({HOUSEHOLD_RLS_PREDICATE})
                WITH CHECK ({HOUSEHOLD_RLS_PREDICATE});
                """
            )
        )
    conn.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_user};")
    )


def _add_columns(conn, table: str, columns) -> None:
    for name, spec in columns:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {spec};"))
        if name in BACKFILL_DEFAULTS:
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP DEFAULT;"))


def upgrade() -> None:
    conn = op.get_bind()
    app_user = get_settings().app_db_user

    for table in SYNC_TABLES:
        if not _table_exists(conn, table):
            _create_sync_table(conn, table)

    _add_columns(conn, "account_connections", CONNECTION_COLUMNS)
    _add_columns(conn, "transactions", TRANSACTION_COLUMNS)

    # The cadence floor, enforced by the database so a direct write cannot bypass
    # the service-layer clamp and leave a connection whose cron hot-loops.
    if not _constraint_exists(conn, "account_connections", INTERVAL_CONSTRAINT):
        conn.execute(
            text(
                f"ALTER TABLE account_connections ADD CONSTRAINT {INTERVAL_CONSTRAINT} "
                f"CHECK (sync_interval_minutes BETWEEN {SYNC_INTERVAL_MIN_MINUTES}"
                f" AND {SYNC_INTERVAL_MAX_MINUTES});"
            )
        )

    if not _index_exists(conn, PENDING_INDEX):
        conn.execute(
            text(
                f"CREATE INDEX {PENDING_INDEX} ON transactions (household_id, pending_since) "
                "WHERE is_pending;"
            )
        )

    # Policies last: everything they protect must already exist.
    for table in SYNC_TABLES:
        _secure_sync_table(conn, table, app_user)


def downgrade() -> None:
    """Drop the sync tables and the columns this migration added. Lossy.

    The tables hold queue state and run history and nothing else references them, so
    backing out a bad deploy simply discards both; re-upgrading produces empty tables
    rather than the runs that were there. Note that dropping ``sync_jobs`` while a
    worker is live leaves that worker holding a claim on a table that no longer
    exists — stop the worker first, which is the same posture 0003 takes.
    """
    conn = op.get_bind()
    # Policies and grants go with the tables; nothing else to unwind.
    conn.execute(text("DROP TABLE IF EXISTS sync_run_events;"))
    conn.execute(text("DROP TABLE IF EXISTS sync_runs;"))
    conn.execute(text("DROP TABLE IF EXISTS sync_jobs;"))

    conn.execute(text(f"DROP INDEX IF EXISTS {PENDING_INDEX};"))
    conn.execute(
        text(f"ALTER TABLE account_connections DROP CONSTRAINT IF EXISTS {INTERVAL_CONSTRAINT};")
    )
    for table, columns in (
        ("account_connections", CONNECTION_COLUMNS),
        ("transactions", TRANSACTION_COLUMNS),
    ):
        for name, _ in columns:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name};"))
