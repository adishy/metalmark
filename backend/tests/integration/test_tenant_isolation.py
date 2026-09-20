"""Tenant-isolation gate (PLAN 'Tenancy (P0)'): household B cannot read or
mutate household A's data. Exercises the real RLS path via the app role."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.db import scoped_session
from app.models import Account, Owner

pytestmark = pytest.mark.integration


def _account(household_id, owner_id, name="Checking", currency="USD"):
    return Account(
        household_id=household_id,
        name=name,
        type="depository",
        currency=currency,
        current_balance=Decimal("100.0000"),
        is_asset=True,
        is_manual=True,
        owner_id=owner_id,
    )


async def _shared_owner_id(household_id):
    async with scoped_session(household_id=household_id) as s:
        return (
            await s.execute(select(Owner.id).where(Owner.kind == "shared"))
        ).scalar_one()


async def test_household_b_cannot_read_household_a(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    owner_a = await _shared_owner_id(a)

    async with scoped_session(household_id=a) as s:
        s.add(_account(a, owner_a, "A-Checking"))

    # B sees zero of A's accounts.
    async with scoped_session(household_id=b) as s:
        count = (await s.execute(select(func.count()).select_from(Account))).scalar_one()
        assert count == 0

    # A sees its own.
    async with scoped_session(household_id=a) as s:
        names = (await s.execute(select(Account.name))).scalars().all()
        assert names == ["A-Checking"]


async def test_insert_into_foreign_household_is_blocked(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    # B's *own* owner, so the only thing left that can reject this insert is the
    # household policy — a foreign owner id would trip the FK first and prove
    # nothing about RLS.
    owner_b = await _shared_owner_id(b)

    with pytest.raises(DBAPIError):
        async with scoped_session(household_id=b) as s:
            s.add(_account(a, owner_b, "Sneaky"))
            await s.flush()


async def test_unscoped_query_sees_nothing(household_factory):
    a = await household_factory(name="A")
    owner_a = await _shared_owner_id(a)
    async with scoped_session(household_id=a) as s:
        s.add(_account(a, owner_a, "A-Checking"))

    # No GUC set -> fail-closed, zero rows.
    async with scoped_session(household_id=None) as s:
        count = (await s.execute(select(func.count()).select_from(Account))).scalar_one()
        assert count == 0


async def test_owners_are_tenant_isolated(household_factory):
    """The new table carries the same policy as every other household table.

    It matters more than most: owners are what a filter narrows to, so a leak here
    would show one household the other's names and ids.
    """
    a = await household_factory(name="A")
    b = await household_factory(name="B")

    async with scoped_session(household_id=a) as s:
        s.add(Owner(household_id=a, name="Alex", kind="person", sort=1))

    async with scoped_session(household_id=b) as s:
        # Only B's auto-created Shared owner, none of A's labels.
        names = (await s.execute(select(Owner.name))).scalars().all()
        assert names == ["Shared"]

    with pytest.raises(DBAPIError):
        async with scoped_session(household_id=b) as s:
            s.add(Owner(household_id=a, name="Sneaky", kind="person", sort=2))
            await s.flush()


async def test_one_shared_owner_per_household(household_factory):
    """The partial unique index, not just the service, enforces this."""
    a = await household_factory(name="A")
    with pytest.raises(DBAPIError):
        async with scoped_session(household_id=a) as s:
            s.add(Owner(household_id=a, name="Also Shared", kind="shared", sort=9))
            await s.flush()


async def test_owner_names_are_unique_case_insensitively(household_factory):
    a = await household_factory(name="A")
    with pytest.raises(DBAPIError):
        async with scoped_session(household_id=a) as s:
            s.add(Owner(household_id=a, name="ALEX", kind="person", sort=1))
            s.add(Owner(household_id=a, name="alex", kind="person", sort=2))
            await s.flush()
