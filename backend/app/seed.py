"""First-run bootstrap + dev seed.

    python -m app.seed            # create the first household + owner (idempotent)
    python -m app.seed --demo     # also add default categories

Owner/household come from env (METALMARK_SEED_*). Safe to re-run: does nothing if
a user already exists.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from app.db import scoped_session, unscoped_session
from app.logging import configure_logging, get_logger
from app.models import Category, CategoryGroup, User
from app.services import auth as svc
from app.settings import get_settings

log = get_logger("seed")

DEFAULT_GROUPS = {
    "income": ["Salary", "Interest", "Dividends", "Other Income"],
    "expense": ["Groceries", "Dining", "Housing", "Utilities", "Transport", "Shopping",
                "Health", "Entertainment", "Fees"],
    "transfer": ["Transfer", "Credit Card Payment"],
}


async def run(demo: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)

    async with unscoped_session() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            log.info("seed.skip", reason="a user already exists")
            return
        household, owner = await svc.bootstrap_household(
            session,
            name=os.getenv("METALMARK_SEED_HOUSEHOLD", "Home"),
            base_currency=settings.default_base_currency,
            owner_email=os.getenv("METALMARK_SEED_EMAIL", "owner@example.com"),
            owner_name=os.getenv("METALMARK_SEED_NAME", "Owner"),
            owner_password=os.getenv("METALMARK_SEED_PASSWORD", "changeme-please-8+"),
            timezone=os.getenv("METALMARK_SEED_TZ", "UTC"),
        )
        household_id = household.id
        log.info("seed.bootstrap", household=str(household_id), owner=owner.email)

    if demo:
        async with scoped_session(household_id=household_id) as session:
            for gtype, cats in DEFAULT_GROUPS.items():
                group = CategoryGroup(household_id=household_id, name=gtype.title(), type=gtype)
                session.add(group)
                await session.flush()
                for i, name in enumerate(cats):
                    session.add(
                        Category(household_id=household_id, group_id=group.id, name=name, sort=i)
                    )
            log.info("seed.demo_categories", household=str(household_id))


if __name__ == "__main__":
    asyncio.run(run(demo="--demo" in sys.argv))
