"""First-run bootstrap + dev seed.

    python -m app.seed            # create the first household + owner (idempotent)
    python -m app.seed --demo     # also add categories, owners and a demo ledger

Owner/household come from env (METALMARK_SEED_*). Safe to re-run: does nothing if
a user already exists.

The demo ledger is not decoration. Reports, owner filters, transfer matching and
the rules engine all need data with a *shape* before they can be looked at, and
hand-entering five months of transactions to review a chart is not a good use of
anyone's afternoon. It goes through the same services the API does — so
``base_amount``, ``field_sources`` and the split allocation are computed exactly
as they would be for a human — which makes it a smoke test of those services too.
"""

from __future__ import annotations

import asyncio
import calendar
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import scoped_session, unscoped_session
from app.logging import configure_logging, get_logger
from app.models import Category, CategoryGroup, User
from app.schemas.ledger import AccountCreate, AccountUpdate
from app.schemas.transactions import SplitIn, TransactionCreate
from app.services import auth as svc
from app.services import ledger as ledger_svc
from app.services import owners as owner_svc
from app.services import transactions as txn_svc
from app.settings import get_settings

log = get_logger("seed")

DEFAULT_GROUPS = {
    "income": ["Salary", "Interest", "Dividends", "Other Income"],
    "expense": ["Groceries", "Dining", "Housing", "Utilities", "Transport", "Shopping",
                "Health", "Entertainment", "Fees"],
    "transfer": ["Transfer", "Credit Card Payment"],
}

# Labels, not people: they exist so the owner pickers have something in them on a
# fresh dev database. The seed user's own owner comes from their display name; there
# is no "Joint" here because the household's Shared owner already is that label.
DEMO_OWNERS = ["Partner"]

# Five months of activity, ending with the current (partial) month. The opening
# balances are snapshotted at the start of the year so the net-worth series starts
# at the household's real opening position: a snapshot is carried forward, but an
# account with no snapshot at or before a date contributes *nothing* to it, so a
# first snapshot in April would make the chart climb out of zero.
DEMO_MONTHS = 5
DEMO_OPENING = {
    "checking": Decimal("4000"),
    "savings": Decimal("12000"),
    "card_owed": Decimal("850"),
    "euro": Decimal("2000"),
}
DEMO_FX_RATE = {"opening": Decimal("0.90"), "latest": Decimal("0.85")}

# One month's ordinary activity on the checking account, as (day, amount, category).
DEMO_MONTHLY = [
    (1, Decimal("5200"), "Salary", "Paycheque"),
    (5, Decimal("-420"), "Groceries", "Supermarket"),
    (12, Decimal("-180"), "Dining", "Restaurants"),
    (15, Decimal("-140"), "Utilities", "Power and water"),
    (18, Decimal("-90"), "Transport", "Transit pass"),
    (22, Decimal("-160"), "Shopping", "Department store"),
]


def _month_start(d: date, months_back: int) -> date:
    """First day of the month ``months_back`` months before ``d``'s month."""
    total = d.year * 12 + d.month - 1 - months_back
    return date(total // 12, total % 12 + 1, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _at(d: date) -> datetime:
    """Midday UTC: far enough from midnight that no timezone shifts the date."""
    return datetime(d.year, d.month, d.day, 12, tzinfo=UTC)


async def _named(session, model, household_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = (
        await session.execute(
            select(model.name, model.id).where(model.household_id == household_id)
        )
    ).all()
    return {name.lower(): row_id for name, row_id in rows}


def _demo_period(today: date) -> tuple[date, date, list[date]]:
    """``(opening_date, today, month_starts)`` for a run of the demo ledger."""
    months = [_month_start(today, back) for back in range(DEMO_MONTHS - 1, -1, -1)]
    # The opening snapshot is at the start of the year, or just before the first
    # activity if that is earlier.
    return min(date(today.year, 1, 1), months[0] - timedelta(days=1)), today, months


async def _demo_ledger(session, household_id: uuid.UUID, owner_name: str) -> dict:
    """Five months of coherent activity, built through the services.

    "Coherent" is the load-bearing word: every account's month-end snapshot is its
    opening balance plus the sum of that account's own transactions, which is what
    makes ``Δ net worth = cash flow + revaluation`` hold on the demo data. Seed a
    balance that does not match the flows and the report shows a revaluation figure
    that looks like a bug in the report.

    Returns the period it covered and the account ids, so a caller (the coherence
    test) can assert against the same range instead of recomputing it.
    """
    base = await ledger_svc.base_currency(session, household_id)
    owners = await _named(session, owner_svc.Owner, household_id)
    cats = await _named(session, Category, household_id)
    mine, partner, shared = owners[owner_name.lower()], owners["partner"], owners["shared"]

    opening_date, today, months = _demo_period(date.today())

    checking = await ledger_svc.create_account(session, household_id, AccountCreate(
        name="Everyday Checking", type="depository", currency=base,
        institution="Demo Bank", current_balance=DEMO_OPENING["checking"],
        balance_date=opening_date, owner_id=mine))
    savings = await ledger_svc.create_account(session, household_id, AccountCreate(
        name="High-Yield Savings", type="depository", currency=base,
        institution="Demo Bank", current_balance=DEMO_OPENING["savings"],
        balance_date=opening_date, owner_id=shared))
    # A liability and a foreign-currency account, both left flat: net worth then has
    # an asset, a liability and an unconverted-currency line, and the euro balance is
    # constant while its rate moves, so the currency-revaluation line is non-zero and
    # attributable to FX alone rather than to any spending.
    card = await ledger_svc.create_account(session, household_id, AccountCreate(
        name="Rewards Card", type="credit", currency=base,
        institution="Demo Bank", current_balance=DEMO_OPENING["card_owed"],
        balance_date=opening_date, owner_id=shared))
    euro = await ledger_svc.create_account(session, household_id, AccountCreate(
        name="Euro Savings", type="depository", currency="EUR",
        institution="Demo Bank", current_balance=DEMO_OPENING["euro"],
        balance_date=opening_date, owner_id=partner))

    # base -> EUR, quoted as EUR per unit of base, which is the direction the rest of
    # the app uses. Weakening the quote raises the base value of a euro balance.
    #
    # The second rate is dated *today*, not the current month's end: ``net_worth_at``
    # resolves a date to the latest rate at or before it, so a rate dated anywhere
    # past the report's end is never consulted and the revaluation line — the whole
    # reason this account exists — reads a flat zero until the month turns over.
    await ledger_svc.upsert_fx_rate(session, household_id, base_ccy=base, quote_ccy="EUR",
                                    rate_date=opening_date, rate=DEMO_FX_RATE["opening"])
    await ledger_svc.upsert_fx_rate(session, household_id, base_ccy=base, quote_ccy="EUR",
                                    rate_date=today, rate=DEMO_FX_RATE["latest"])

    running = {"checking": DEMO_OPENING["checking"], "savings": DEMO_OPENING["savings"]}

    async def record(account, key, when: date, amount: Decimal, category: str,
                     description: str, owner_id=None):
        txn = await txn_svc.create_transaction(session, household_id, TransactionCreate(
            account_id=account.id, amount=amount, transacted_at=_at(when),
            description=description, category_id=cats[category.lower()], owner_id=owner_id))
        running[key] += amount
        return txn

    split_candidate = None
    for index, month in enumerate(months):
        last_day = min(_month_end(month), today)
        for day, amount, category, description in DEMO_MONTHLY:
            when = date(month.year, month.month, min(day, last_day.day))
            if when > today:
                continue  # the current month is only as far along as it is
            # The salary is the seed user's own; everything else lands on Shared.
            owner_id = mine if amount > 0 else shared
            txn = await record(checking, "checking", when, amount, category, description,
                               owner_id=owner_id)
            if category == "Shopping" and index == DEMO_MONTHS - 2:
                split_candidate = txn

        # The savings interest is a month-end posting, so it uses the real month end
        # rather than the clamped one — except in the current month, where it has not
        # happened yet.
        if last_day == _month_end(month):
            await record(savings, "savings", last_day, Decimal("35"), "Interest",
                         "Savings interest", owner_id=shared)

        if index == 1:
            # Deliberately left unmatched: two legs a day apart, waiting for someone
            # to match them in the UI (and to watch cash flow correct itself).
            await record(checking, "checking", date(month.year, month.month, 24),
                         Decimal("-500"), "Transfer", "Move to savings")
            await record(savings, "savings", date(month.year, month.month, 25),
                         Decimal("500"), "Transfer", "From checking")
        if index == 2:
            out = await record(checking, "checking", date(month.year, month.month, 10),
                               Decimal("-1000"), "Transfer", "Monthly savings")
            into = await record(savings, "savings", date(month.year, month.month, 10),
                                Decimal("1000"), "Transfer", "From checking")
            await txn_svc.link_transfer(session, household_id, out.id, into.id)

        await ledger_svc.update_account(session, checking.id, AccountUpdate(
            current_balance=running["checking"], balance_date=last_day))
        await ledger_svc.update_account(session, savings.id, AccountUpdate(
            current_balance=running["savings"], balance_date=last_day))

    if split_candidate is not None:
        # One charge split across two owners, which is the shape the row-scoped
        # report filters exist for: the parent belongs to Shared, the children to
        # two different people.
        await txn_svc.replace_splits(session, split_candidate.id, [
            SplitIn(amount=Decimal("-100"), category_id=cats["groceries"], owner_id=partner),
            SplitIn(amount=Decimal("-60"), category_id=cats["dining"], owner_id=mine),
        ])

    log.info("seed.demo_ledger", household=str(household_id),
             accounts=4, transactions=len(months) * (len(DEMO_MONTHLY) + 1),
             card=str(card.id), euro=str(euro.id))
    return {
        "start": opening_date,
        "end": today,
        "accounts": {
            "checking": checking.id,
            "savings": savings.id,
            "card": card.id,
            "euro": euro.id,
        },
    }


async def demo_reference_data(
    session, household_id: uuid.UUID, owner_name: str
) -> None:
    """The owners and categories the demo ledger files its rows against.

    Split out of ``run`` so the seed's coherence test can build the same world
    without going through the env-var bootstrap. Idempotent, like the rest of the
    seed: re-running adds only what is missing.
    """
    # The Shared owner already exists — bootstrap_household created it with the
    # household (ADR-0026). These are the ones a human would want.
    for name in [owner_name, *DEMO_OWNERS]:
        if not (
            await session.execute(
                select(owner_svc.Owner.id).where(
                    owner_svc.Owner.household_id == household_id,
                    owner_svc.Owner.name == name,
                )
            )
        ).first():
            await owner_svc.create_owner(session, household_id, name=name)
    for gtype, cats in DEFAULT_GROUPS.items():
        group = CategoryGroup(household_id=household_id, name=gtype.title(), type=gtype)
        session.add(group)
        await session.flush()
        for i, name in enumerate(cats):
            session.add(
                Category(household_id=household_id, group_id=group.id, name=name, sort=i)
            )
    await session.flush()


async def run(demo: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)
    owner_name = os.getenv("METALMARK_SEED_NAME", "Owner")

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
            owner_name=owner_name,
            owner_password=os.getenv("METALMARK_SEED_PASSWORD", "changeme-please-8+"),
            timezone=os.getenv("METALMARK_SEED_TZ", "UTC"),
        )
        household_id = household.id
        log.info("seed.bootstrap", household=str(household_id), owner=owner.email)

    if demo:
        async with scoped_session(household_id=household_id) as session:
            await demo_reference_data(session, household_id, owner_name)
            log.info("seed.demo_categories", household=str(household_id))
            await _demo_ledger(session, household_id, owner_name)


if __name__ == "__main__":
    asyncio.run(run(demo="--demo" in sys.argv))
