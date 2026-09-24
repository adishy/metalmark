"""institution_logos: a household's logo for each institution it banks with

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24

Additive: one new household-scoped table, RLS-protected like every other
(ADR-0014/0025). Nothing existing is read or changed.

Shape-detecting, like 0003 and 0008: 0001 builds the schema from the live
metadata, so on a fresh database the table already exists by the time this runs,
and only the policy and grant are left to do. The policy goes on **before** the
grant, for 0003's reason — ``ALTER DEFAULT PRIVILEGES`` makes a new table
reachable by the app role the moment it exists.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLE = "institution_logos"
POLICY_NAME = f"{TABLE}_household_isolation"
HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)


def _table_exists(conn) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{TABLE}"}
    ).scalar()


def _policy_exists(conn) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM pg_policies WHERE schemaname = current_schema() "
                "AND tablename = :t AND policyname = :n"
            ),
            {"t": TABLE, "n": POLICY_NAME},
        ).scalar()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn):
        Base.metadata.tables[TABLE].create(bind=conn)
    if not _policy_exists(conn):
        conn.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"CREATE POLICY {POLICY_NAME} ON {TABLE} "
                f"USING ({HOUSEHOLD_RLS_PREDICATE}) WITH CHECK ({HOUSEHOLD_RLS_PREDICATE});"
            )
        )
    conn.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {get_settings().app_db_user};")
    )


def downgrade() -> None:
    """Drop the table: the logos go with it. They can be fetched or uploaded again."""
    op.get_bind().execute(text(f"DROP TABLE IF EXISTS {TABLE};"))
