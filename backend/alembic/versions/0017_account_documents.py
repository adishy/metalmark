"""account documents (ADR-0060)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-04

Account document storage; additive, shape-detecting, RLS before grant.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

TABLES = ("account_documents",)


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
    # Explicitly lossy for files added after upgrade; unrelated ledger rows survive.
    op.get_bind().execute(text("DROP TABLE IF EXISTS account_documents;"))
