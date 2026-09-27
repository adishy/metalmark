"""budgets (ADR-0058)

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26

One new household table: what the household plans to spend, per category, in one
calendar period. RLS-protected like every other household table (ADR-0014/0025).

**Shape-detecting**, like 0003/0010/0011/0013/0014: 0001 builds the schema from
live metadata, so a database created after this change already has the table and
the create below is guarded. The policy step is guarded too so ``upgrade head``
is a fixed point, and the policy is created **before** the grant — 0001's
``ALTER DEFAULT PRIVILEGES`` means a table is reachable by the app role the
instant it exists, so a policy-less table would be readable across households
for the width of this migration otherwise.

Nothing here reads or rewrites existing data. The table is new; the only existing
rows it touches are the ones it *points at*, and it points at them with
``ON DELETE CASCADE`` on ``category_id`` — deliberately, and unlike the
``SET NULL`` the transaction and recurring-series tables use, because the column
is ``NOT NULL`` and ``SET NULL`` on a ``NOT NULL`` column raises. The live-data
case in ``test_migrations_live_data.py`` walks a populated install through all of
it: the table arrives secure and the rest of the database is byte-identical, a
budget the API would write lands, deleting its category takes only the budget,
and the downgrade leaves the household's own rows alone.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

TABLES = ("budgets",)


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
    """Drop the table. Lossy, and clearly so: a budget is the household's own
    statement and there is nowhere to keep it once the table is gone. Nothing
    else in the schema references ``budgets``, so nothing else has to move —
    the foreign keys point *out* of it, not into it.

    A downgrade does not touch the categories or the ledger rows the plans were
    written against: dropping a plan is not a reason to change what was spent.
    """
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS budgets;"))
