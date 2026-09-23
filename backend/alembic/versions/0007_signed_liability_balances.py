"""liability balances are signed: debt is negative (ADR-0043)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23

Before ADR-0043 a card or loan's balance meant two things: the manual form stored
the amount owed (positive), SimpleFIN and OFX the signed balance (negative). The
report subtracted liabilities *as well*, so a synced card counted in the household's
favour and every payoff dropped the net-worth line by twice the balance (session 04
audit, #1). The readers now add signed balances; this brings stored rows into that
convention.

**Written for a database holding a household's real history.** No column records
which writer produced a row, so the rule is per account, from its own rows
(``services/balance_sign.py`` states it in full; this is a frozen copy, and
``test_migrations_live_data.py`` holds the two to the same answers):

* a liability never synced (no ``external_key``) — every positive balance was typed
  as an amount owed, and is negated;
* a synced liability with any negative balance — the provider's sign is evident,
  so its positives are hand edits, and are negated;
* a synced liability never negative — no evidence either way; left untouched (sync
  now warns when a liability reports a positive balance).

``available_balance`` is not touched: for a card, SimpleFIN reports it as the
available credit, which is positive in both conventions.

Every value changed is recorded in ``migration_backup`` (out of the app role's
reach — see 0006). ``downgrade`` restores a value **only where it still holds what
this migration wrote**: a balance sync or a human has changed since was written in
the signed convention on purpose, and reverting it would lose it.

Data-only; nothing in ``public`` is created or altered.
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

BACKUP_SCHEMA = "migration_backup"

#: The accounts whose positive balances were stored as an amount owed.
TARGETS = """
    SELECT a.id FROM accounts a
    WHERE a.type IN ('credit', 'loan')
      AND (
          a.current_balance > 0
          OR EXISTS (SELECT 1 FROM balance_snapshots s
                     WHERE s.account_id = a.id AND s.balance > 0)
      )
      AND (
          a.external_key IS NULL
          OR a.current_balance < 0
          OR EXISTS (SELECT 1 FROM balance_snapshots s
                     WHERE s.account_id = a.id AND s.balance < 0)
      )
"""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {BACKUP_SCHEMA}"))
    conn.execute(text(f"REVOKE ALL ON SCHEMA {BACKUP_SCHEMA} FROM PUBLIC"))
    conn.execute(
        text(
            f"""
            CREATE TABLE {BACKUP_SCHEMA}.r0007_account_balance (
                account_id uuid PRIMARY KEY,
                current_balance numeric(19, 4) NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE {BACKUP_SCHEMA}.r0007_snapshot_balance (
                snapshot_id uuid PRIMARY KEY,
                balance numeric(19, 4) NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {BACKUP_SCHEMA}.r0007_account_balance (account_id, current_balance)
            SELECT id, current_balance FROM accounts
            WHERE id IN ({TARGETS}) AND current_balance > 0
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {BACKUP_SCHEMA}.r0007_snapshot_balance (snapshot_id, balance)
            SELECT id, balance FROM balance_snapshots
            WHERE account_id IN ({TARGETS}) AND balance > 0
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE accounts a SET current_balance = -b.current_balance
            FROM {BACKUP_SCHEMA}.r0007_account_balance b
            WHERE a.id = b.account_id
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE balance_snapshots s SET balance = -b.balance
            FROM {BACKUP_SCHEMA}.r0007_snapshot_balance b
            WHERE s.id = b.snapshot_id
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            UPDATE accounts a SET current_balance = b.current_balance
            FROM {BACKUP_SCHEMA}.r0007_account_balance b
            WHERE a.id = b.account_id AND a.current_balance = -b.current_balance
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE balance_snapshots s SET balance = b.balance
            FROM {BACKUP_SCHEMA}.r0007_snapshot_balance b
            WHERE s.id = b.snapshot_id AND s.balance = -b.balance
            """
        )
    )
    conn.execute(text(f"DROP TABLE {BACKUP_SCHEMA}.r0007_snapshot_balance"))
    conn.execute(text(f"DROP TABLE {BACKUP_SCHEMA}.r0007_account_balance"))
    remaining = conn.execute(
        text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :s"),
        {"s": BACKUP_SCHEMA},
    ).scalar()
    if remaining == 0:
        conn.execute(text(f"DROP SCHEMA {BACKUP_SCHEMA}"))
