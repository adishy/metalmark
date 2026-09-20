"""investments: securities, prices, holdings, investment transactions

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20

Adds the four investment tables specified by ADR-0011/0020/0021 and refined by
ADR-0032/0033, all household-scoped and RLS-protected like every other household
table (ADR-0014/0025).

**Shape-detecting**, for the third time and now as a standing cost: 0001 builds
the schema with ``Base.metadata.create_all`` from the *live* ORM metadata, so on a
database created after this change 0001 already produces all four tables and this
file is pure policy work. 0001's frozen ``HOUSEHOLD_TABLES`` list does not know
these names, so it enables no policy for them and this file must.

The residual debt 0003 accepted and 0004 flagged as compounding is now inherited a
third time: because 0001 derives its DDL from live metadata, a database created by
the *new* metadata is granted DML on these four tables by 0001 before this file
adds their policies. That window is inside a single ``upgrade head`` on an empty
database. Freezing 0001 to a literal DDL snapshot is the real fix. This migration
does not make it worse — it adds no new kind of debt — but it is the fourth
migration to inherit it, and the argument for deferring it again is now only that
no migration is the right place to do it. If a 0006 is ever needed, do it first.

Supported: ``upgrade head`` from empty, and from a data-bearing 0004.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Repeated verbatim from 0001-0004 rather than imported: a migration has to keep
# meaning what it meant on the day it ran, so it must not follow a future edit to
# a shared constant.
HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

# Dependency order, so each table's foreign keys point at one that already exists.
# ``holdings`` is created after ``securities`` for that reason and is not itself a
# dependency of anything here.
INVESTMENT_TABLES = (
    "securities",
    "security_prices",
    "holdings",
    "investment_transactions",
)


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


def _create_investment_table(conn, name: str) -> None:
    """Create one table from the live metadata, exactly as ``create_all`` would.

    Going through the metadata (rather than a literal ``CREATE TABLE``) is what
    keeps the fresh-database path and the migrated path in agreement, which is what
    ``alembic check`` verifies. It is also why every index and constraint name must
    match the ORM exactly — including the two partial unique indexes on
    ``investment_transactions``, whose names are given explicitly in the model
    precisely so they cannot drift.
    """
    Base.metadata.tables[name].create(bind=conn)


def _secure_investment_table(conn, table: str, app_user: str) -> None:
    """RLS policy, then grants — never the other way round.

    ``ALTER DEFAULT PRIVILEGES`` from 0001 means a table created below is reachable
    by the app role the moment it exists. If the grant went first, a policy-less
    ``securities`` would be readable across households — and since every household's
    securities carry names and tickers, that is a leak of what each household owns.
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


def upgrade() -> None:
    conn = op.get_bind()
    app_user = get_settings().app_db_user

    for table in INVESTMENT_TABLES:
        if not _table_exists(conn, table):
            _create_investment_table(conn, table)

    # Policies last: everything they protect must already exist.
    for table in INVESTMENT_TABLES:
        _secure_investment_table(conn, table, app_user)


def downgrade() -> None:
    """Drop the four investment tables. Lossy, and knowingly so.

    Nothing outside these tables references them, so backing out a bad deploy
    discards every price, position and trade the household entered and re-upgrading
    produces empty tables. Unlike 0004's sync state this is *user-authored financial
    history*, not regenerable queue state, and it is the one thing in the schema a
    household cannot re-derive from a provider — SimpleFIN has no holdings at all
    (ADR-0011). Prefer restoring a backup over running this.

    Note also that it takes the *cash holding* with it, which is what makes a
    `derived` investment balance nonzero (ADR-0033 §4) — after a downgrade those
    accounts read zero, not their pre-investments balance.
    """
    conn = op.get_bind()
    # Reverse dependency order. Policies and grants go with the tables.
    for table in reversed(INVESTMENT_TABLES):
        conn.execute(text(f"DROP TABLE IF EXISTS {table};"))
