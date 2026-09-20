"""initial schema + app role + RLS policies

Revision ID: 0001
Revises:
Create Date: 2026-09-19

Creates the Phase-0 core schema from the ORM metadata, provisions the
non-superuser application role, and enables Row-Level Security keyed on the
``app.household_id`` GUC (ADR-0014). Household-scoped tables fail closed when
the GUC is unset. Child tables (splits, tags) are scoped via an EXISTS
subquery against their RLS-protected parent.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables that carry a household_id column and are directly RLS-scoped.
HOUSEHOLD_TABLES = [
    "account_connections",
    "accounts",
    "category_groups",
    "categories",
    "tags",
    "transfer_groups",
    "transactions",
    "balance_snapshots",
]

# Child tables scoped via their parent (no household_id column).
CHILD_POLICIES = {
    "transaction_splits": "parent_txn_id",
    "transaction_tags": "transaction_id",
}

ALL_TABLES = list(Base.metadata.tables.keys())


def upgrade() -> None:
    conn = op.get_bind()
    settings = get_settings()

    # 1. Schema from metadata (baseline; later workstreams add incremental migrations).
    Base.metadata.create_all(bind=conn)

    # 2. Application role (idempotent). Migrations run as owner/superuser.
    app_user = settings.app_db_user
    app_pw = settings.app_db_password
    conn.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{app_user}') THEN
                    CREATE ROLE {app_user} LOGIN PASSWORD '{app_pw}';
                END IF;
            END
            $$;
            """
        )
    )
    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {app_user};"))
    conn.execute(
        text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_user};"
        )
    )
    conn.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user};"
        )
    )

    # 3. Row-Level Security on household-scoped tables.
    for tbl in HOUSEHOLD_TABLES:
        conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"""
                CREATE POLICY {tbl}_household_isolation ON {tbl}
                USING (
                    household_id = NULLIF(current_setting('app.household_id', true), '')::uuid
                )
                WITH CHECK (
                    household_id = NULLIF(current_setting('app.household_id', true), '')::uuid
                );
                """
            )
        )

    # 4. Child tables scoped via parent (parent's RLS naturally filters the EXISTS).
    for tbl, fk in CHILD_POLICIES.items():
        conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"""
                CREATE POLICY {tbl}_household_isolation ON {tbl}
                USING (EXISTS (SELECT 1 FROM transactions t WHERE t.id = {tbl}.{fk}))
                WITH CHECK (EXISTS (SELECT 1 FROM transactions t WHERE t.id = {tbl}.{fk}));
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in list(CHILD_POLICIES.keys()) + HOUSEHOLD_TABLES:
        conn.execute(text(f"DROP POLICY IF EXISTS {tbl}_household_isolation ON {tbl};"))
        conn.execute(text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;"))
    Base.metadata.drop_all(bind=conn)
