"""Tenant-isolation gate (PLAN 'Tenancy (P0)'): household B cannot read or
mutate household A's data. Exercises the real RLS path via the app role."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db import scoped_session
from app.models import Account

pytestmark = pytest.mark.integration


def _account(household_id, name="Checking", currency="USD"):
    return Account(
        household_id=household_id,
        name=name,
        type="depository",
        currency=currency,
        current_balance=Decimal("100.0000"),
        is_asset=True,
        is_manual=True,
    )


async def test_household_b_cannot_read_household_a(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")

    async with scoped_session(household_id=a) as s:
        s.add(_account(a, "A-Checking"))

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

    # Scoped to B, try to insert a row tagged for A -> RLS WITH CHECK rejects.
    with pytest.raises(Exception):
        async with scoped_session(household_id=b) as s:
            s.add(_account(a, "Sneaky"))
            await s.flush()


async def test_unscoped_query_sees_nothing(household_factory):
    a = await household_factory(name="A")
    async with scoped_session(household_id=a) as s:
        s.add(_account(a, "A-Checking"))

    # No GUC set -> fail-closed, zero rows.
    async with scoped_session(household_id=None) as s:
        count = (await s.execute(select(func.count()).select_from(Account))).scalar_one()
        assert count == 0
