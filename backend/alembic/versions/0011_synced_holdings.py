"""holdings.source: which positions sync wrote from the bank's holdings

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

ADR-0051 makes sync a writer of positions, so a ``holdings`` row now says who
wrote it. Additive: a new column whose default, ``manual``, is exactly what every
existing row is — before this revision nothing but a human wrote holdings. No
existing value is read or changed.

Shape-detecting, like 0010: 0001 builds the schema from the live metadata, so on a
fresh database the column is already there.

The downgrade removes the rows sync wrote, and the securities and prices only
those rows used. Without the column an older build would read them as a human's
positions, which nothing would ever update or remove again; the next sync after
re-upgrading writes them back from the bank.
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _has_column(conn) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND table_name = 'holdings' AND column_name = 'source'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn):
        return
    conn.execute(
        text(
            "ALTER TABLE holdings ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'manual', "
            "ADD CONSTRAINT ck_holdings_source_valid "
            "CHECK (source IN ('manual', 'simplefin'))"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn):
        return
    synced = (
        "SELECT security_id FROM holdings WHERE source = 'simplefin'"
    )
    # Only a security sync created (is_manual = false) and that nothing else — no
    # hand-entered position, no recorded trade — refers to once its synced rows go.
    conn.execute(
        text(
            f"""
            CREATE TEMP TABLE _synced_only ON COMMIT DROP AS
            SELECT s.id FROM securities s
            WHERE s.is_manual = false AND s.id IN ({synced})
              AND NOT EXISTS (SELECT 1 FROM holdings h
                              WHERE h.security_id = s.id AND h.source <> 'simplefin')
              AND NOT EXISTS (SELECT 1 FROM investment_transactions t
                              WHERE t.security_id = s.id)
            """
        )
    )
    conn.execute(text("DELETE FROM holdings WHERE source = 'simplefin'"))
    conn.execute(text("DELETE FROM securities WHERE id IN (SELECT id FROM _synced_only)"))
    conn.execute(
        text(
            "ALTER TABLE holdings DROP CONSTRAINT IF EXISTS ck_holdings_source_valid, "
            "DROP COLUMN source"
        )
    )
