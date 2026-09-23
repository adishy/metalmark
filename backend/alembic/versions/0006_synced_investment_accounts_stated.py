"""synced investment accounts with nothing to derive from become stated

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23

Sync typed any account whose payload carried holdings as ``investment`` with
``balance_source='derived'`` (ADR-0021's manual default) — but sync writes no
holdings, so the net-worth series valued those accounts from nothing: $0 on the
chart, the provider's balance on the Accounts page (session 04 audit, #2). Sync now
creates them ``stated``; this migration moves the existing ones across.

**Written for a database holding a household's real history**, so it is narrow
and exactly reversible:

* It touches only accounts that were ever synced (``external_key`` is set, and a
  disconnect keeps it), are ``derived``, and have **no** holding and **no**
  investment transaction. An account with any position a human entered is left
  derived — its history is its holdings', and that is still right.
* Each changed account also gets **one** snapshot from the balance it already
  shows (``current_balance`` at ``balance_date``), if it has none: sync never wrote
  snapshots for a derived account, and a stated account with no history would
  reappear on the chart only from its next sync — the first-snapshot cliff again.
* What it changed is recorded in ``migration_backup`` — a schema the app role has
  no grant on (``ALTER DEFAULT PRIVILEGES`` in 0001 covers ``public`` only), which
  ``alembic check`` does not compare. ``downgrade`` restores ``balance_source``
  from it and deletes exactly the snapshots it inserted, then drops its tables.

Data-only: no table in ``public`` is created or altered, so 0005's standing note
about 0001 building DDL from live metadata gains no new case here.
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

BACKUP_SCHEMA = "migration_backup"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {BACKUP_SCHEMA}"))
    conn.execute(text(f"REVOKE ALL ON SCHEMA {BACKUP_SCHEMA} FROM PUBLIC"))
    conn.execute(
        text(
            f"""
            CREATE TABLE {BACKUP_SCHEMA}.r0006_balance_source (
                account_id uuid PRIMARY KEY,
                balance_source varchar(10)
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE {BACKUP_SCHEMA}.r0006_seeded_snapshot (
                snapshot_id uuid PRIMARY KEY
            )
            """
        )
    )

    conn.execute(
        text(
            f"""
            INSERT INTO {BACKUP_SCHEMA}.r0006_balance_source (account_id, balance_source)
            SELECT a.id, a.balance_source
            FROM accounts a
            WHERE a.type = 'investment'
              AND a.balance_source = 'derived'
              AND a.external_key IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM holdings h WHERE h.account_id = a.id)
              AND NOT EXISTS (
                  SELECT 1 FROM investment_transactions t WHERE t.account_id = a.id
              )
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE accounts SET balance_source = 'stated'
            WHERE id IN (SELECT account_id FROM {BACKUP_SCHEMA}.r0006_balance_source)
            """
        )
    )
    conn.execute(
        text(
            f"""
            WITH seeded AS (
                INSERT INTO balance_snapshots
                    (household_id, account_id, balance_date, balance, currency)
                SELECT a.household_id, a.id, COALESCE(a.balance_date, CURRENT_DATE),
                       a.current_balance, a.currency
                FROM accounts a
                WHERE a.id IN (SELECT account_id FROM {BACKUP_SCHEMA}.r0006_balance_source)
                  AND a.current_balance IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM balance_snapshots s WHERE s.account_id = a.id
                  )
                RETURNING id
            )
            INSERT INTO {BACKUP_SCHEMA}.r0006_seeded_snapshot (snapshot_id)
            SELECT id FROM seeded
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            DELETE FROM balance_snapshots
            WHERE id IN (SELECT snapshot_id FROM {BACKUP_SCHEMA}.r0006_seeded_snapshot)
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE accounts a SET balance_source = b.balance_source
            FROM {BACKUP_SCHEMA}.r0006_balance_source b
            WHERE a.id = b.account_id
            """
        )
    )
    conn.execute(text(f"DROP TABLE {BACKUP_SCHEMA}.r0006_seeded_snapshot"))
    conn.execute(text(f"DROP TABLE {BACKUP_SCHEMA}.r0006_balance_source"))
    # Only if nothing else keeps a backup there — a later revision's tables are
    # that revision's to drop.
    remaining = conn.execute(
        text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = :s"
        ),
        {"s": BACKUP_SCHEMA},
    ).scalar()
    if remaining == 0:
        conn.execute(text(f"DROP SCHEMA {BACKUP_SCHEMA}"))
