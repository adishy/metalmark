"""Data migrations, run against a database that already holds a household.

The rest of the suite migrates an *empty* database to head once per session, which
proves the schema and says nothing about what a migration does to rows a
household has spent years entering. A data migration is only as good as its
behaviour on those rows, so each one here is exercised the way it meets a real
install: a scratch database is migrated to the revision before it, filled with the
shapes of data it must change **and the shapes it must leave alone**, upgraded,
checked, downgraded — and then compared, row for row, with what it held before.

Alembic runs in a subprocess so the scratch database cannot disturb the session's
own (settings and engines are process-cached).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from app.services.balance_sign import positives_are_amounts_owed
from tests.conftest import OWNER_PW, OWNER_USER, TEST_DB

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRATCH_DB = f"{TEST_DB}_migrations"

#: Every row of these, in a stable order, is what "unchanged" means.
FINGERPRINT_QUERIES = {
    "accounts": "SELECT * FROM accounts ORDER BY id",
    "balance_snapshots": "SELECT * FROM balance_snapshots ORDER BY id",
}


def _dsn(db: str) -> str:
    return (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={db} user={OWNER_USER} password={OWNER_PW}"
    )


@pytest.fixture
def scratch_db():
    with psycopg.connect(_dsn("postgres"), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        admin.execute(f'CREATE DATABASE "{SCRATCH_DB}" OWNER "{OWNER_USER}"')
    yield SCRATCH_DB
    with psycopg.connect(_dsn("postgres"), autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (SCRATCH_DB,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


def _alembic(db: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "POSTGRES_DB": db},
        check=True,
        capture_output=True,
    )


def _fingerprint(conn) -> dict[str, list[tuple]]:
    return {name: conn.execute(q).fetchall() for name, q in FINGERPRINT_QUERIES.items()}


def _household(conn) -> tuple[uuid.UUID, uuid.UUID]:
    hid = conn.execute(
        "INSERT INTO households (name, base_currency, timezone) "
        "VALUES ('Home', 'USD', 'UTC') RETURNING id"
    ).fetchone()[0]
    owner = conn.execute(
        "INSERT INTO owners (household_id, name, kind, sort) "
        "VALUES (%s, 'Shared', 'shared', 0) RETURNING id",
        (hid,),
    ).fetchone()[0]
    return hid, owner


def _account(conn, hid, owner, name, *, type_="investment", source="derived",
             synced=True, balance="0", balance_date="2026-09-20", is_asset=True):
    return conn.execute(
        "INSERT INTO accounts (household_id, name, type, currency, current_balance, "
        "balance_date, is_asset, owner_id, is_manual, is_hidden, balance_source, "
        "external_key, external_id) "
        "VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, %s, false, %s, %s, %s) RETURNING id",
        (hid, name, type_, balance, balance_date, is_asset, owner, not synced, source,
         f"bank:{name.lower()}" if synced else None, f"ACT-{name}" if synced else None),
    ).fetchone()[0]


def _snapshot(conn, hid, account_id, day, balance):
    conn.execute(
        "INSERT INTO balance_snapshots (household_id, account_id, balance_date, balance, "
        "currency) VALUES (%s, %s, %s, %s, 'USD')",
        (hid, account_id, day, balance),
    )


def _source(conn, account_id) -> str | None:
    return conn.execute(
        "SELECT balance_source FROM accounts WHERE id = %s", (account_id,)
    ).fetchone()[0]


def _snapshots(conn, account_id) -> list[tuple]:
    return conn.execute(
        "SELECT balance_date::text, balance::text FROM balance_snapshots "
        "WHERE account_id = %s ORDER BY balance_date",
        (account_id,),
    ).fetchall()


# ---- 0006: synced investment accounts with nothing to derive from ------------


def test_0006_moves_only_synced_positionless_accounts_and_reverses_exactly(scratch_db):
    _alembic(scratch_db, "upgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        hid, owner = _household(conn)
        # Changed: synced, derived, nothing to derive from, never snapshotted.
        bare = _account(conn, hid, owner, "Savings", balance="114685.51")
        # Changed, but it already has history — nothing is seeded over it.
        with_history = _account(conn, hid, owner, "Brokerage", balance="5000")
        _snapshot(conn, hid, with_history, "2026-09-01", "4800")
        # Left alone: a human entered a position, so its history is its holdings'.
        held = _account(conn, hid, owner, "IRA", balance="20000")
        security = conn.execute(
            "INSERT INTO securities (household_id, name, security_type, currency, "
            "is_manual) VALUES (%s, 'VTI', 'etf', 'USD', true) RETURNING id",
            (hid,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO holdings (household_id, account_id, security_id, quantity) "
            "VALUES (%s, %s, %s, 10)",
            (hid, held, security),
        )
        # Left alone: never synced — ADR-0021's manual default stands.
        manual = _account(conn, hid, owner, "401k", synced=False, balance="80000")
        # Left alone: not an investment account at all.
        checking = _account(conn, hid, owner, "Checking", type_="depository", source=None,
                            balance="2500")
        before = _fingerprint(conn)

    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _source(conn, bare) == "stated"
        assert _snapshots(conn, bare) == [("2026-09-20", "114685.5100")]
        assert _source(conn, with_history) == "stated"
        assert _snapshots(conn, with_history) == [("2026-09-01", "4800.0000")]
        assert _source(conn, held) == "derived"
        assert _source(conn, manual) == "derived"
        assert _source(conn, checking) is None

    _alembic(scratch_db, "downgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _fingerprint(conn) == before
        assert conn.execute(
            "SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = 'migration_backup'"
        ).fetchone() is None


def test_0006_backup_is_out_of_the_app_roles_reach(scratch_db):
    """The backup names accounts across households, so the RLS-bound role must not
    be able to read it at all."""
    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        app_user = os.environ["APP_DB_USER"]
        can_use = conn.execute(
            "SELECT has_schema_privilege(%s, 'migration_backup', 'USAGE')", (app_user,)
        ).fetchone()[0]
    assert can_use is False


def test_0006_downgrade_keeps_a_balance_written_after_it(scratch_db):
    """A sync on the seeded snapshot's day updates that row in place; its balance
    is the household's now, and the downgrade must not delete it."""
    _alembic(scratch_db, "upgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        hid, owner = _household(conn)
        bare = _account(conn, hid, owner, "Savings", balance="100")
    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        conn.execute("UPDATE balance_snapshots SET balance = 130 WHERE account_id = %s", (bare,))
    _alembic(scratch_db, "downgrade", "0005")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _snapshots(conn, bare) == [("2026-09-20", "130.0000")]


# ---- 0007: liability balances become signed -----------------------------------

#: (name, ever synced, current_balance, [(day, snapshot balance)]) → expected
#: current_balance and snapshot balances after the upgrade.
LIABILITIES = [
    # Hand-entered card: every positive was an amount owed.
    ("Hand card", False, "850", [("2026-01-01", "900"), ("2026-02-01", "850")],
     "-850.0000", ["-900.0000", "-850.0000"]),
    # Hand-made card with an OFX statement imported into it: mixed, positives flip.
    ("Hand+OFX", False, "300", [("2026-01-01", "-250"), ("2026-02-01", "300")],
     "-300.0000", ["-250.0000", "-300.0000"]),
    # Synced card with a hand edit in the old convention: provider sign evident.
    ("Synced+edit", True, "-120", [("2026-01-01", "-100"), ("2026-01-15", "40")],
     "-120.0000", ["-100.0000", "-40.0000"]),
    # Synced card, always negative: already signed.
    ("Synced", True, "-60", [("2026-01-01", "-60")], "-60.0000", ["-60.0000"]),
    # Synced card, never negative: no evidence — untouched.
    ("Synced positive", True, "75", [("2026-01-01", "75")], "75.0000", ["75.0000"]),
    # Paid-off hand card: nothing positive to flip.
    ("Paid off", False, "0", [("2026-01-01", "0")], "0.0000", ["0.0000"]),
]


def test_0007_signs_liabilities_per_account_and_reverses_exactly(scratch_db):
    _alembic(scratch_db, "upgrade", "0006")
    ids = {}
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        hid, owner = _household(conn)
        for name, synced, current, snaps, *_ in LIABILITIES:
            ids[name] = _account(conn, hid, owner, name, type_="credit", source=None,
                                 synced=synced, balance=current, is_asset=False)
            for day, balance in snaps:
                _snapshot(conn, hid, ids[name], day, balance)
        # An asset with a positive balance is not a liability and is never touched.
        checking = _account(conn, hid, owner, "Checking", type_="depository", source=None,
                            balance="2500")
        _snapshot(conn, hid, checking, "2026-01-01", "2500")
        before = _fingerprint(conn)

    _alembic(scratch_db, "upgrade", "0007")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        for name, _synced, _current, _snaps, want_current, want_snaps in LIABILITIES:
            got_current = conn.execute(
                "SELECT current_balance::text FROM accounts WHERE id = %s", (ids[name],)
            ).fetchone()[0]
            assert (name, got_current) == (name, want_current)
            assert [b for _d, b in _snapshots(conn, ids[name])] == want_snaps, name
        assert _snapshots(conn, checking) == [("2026-01-01", "2500.0000")]

    _alembic(scratch_db, "downgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _fingerprint(conn) == before


def test_0007_and_the_import_rule_agree():
    """The migration carries a frozen SQL copy of ``balance_sign``'s rule; this
    holds the Python one to the same answers on the same cases: it says "flip"
    exactly for the accounts whose rows the migration changed."""
    for name, synced, current, snaps, want_current, want_snaps in LIABILITIES:
        values = [Decimal(current), *(Decimal(b) for _d, b in snaps)]
        wanted = [Decimal(want_current), *(Decimal(b) for b in want_snaps)]
        assert positives_are_amounts_owed(values, ever_synced=synced) == (values != wanted), name


def test_0007_downgrade_keeps_a_balance_written_after_it(scratch_db):
    """A card edited after the upgrade was edited in the signed convention on
    purpose; reverting it would lose the edit."""
    _alembic(scratch_db, "upgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        hid, owner = _household(conn)
        card = _account(conn, hid, owner, "Card", type_="credit", source=None, synced=False,
                        balance="850", is_asset=False)
        _snapshot(conn, hid, card, "2026-01-01", "850")
    _alembic(scratch_db, "upgrade", "0007")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        conn.execute("UPDATE accounts SET current_balance = -900 WHERE id = %s", (card,))
    _alembic(scratch_db, "downgrade", "0006")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert conn.execute(
            "SELECT current_balance::text FROM accounts WHERE id = %s", (card,)
        ).fetchone()[0] == "-900.0000"
        assert _snapshots(conn, card) == [("2026-01-01", "850.0000")]


# ---- 0008: agent tokens --------------------------------------------------------


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",)).fetchone()[0]


def test_0008_adds_agent_tokens_to_a_live_database_and_touches_nothing_else(scratch_db):
    """Additive: the table appears, the app role can use it, and the household's
    rows are the same before, after, and after a downgrade."""
    _alembic(scratch_db, "upgrade", "0007")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        # 0001 builds from live metadata, so a database created today already has
        # the table at 0007. Drop it to be the database an existing install has.
        conn.execute("DROP TABLE IF EXISTS agent_tokens")
        hid, owner = _household(conn)
        acct = _account(conn, hid, owner, "Checking", type_="depository", source=None,
                        balance="120.50")
        _snapshot(conn, hid, acct, "2026-09-01", "120.50")
        before = _fingerprint(conn)

    _alembic(scratch_db, "upgrade", "0008")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _has_table(conn, "agent_tokens")
        app_role = os.environ["APP_DB_USER"]
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert conn.execute(
                "SELECT has_table_privilege(%s, 'agent_tokens', %s)", (app_role, privilege)
            ).fetchone()[0], privilege
        # An identity table: no household column, so no household RLS to forget.
        columns = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_tokens'"
        ).fetchall()}
        assert "household_id" not in columns
        assert _fingerprint(conn) == before

    _alembic(scratch_db, "downgrade", "0007")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert not _has_table(conn, "agent_tokens")
        assert _fingerprint(conn) == before

    # And up again: the shape-detecting create is safe to re-run.
    _alembic(scratch_db, "upgrade", "0008")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _has_table(conn, "agent_tokens")


# ---- 0009: starter categories --------------------------------------------------


def _category_names(conn, hid) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM categories WHERE household_id = %s ORDER BY name", (hid,)
    ).fetchall()]


def test_0009_matches_the_apps_starter_set():
    """The migration carries a frozen copy; at this revision the two are equal."""
    import importlib.util

    from app.services.default_categories import DEFAULT_CATEGORIES

    path = BACKEND_DIR / "alembic" / "versions" / "0009_starter_categories.py"
    spec = importlib.util.spec_from_file_location("m0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_CATEGORIES == DEFAULT_CATEGORIES


def test_0009_fills_only_empty_households_and_downgrade_keeps_what_is_used(scratch_db):
    """A household with its own categories is untouched; an empty one gets the set,
    typed; a downgrade removes the added rows except one a transaction now uses."""
    _alembic(scratch_db, "upgrade", "0008")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        empty, empty_owner = _household(conn)
        own, _own_owner = _household(conn)
        group = conn.execute(
            "INSERT INTO category_groups (household_id, name, type, sort) "
            "VALUES (%s, 'Mine', 'expense', 0) RETURNING id", (own,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO categories (household_id, group_id, name, rollover, sort) "
            "VALUES (%s, %s, 'Hobbies', false, 0)", (own, group)
        )
        acct = _account(conn, empty, empty_owner, "Checking", type_="depository",
                        source=None, balance="10")

    _alembic(scratch_db, "upgrade", "0009")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _category_names(conn, own) == ["Hobbies"]
        names = _category_names(conn, empty)
        assert "Groceries" in names and "Transfer" in names and "Paychecks" in names
        types = dict(conn.execute(
            "SELECT c.name, g.type FROM categories c JOIN category_groups g "
            "ON g.id = c.group_id WHERE c.household_id = %s", (empty,)
        ).fetchall())
        assert types["Transfer"] == "transfer"
        assert types["Paychecks"] == "income"
        assert types["Rent"] == "expense"
        icon = conn.execute(
            "SELECT icon FROM categories WHERE household_id = %s AND name = 'Groceries'",
            (empty,),
        ).fetchone()[0]
        assert icon == "🛒"
        groceries = conn.execute(
            "SELECT id FROM categories WHERE household_id = %s AND name = 'Groceries'",
            (empty,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO transactions (household_id, account_id, amount, currency, "
            "transacted_at, category_id, review_status, is_pending, is_hidden, "
            "is_split_parent, source, field_sources) VALUES (%s, %s, -5, 'USD', now(), %s, "
            "'needs_review', false, false, false, 'manual', '{}')",
            (empty, acct, groceries),
        )

    _alembic(scratch_db, "downgrade", "0008")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _category_names(conn, own) == ["Hobbies"]
        # The one in use stays, with its group; the rest are gone.
        assert _category_names(conn, empty) == ["Groceries"]
        assert conn.execute(
            "SELECT count(*) FROM category_groups WHERE household_id = %s", (empty,)
        ).fetchone()[0] == 1


# ---- 0010: institution logos ---------------------------------------------------


def test_0010_adds_institution_logos_with_rls_and_touches_nothing_else(scratch_db):
    _alembic(scratch_db, "upgrade", "0009")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS institution_logos")
        hid, owner = _household(conn)
        acct = _account(conn, hid, owner, "Checking", type_="depository", source=None,
                        balance="10")
        _snapshot(conn, hid, acct, "2026-09-01", "10")
        before = _fingerprint(conn)

    _alembic(scratch_db, "upgrade", "0010")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _has_table(conn, "institution_logos")
        assert conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'institution_logos'"
        ).fetchone()[0]
        assert _fingerprint(conn) == before

    _alembic(scratch_db, "downgrade", "0009")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert not _has_table(conn, "institution_logos")
        assert _fingerprint(conn) == before


# ---- 0011: holdings.source ------------------------------------------------------


def _holdings(conn) -> list[tuple]:
    return conn.execute("SELECT * FROM holdings ORDER BY id").fetchall()


def test_0011_marks_every_existing_holding_manual_and_downgrade_drops_only_synced(scratch_db):
    _alembic(scratch_db, "upgrade", "0010")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        # 0001 builds from today's metadata, so undo the column to be the old shape.
        conn.execute(
            "ALTER TABLE holdings DROP CONSTRAINT IF EXISTS ck_holdings_source_valid, "
            "DROP COLUMN IF EXISTS source"
        )
        hid, owner = _household(conn)
        acct = _account(conn, hid, owner, "Brokerage", source="stated", balance="5000")
        vti = conn.execute(
            "INSERT INTO securities (household_id, name, ticker, security_type, currency, "
            "is_manual) VALUES (%s, 'Vanguard', 'VTI', 'etf', 'USD', true) RETURNING id",
            (hid,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO holdings (household_id, account_id, security_id, quantity) "
            "VALUES (%s, %s, %s, 10)",
            (hid, acct, vti),
        )
        before = _fingerprint(conn)
        held_before = _holdings(conn)

    _alembic(scratch_db, "upgrade", "0011")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert conn.execute("SELECT source FROM holdings").fetchall() == [("manual",)]
        assert _fingerprint(conn) == before
        # What sync would write after the upgrade: one position in a security only
        # the bank reports, and one in the human's own security in another account.
        aapl = conn.execute(
            "INSERT INTO securities (household_id, name, ticker, security_type, currency, "
            "is_manual) VALUES (%s, 'Apple', 'AAPL', 'stock', 'USD', false) RETURNING id",
            (hid,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO security_prices (household_id, security_id, price_date, price, "
            "currency, source) VALUES (%s, %s, '2026-09-21', 190, 'USD', 'auto')",
            (hid, aapl),
        )
        ira = _account(conn, hid, owner, "IRA", source="stated", balance="900")
        for security in (aapl, vti):
            conn.execute(
                "INSERT INTO holdings (household_id, account_id, security_id, quantity, "
                "source) VALUES (%s, %s, %s, 2, 'simplefin')",
                (hid, ira, security),
            )

    _alembic(scratch_db, "downgrade", "0010")
    with psycopg.connect(_dsn(scratch_db), autocommit=True) as conn:
        assert _holdings(conn) == held_before
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM securities").fetchall()]
        assert tickers == ["VTI"]
        assert conn.execute("SELECT count(*) FROM security_prices").fetchone()[0] == 0
