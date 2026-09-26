"""account_connections.display_name: a local name the owner gives a connection

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25

The bank's ``org_name`` is what the provider calls itself, and a household with
two connections to the same bank (a personal and a joint claim, say) has no way
to tell them apart in the UI beyond that one shared string. This adds a nullable
``display_name`` the owner can set; NULL means "use the bank's name", which is
also every existing row's state, so this is purely additive — no value is read
or rewritten.

Shape-detecting like 0010/0011: 0001 builds the schema from live metadata, so a
database created after this change already has the column.
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _has_column(conn) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND table_name = 'account_connections' "
                "AND column_name = 'display_name'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn):
        return
    conn.execute(
        text("ALTER TABLE account_connections ADD COLUMN display_name VARCHAR(100)")
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn):
        return
    conn.execute(text("ALTER TABLE account_connections DROP COLUMN display_name"))
