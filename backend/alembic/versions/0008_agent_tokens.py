"""agent tokens: bearer credentials for the anonymized agent routes (ADR-0048)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

Adds ``agent_tokens``, an identity table like ``sessions``: it is read before a
household is known, so it has no ``household_id`` and no household RLS policy,
and ``test_rls_coverage`` has nothing to exempt.

**Additive only.** Nothing existing is read or rewritten, so it is safe on a
database holding a household's real history, and ``downgrade`` drops only the new
table (every token is lost, which is what revoking them all would do anyway).

Shape-detecting like 0002-0005: on a database created after this change 0001's
``create_all`` already builds the table from live metadata, so this file only
creates it when it is missing, then grants.
"""

from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLE = "agent_tokens"


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, TABLE):
        # From the live metadata, so the fresh and migrated paths agree on every
        # index and constraint name (what ``alembic check`` compares).
        Base.metadata.tables[TABLE].create(bind=conn)
    app_user = get_settings().app_db_user
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {app_user};"))


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
