"""Every household-scoped table must carry an RLS policy — asserted over the
whole metadata, not table by table.

The reason this file exists: policies are added by hand in each migration's
``_secure_*_table`` helper (`0002`-`0005`), because ``0001`` builds the schema from
*live* ORM metadata and its frozen ``HOUSEHOLD_TABLES`` list only knows the tables
that existed when it was written. So a new table gets created by ``0001`` and gets
its policy only if the author of the next migration remembered the call. Nothing
failed when they forgot — the table worked perfectly for the household that created
it, and leaked to every other one.

Per-table tests have not caught this either, because a per-table test is written by
the same person who wrote the migration. The check has to be derived from the
metadata so that adding a model is what makes it run.

The definition of "household-scoped" here is deliberately mechanical: a table with
a ``household_id`` column. That is the same predicate the migrations use to decide
which policy to write, so the two cannot disagree about what is covered.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import Base, scoped_session, unscoped_session
from app.models import Account, Holding, Owner, Security

pytestmark = pytest.mark.integration

#: Tables that carry household_id but are *not* under household RLS, with the
#: reason. One entry, and it is load-bearing that there is exactly one.
KNOWN_EXEMPT: dict[str, str] = {
    "household_members": (
        "Identity table (ADR-0025). It cannot be under household RLS, for a "
        "chicken-and-egg reason: resolving *which* household a user belongs to is "
        "what lets the GUC be set at all, so the lookup has to happen before a "
        "household context exists. Protected by the auth layer instead — "
        "`auth.membership_for` — which is the only reader. Contrast `owners`, which "
        "also names people but is household data and *is* under RLS (ADR-0026)."
    ),
}


def _household_tables() -> list[str]:
    return sorted(
        t.name for t in Base.metadata.tables.values() if "household_id" in t.columns
    )


async def _policy_rows(session) -> dict[str, tuple[bool, int]]:
    """{table: (rls_enabled, policy_count)} for every base table in the schema."""
    rows = (
        await session.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity,
                       (SELECT count(*) FROM pg_policies p
                         WHERE p.schemaname = current_schema()
                           AND p.tablename = c.relname) AS policies
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relkind = 'r'
                """
            )
        )
    ).all()
    return {name: (rls, int(policies)) for name, rls, policies in rows}


async def test_the_set_of_household_tables_is_non_trivial():
    """Guards the guard: if the metadata ever failed to load, ``_household_tables``
    returns ``[]`` and every assertion below passes vacuously."""
    tables = _household_tables()
    assert len(tables) >= 10, f"only found {tables}"
    for expected in ("accounts", "transactions", "securities", "holdings"):
        assert expected in tables


async def test_every_household_table_has_an_rls_policy():
    tables = _household_tables()
    async with unscoped_session() as session:
        policies = await _policy_rows(session)

    missing = []
    for table in tables:
        if table in KNOWN_EXEMPT:
            continue
        rls, count = policies.get(table, (False, 0))
        if not rls or count == 0:
            missing.append(f"{table} (rls={rls}, policies={count})")

    assert not missing, (
        "household-scoped tables without RLS: "
        + ", ".join(missing)
        + " — the migration that created them forgot its _secure_*_table call"
    )


async def test_a_foreign_household_id_sees_nothing_anywhere(household_factory):
    """The behavioural half, and the one that catches a policy written wrongly
    rather than not at all — ``USING (true)`` has ``relrowsecurity`` on and one
    policy, so the catalog check above would pass it.

    Swept over every household table with a household id that exists nowhere, so a
    single assertion covers tables no other test has ever written to.
    """
    a = await household_factory(name="A")
    async with scoped_session(household_id=a) as s:
        owner_id = (await s.execute(select(Owner.id).limit(1))).scalar_one()
        s.add(
            Account(
                household_id=a,
                name="A-Checking",
                type="depository",
                currency="USD",
                current_balance=Decimal("100.0000"),
                is_asset=True,
                is_manual=True,
                owner_id=owner_id,
            )
        )

    # A household id that is real-looking but belongs to nobody.
    stranger = uuid.uuid4()
    leaks = []
    for table in _household_tables():
        if table in KNOWN_EXEMPT:
            continue
        async with scoped_session(household_id=stranger) as s:
            count = (
                await s.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            ).scalar_one()
        if count:
            leaks.append(f"{table}: {count}")

    assert not leaks, "rows visible to a stranger: " + ", ".join(leaks)


async def test_the_investment_tables_are_isolated_like_the_rest(household_factory):
    """Investment names are the most revealing thing a household owns, so this one
    gets an explicit test on top of the sweep: A's securities and positions must be
    invisible to B, and B's insert claiming A's household must be refused.
    """
    a = await household_factory(name="A")
    b = await household_factory(name="B")

    async with scoped_session(household_id=a) as s:
        owner_id = (await s.execute(select(Owner.id).limit(1))).scalar_one()
        account = Account(
            household_id=a,
            name="A-Brokerage",
            type="investment",
            currency="USD",
            current_balance=Decimal("0.0000"),
            balance_source="derived",
            is_asset=True,
            is_manual=True,
            owner_id=owner_id,
        )
        s.add(account)
        await s.flush()
        security = Security(
            household_id=a, name="A Fund", ticker="AFND", security_type="etf", currency="USD"
        )
        s.add(security)
        await s.flush()
        s.add(
            Holding(
                household_id=a,
                account_id=account.id,
                security_id=security.id,
                quantity=Decimal("10.00000000"),
            )
        )

    async with scoped_session(household_id=b) as s:
        assert (await s.execute(text("SELECT count(*) FROM securities"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM holdings"))).scalar_one() == 0

    # B may not insert into A's household even with a well-formed row: the only
    # thing that can reject this is the policy (household A exists, so the FK is
    # satisfied).
    with pytest.raises(DBAPIError):
        async with scoped_session(household_id=b) as s:
            s.add(
                Security(
                    household_id=a,
                    name="Sneaky Fund",
                    ticker="SNK",
                    security_type="etf",
                    currency="USD",
                )
            )
            await s.flush()
