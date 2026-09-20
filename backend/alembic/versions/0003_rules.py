"""rules: the priority-ordered conditions/actions table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20

Adds ``rules`` (ARCHITECTURE §2) — household-scoped, RLS-protected like every
other household table (ADR-0014/0025).

**This migration must be shape-detecting.** 0001 builds the schema with
``Base.metadata.create_all`` from the *live* ORM metadata, so on a database
created after this change 0001 already produces ``rules`` and this file has
nothing to create. A straight-line 0003 would then fail on every fresh database
(it is fresh for every test run). The create is therefore guarded, and the policy
step is guarded too so that ``upgrade head`` is a fixed point.

Supported: ``upgrade head`` from empty, and from a data-bearing 0002.

Known residual debt: because 0001 derives its DDL from live metadata rather than
a frozen literal snapshot, a database created by the *new* metadata is granted
DML on ``rules`` by 0001 before this file adds its policy (0001 sets
``ALTER DEFAULT PRIVILEGES``). 0001's ``HOUSEHOLD_TABLES`` list also does not
know about ``rules``, so it enables no policy for it. That window is inside a
single ``upgrade head`` command on an empty database, which is why it is
accepted; the on-disk path that carries real data creates the policy before the
grant. Freezing 0001 to literal DDL is the real fix and is deferred (ADR-0026).
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# The predicate is repeated verbatim from 0001/0002 rather than imported: a
# migration has to keep meaning what it meant on the day it ran, so it must not
# follow a future edit to a shared constant.
HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)

POLICY_NAME = "rules_household_isolation"


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


def _create_rules_table(conn) -> None:
    """Create ``rules`` from the live metadata, exactly as create_all would."""
    Base.metadata.tables["rules"].create(bind=conn)


def _secure_rules(conn, app_user: str) -> None:
    """RLS policy, then grants — never the other way round.

    ``ALTER DEFAULT PRIVILEGES`` from 0001 means a table created below is
    reachable by the app role the moment it exists. If the grant went first, a
    policy-less ``rules`` would be readable across households.
    """
    if not _policy_exists(conn, "rules", POLICY_NAME):
        conn.execute(text("ALTER TABLE rules ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"""
                CREATE POLICY {POLICY_NAME} ON rules
                USING ({HOUSEHOLD_RLS_PREDICATE})
                WITH CHECK ({HOUSEHOLD_RLS_PREDICATE});
                """
            )
        )
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON rules TO {app_user};"))


def upgrade() -> None:
    conn = op.get_bind()
    app_user = get_settings().app_db_user

    if not _table_exists(conn, "rules"):
        _create_rules_table(conn)

    _secure_rules(conn, app_user)


def downgrade() -> None:
    """Drop ``rules``. Lossy, and clearly so.

    The table holds user-authored rules and nothing else references it, so there
    is no data to move anywhere — the rules are simply gone, and re-upgrading
    produces an empty table rather than the rules that were there. Intended for
    backing out a bad deploy, not as a data-preserving round trip.
    """
    conn = op.get_bind()
    # The policy and the grants go with the table; nothing else to unwind.
    conn.execute(text("DROP TABLE IF EXISTS rules;"))
