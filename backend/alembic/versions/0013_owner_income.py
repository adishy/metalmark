"""owner_income_profiles, paystubs, paystub_lines (ADR-0052)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25

Three new household tables, RLS-protected like every other household table
(ADR-0014/0025) — the foundation for tax modelling, not wired into Insights yet.

**Shape-detecting**, like 0003/0010/0011: 0001 builds the schema from live
metadata, so a database created after this change already has all three
tables, and the creates below are guarded. The policy step is guarded too so
``upgrade head`` is a fixed point, and each policy is created **before** its
grant — 0001's ``ALTER DEFAULT PRIVILEGES`` means a table is reachable by the
app role the instant it exists, so a policy-less table would be readable
across households for the width of this migration otherwise.

Nothing here reads or rewrites existing data — the tables are new and nothing
references them yet — so there is no live-data backfill case, only the
create/drop shape test in ``test_migrations_live_data.py``.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

TABLES = ("owner_income_profiles", "paystubs", "paystub_lines")


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
    """Drop all three. Lossy, and clearly so — nothing else references this
    data (Insights does not read it yet), so there is nothing to preserve."""
    conn = op.get_bind()
    # Children first: paystub_lines -> paystubs, and owner_income_profiles is
    # independent of both. FKs would enforce this order anyway (ON DELETE
    # CASCADE from paystubs), but an explicit order keeps the intent readable.
    conn.execute(text("DROP TABLE IF EXISTS paystub_lines;"))
    conn.execute(text("DROP TABLE IF EXISTS paystubs;"))
    conn.execute(text("DROP TABLE IF EXISTS owner_income_profiles;"))
