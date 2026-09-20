"""owners become household data, not users; invites drop; open signup

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20

Replaces per-user attribution (``*.owner_user_id`` → ``users.id``) with
household-scoped owner rows (``*.owner_id`` → ``owners.id``, ADR-0026), and
drops ``invites`` (open signup, ADR-0027) with it.

**This migration must be shape-detecting.** 0001 builds the schema with
``Base.metadata.create_all`` from the *live* ORM metadata, so on a database
created after this change 0001 already produces ````owners`` and
``owner_id`` and never produces ``owner_user_id``/``invites``. A straight-line
0002 would then fail on every fresh database (it is fresh for every test run).
So each step is guarded and the backfill runs only when the legacy columns are
actually present.

Supported: ``upgrade head`` from empty, and from a data-bearing 0001. Stamping
an intermediate revision is not supported — there is nothing between 0001 and
this file to stamp.

Known residual debt: because 0001 derives its DDL from live metadata rather than
a frozen literal snapshot, a database created by the *new* metadata is granted
DML on ``owners`` by 0001 before this file adds its policy (0001 sets
``ALTER DEFAULT PRIVILEGES``). That window is inside a single ``upgrade head``
command on an empty database, which is why it is accepted; the on-disk path that
carries real data creates the policy before the grant. Freezing 0001 to literal
DDL is the real fix and is deferred.
"""
from __future__ import annotations

from sqlalchemy import text

import app.models  # noqa: F401  (populate metadata)
from alembic import op
from app.db import Base
from app.settings import get_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SHARED_OWNER_NAME = "Shared"

# (index name, table, definition) — names come from the model's naming convention
# and must match what create_all produces, or migrated and fresh databases diverge
# for every future autogenerate.
OWNER_INDEXES = [
    ("ix_accounts_household_id_owner_id", "accounts", "(household_id, owner_id)"),
    ("ix_transactions_household_id_owner_id", "transactions", "(household_id, owner_id)"),
    (
        "ix_transaction_splits_parent_txn_id_owner_id",
        "transaction_splits",
        "(parent_txn_id, owner_id)",
    ),
]

ATTRIBUTION_TABLES = ("accounts", "transactions", "transaction_splits")

HOUSEHOLD_RLS_PREDICATE = (
    "household_id = NULLIF(current_setting('app.household_id', true), '')::uuid"
)


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}
    ).scalar()


def _column_exists(conn, table: str, column: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :t AND column_name = :c
                """
            ),
            {"t": table, "c": column},
        ).scalar()
        is not None
    )


def _constraint_exists(conn, table: str, name: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema()
                  AND t.relname = :t AND c.conname = :n
                """
            ),
            {"t": table, "n": name},
        ).scalar()
        is not None
    )


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


def _create_owners_table(conn) -> None:
    """Create ``owners`` from the live metadata, exactly as create_all would."""
    Base.metadata.tables["owners"].create(bind=conn)


def _secure_owners(conn, app_user: str) -> None:
    """RLS policy, then grants — never the other way round.

    ``ALTER DEFAULT PRIVILEGES`` from 0001 means a table created below is
    reachable by the app role the moment it exists. If the grant went first, a
    policy-less ``owners`` would be readable across households.
    """
    if not _policy_exists(conn, "owners", "owners_household_isolation"):
        conn.execute(text("ALTER TABLE owners ENABLE ROW LEVEL SECURITY;"))
        conn.execute(
            text(
                f"""
                CREATE POLICY owners_household_isolation ON owners
                USING ({HOUSEHOLD_RLS_PREDICATE})
                WITH CHECK ({HOUSEHOLD_RLS_PREDICATE});
                """
            )
        )
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON owners TO {app_user};"))


def _add_attribution_columns(conn) -> None:
    for table in ATTRIBUTION_TABLES:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS owner_id uuid;"))


def _backfill_from_users(conn) -> None:
    """Map the old per-user attribution onto owner rows.

    Through a temp mapping table rather than name matching against ``owners``
    directly: display names collide, ``min()`` has to pick one spelling, and a
    user's ``owner_user_id`` may be NULL — the old "joint" meaning, which maps to
    the household's Shared owner rather than to nothing.
    """
    conn.execute(
        text(
            """
            INSERT INTO owners (household_id, name, kind, sort)
            SELECT h.id, :shared, 'shared', 0 FROM households h
            ON CONFLICT DO NOTHING;
            """
        ),
        {"shared": SHARED_OWNER_NAME},
    )
    # A user whose display name is literally "Shared" cannot get a person owner of
    # that name (names are unique per household, case-insensitively) and lands on
    # the Shared owner below. Degrading to Shared is the same thing that happens to
    # an unattributed account, and the SET NOT NULL assert still holds.
    conn.execute(
        text(
            """
            INSERT INTO owners (household_id, name, kind, sort)
            SELECT hm.household_id, min(u.display_name), 'person', 1
            FROM household_members hm
            JOIN users u ON u.id = hm.user_id
            GROUP BY hm.household_id, lower(u.display_name)
            ON CONFLICT DO NOTHING;
            """
        )
    )

    conn.execute(text("DROP TABLE IF EXISTS _mm_user_owner;"))
    conn.execute(
        text(
            """
            CREATE TEMP TABLE _mm_user_owner (user_id uuid PRIMARY KEY, owner_id uuid NOT NULL);
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO _mm_user_owner (user_id, owner_id)
            SELECT u.id, o.id
            FROM users u
            JOIN household_members hm ON hm.user_id = u.id
            JOIN owners o
              ON o.household_id = hm.household_id
             AND o.kind = 'person'
             AND lower(o.name) = lower(u.display_name);
            """
        )
    )

    conn.execute(
        text(
            """
            UPDATE accounts a
            SET owner_id = COALESCE(
                (SELECT uo.owner_id FROM _mm_user_owner uo WHERE uo.user_id = a.owner_user_id),
                (SELECT o.id FROM owners o
                  WHERE o.household_id = a.household_id AND o.kind = 'shared')
            )
            WHERE a.owner_id IS NULL;
            """
        )
    )
    # Transactions and splits keep NULL for an unset owner: that is the inheritance
    # chain, and it reproduces the old "joint" semantics exactly (ADR-0026).
    for table in ("transactions", "transaction_splits"):
        conn.execute(
            text(
                f"""
                UPDATE {table} x
                SET owner_id = (
                    SELECT uo.owner_id FROM _mm_user_owner uo WHERE uo.user_id = x.owner_user_id
                )
                WHERE x.owner_id IS NULL AND x.owner_user_id IS NOT NULL;
                """
            )
        )
    conn.execute(text("DROP TABLE _mm_user_owner;"))


def _add_foreign_keys(conn) -> None:
    for table in ATTRIBUTION_TABLES:
        name = f"fk_{table}_owner_id_owners"
        if not _constraint_exists(conn, table, name):
            # No ON DELETE action, deliberately (ADR-0026): SET NULL would silently
            # turn an explicit attribution into inheritance, and the service
            # reassigns before deleting an owner.
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                    f"FOREIGN KEY (owner_id) REFERENCES owners(id);"
                )
            )


def _require_accounts_have_owners(conn) -> None:
    nulls = conn.execute(text("SELECT count(*) FROM accounts WHERE owner_id IS NULL")).scalar()
    if nulls:
        raise RuntimeError(
            f"refusing to set accounts.owner_id NOT NULL: {nulls} account(s) would be left "
            "with no owner. The backfill has a gap; fix it before upgrading."
        )


def upgrade() -> None:
    conn = op.get_bind()
    app_user = get_settings().app_db_user

    # 1. The owners table itself.
    if not _table_exists(conn, "owners"):
        _create_owners_table(conn)

    # 2. Policy before grants (see _secure_owners).
    _secure_owners(conn, app_user)

    # 3. Attribution columns, and the legacy backfill if there is anything to move.
    legacy = _column_exists(conn, "accounts", "owner_user_id")
    _add_attribution_columns(conn)
    if legacy:
        _backfill_from_users(conn)

    # 4. Constraints and indexes.
    _add_foreign_keys(conn)
    _require_accounts_have_owners(conn)
    conn.execute(text("ALTER TABLE accounts ALTER COLUMN owner_id SET NOT NULL;"))
    for name, table, definition in OWNER_INDEXES:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {definition};"))

    # 5. Retire the old shape.
    if legacy:
        for table in ("transaction_splits", "transactions", "accounts"):
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS owner_user_id;"))
    # Superseded by (parent_txn_id, owner_id), which covers the same prefix.
    conn.execute(text("DROP INDEX IF EXISTS public.ix_transaction_splits_parent_txn_id;"))

    if _table_exists(conn, "invites"):
        conn.execute(text("DROP TABLE invites;"))


def downgrade() -> None:
    """Reverse 0002. Lossy, and clearly so.

    ``owners`` has no pre-0002 representation except a user, so a person owner maps
    back only by display-name match against the household's members; anything that
    does not match — the Shared owner above all — becomes NULL, the old "joint".
    Owner rows themselves are dropped. Intended for the upgrade→downgrade→upgrade
    rehearsal and for backing out a bad deploy, not as a data-preserving round trip.

    ``invites`` is not recreated: nothing in the codebase can consume it any more,
    and a fresh database under this revision never had it either.
    """
    conn = op.get_bind()

    conn.execute(text("DROP TABLE IF EXISTS _mm_owner_user;"))
    conn.execute(
        text(
            """
            CREATE TEMP TABLE _mm_owner_user (owner_id uuid PRIMARY KEY, user_id uuid);
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO _mm_owner_user (owner_id, user_id)
            SELECT DISTINCT ON (o.id) o.id, u.id
            FROM owners o
            JOIN household_members hm ON hm.household_id = o.household_id
            JOIN users u ON u.id = hm.user_id AND lower(u.display_name) = lower(o.name)
            -- Two members may share a display name; pick the one that got there
            -- first, and break the tie by id so the choice is not arbitrary.
            -- (No min(uuid) in Postgres — there is no ordering aggregate for uuid.)
            ORDER BY o.id, u.created_at, u.id;
            """
        )
    )

    for table in ("transaction_splits", "transactions", "accounts"):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS owner_user_id uuid;"))
        conn.execute(
            text(
                f"""
                UPDATE {table} x
                SET owner_user_id = (
                    SELECT ou.user_id FROM _mm_owner_user ou WHERE ou.owner_id = x.owner_id
                )
                WHERE x.owner_id IS NOT NULL;
                """
            )
        )
        name = f"fk_{table}_owner_user_id_users"
        if not _constraint_exists(conn, table, name):
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                    f"FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;"
                )
            )
    conn.execute(text("DROP TABLE _mm_owner_user;"))

    # Dropping owner_id takes its composite indexes with it. The NOT NULL on
    # accounts goes with the column, so there is nothing to un-set first.
    for table in ATTRIBUTION_TABLES:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS owner_id;"))
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_transaction_splits_parent_txn_id "
            "ON transaction_splits (parent_txn_id);"
        )
    )
    conn.execute(text("DROP TABLE IF EXISTS owners;"))
