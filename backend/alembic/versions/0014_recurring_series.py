"""recurring_series (ADR-0053)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25

One new household table: what the household states happens regularly, and what
the detector's suggestions become once someone accepts one. RLS-protected like
every other household table (ADR-0014/0025).

**Shape-detecting**, like 0003/0010/0011/0013: 0001 builds the schema from live
metadata, so a database created after this change already has the table and the
create below is guarded. The policy step is guarded too so ``upgrade head`` is a
fixed point, and the policy is created **before** the grant — 0001's ``ALTER
DEFAULT PRIVILEGES`` means a table is reachable by the app role the instant it
exists, so a policy-less table would be readable across households for the width
of this migration otherwise.

Nothing here reads or rewrites existing data: the table is new, it references
existing rows (account, category, transaction) only through ``SET NULL``
foreign keys, and no migration of ledger rows is needed or wanted — a series is
derived from the ledger at read time (ADR-0053), never written back into it. So
the live-data case in ``test_migrations_live_data.py`` proves the shape a
populated install gets: the table arrives secure, the rest of the database is
untouched, and the ``SET NULL`` keys behave (deleting the transaction a series
was picked from does not delete the series).
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

TABLES = ("recurring_series",)


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


def _secure(conn, table: str, app_user: str) -> None:
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
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_user};"))


def upgrade() -> None:
    conn = op.get_bind()
    app_user = get_settings().app_db_user

    for table in TABLES:
        if not _table_exists(conn, table):
            Base.metadata.tables[table].create(bind=conn)
        _secure(conn, table, app_user)


def downgrade() -> None:
    """Drop the table. Lossy, and it says so: the *series* is what goes — the
    transactions a series described are ledger rows in ``transactions`` and are
    not touched, which is the same promise deleting one makes (ADR-0053). What
    a person loses is the list itself, so this is a downgrade, not a cleanup."""
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS recurring_series;"))
