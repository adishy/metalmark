"""Ledger core: accounts, categories, tags, FX-rate writes + net worth.

The manual write path is canonical (ADR-0010). All queries run in the request's
household-scoped session (RLS), except FX rates which are shared reference data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_storage
from app.models import (
    Account,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    FxRate,
    Household,
    Tag,
    Transaction,
)
from app.schemas.ledger import AccountCreate, AccountUpdate, is_asset_for
from app.services import fx


class LedgerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _today() -> date:
    return datetime.now(UTC).date()


async def base_currency(session: AsyncSession, household_id: uuid.UUID) -> str:
    return (
        await session.execute(
            select(Household.base_currency).where(Household.id == household_id)
        )
    ).scalar_one()


# ---- Accounts -------------------------------------------------------------

async def _snapshot_balance(session: AsyncSession, account: Account) -> None:
    """Upsert a balance snapshot for the account's balance_date (net-worth history)."""
    d = account.balance_date or _today()
    existing = (
        await session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.account_id == account.id, BalanceSnapshot.balance_date == d
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.balance = account.current_balance
        existing.currency = account.currency
    else:
        session.add(
            BalanceSnapshot(
                household_id=account.household_id,
                account_id=account.id,
                balance_date=d,
                balance=account.current_balance,
                currency=account.currency,
            )
        )


async def create_account(session: AsyncSession, household_id: uuid.UUID,
                          data: AccountCreate) -> Account:
    acct = Account(
        household_id=household_id,
        name=data.name,
        type=data.type,
        currency=data.currency.upper(),
        subtype=data.subtype,
        institution=data.institution,
        current_balance=data.current_balance,
        balance_date=data.balance_date or _today(),
        is_asset=is_asset_for(data.type),
        balance_source="derived" if data.type == "investment" else None,
        owner_user_id=data.owner_user_id,
        is_manual=True,
    )
    session.add(acct)
    await session.flush()
    await _snapshot_balance(session, acct)
    await session.flush()
    return acct


async def list_accounts(session: AsyncSession) -> list[Account]:
    return list(
        (await session.execute(select(Account).order_by(Account.name))).scalars().all()
    )


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    acct = (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if acct is None:
        raise LedgerError("Account not found", 404)
    return acct


async def update_account(session: AsyncSession, account_id: uuid.UUID,
                         data: AccountUpdate) -> Account:
    acct = await get_account(session, account_id)
    changed_balance = False
    for field in ("name", "subtype", "institution", "owner_user_id", "is_hidden"):
        val = getattr(data, field)
        if val is not None:
            setattr(acct, field, val)
    if data.current_balance is not None:
        acct.current_balance = data.current_balance
        changed_balance = True
    if data.balance_date is not None:
        acct.balance_date = data.balance_date
        changed_balance = True
    await session.flush()
    if changed_balance:
        await _snapshot_balance(session, acct)
        await session.flush()
    return acct


async def delete_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    acct = await get_account(session, account_id)
    await session.delete(acct)
    await session.flush()


async def net_worth(session: AsyncSession, household_id: uuid.UUID):
    base = await base_currency(session, household_id)
    accounts = await list_accounts(session)
    assets = Decimal("0")
    liabilities = Decimal("0")
    unconverted: set[str] = set()
    for a in accounts:
        if a.is_hidden:
            continue
        conv, _ = await fx.to_base(
            session, amount=a.current_balance, currency=a.currency,
            on=a.balance_date or _today(), base_ccy=base,
        )
        if conv is None:
            unconverted.add(a.currency)
            continue
        if a.is_asset:
            assets += conv
        else:
            liabilities += conv
    return {
        "base_currency": base,
        "assets": quantize_storage(assets),
        "liabilities": quantize_storage(liabilities),
        "net_worth": quantize_storage(assets - liabilities),
        "unconverted_currencies": sorted(unconverted),
    }


# ---- Categories & tags ----------------------------------------------------

async def create_category_group(session: AsyncSession, household_id: uuid.UUID,
                                 name: str, type_: str, sort: int) -> CategoryGroup:
    g = CategoryGroup(household_id=household_id, name=name, type=type_, sort=sort)
    session.add(g)
    await session.flush()
    return g


async def create_category(session: AsyncSession, household_id: uuid.UUID, group_id: uuid.UUID,
                          name: str, icon: str | None, color: str | None, sort: int) -> Category:
    grp = (
        await session.execute(select(CategoryGroup).where(CategoryGroup.id == group_id))
    ).scalar_one_or_none()
    if grp is None:
        raise LedgerError("Category group not found", 404)
    c = Category(household_id=household_id, group_id=group_id, name=name, icon=icon,
                 color=color, sort=sort)
    session.add(c)
    await session.flush()
    return c


async def list_category_groups(session: AsyncSession) -> list[CategoryGroup]:
    return list(
        (await session.execute(select(CategoryGroup).order_by(CategoryGroup.sort))).scalars().all()
    )


async def list_categories(session: AsyncSession) -> list[Category]:
    return list(
        (await session.execute(select(Category).order_by(Category.sort))).scalars().all()
    )


async def create_tag(session: AsyncSession, household_id: uuid.UUID, name: str,
                     color: str | None) -> Tag:
    t = Tag(household_id=household_id, name=name, color=color)
    session.add(t)
    await session.flush()
    return t


async def list_tags(session: AsyncSession) -> list[Tag]:
    return list((await session.execute(select(Tag).order_by(Tag.name))).scalars().all())


# ---- FX rates (with cache invalidation) -----------------------------------

async def upsert_fx_rate(session: AsyncSession, household_id: uuid.UUID, *, base_ccy: str,
                         quote_ccy: str, rate_date: date, rate: Decimal,
                         source: str = "manual") -> FxRate:
    base_ccy, quote_ccy = base_ccy.upper(), quote_ccy.upper()
    existing = (
        await session.execute(
            select(FxRate).where(
                FxRate.base_currency == base_ccy,
                FxRate.quote_currency == quote_ccy,
                FxRate.rate_date == rate_date,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.rate = rate
        existing.source = source
        row = existing
    else:
        row = FxRate(base_currency=base_ccy, quote_currency=quote_ccy, rate_date=rate_date,
                     rate=rate, source=source)
        session.add(row)
    await session.flush()
    # A rate change invalidates dependent base_amount caches (ADR-0017). Recompute
    # for this household's foreign-currency transactions (rollups are Phase 3).
    await recompute_base_amounts(session, household_id)
    return row


async def list_fx_rates(session: AsyncSession) -> list[FxRate]:
    return list(
        (await session.execute(select(FxRate).order_by(FxRate.rate_date.desc()))).scalars().all()
    )


async def recompute_base_amounts(session: AsyncSession, household_id: uuid.UUID) -> int:
    """Recompute base_amount for foreign-currency transactions in the household."""
    base = await base_currency(session, household_id)
    txns = list(
        (
            await session.execute(
                select(Transaction).where(Transaction.currency != base)
            )
        ).scalars().all()
    )
    n = 0
    for t in txns:
        conv, rate_date = await fx.to_base(
            session, amount=t.amount, currency=t.currency,
            on=t.transacted_at.date(), base_ccy=base,
        )
        t.base_amount = conv
        t.fx_rate_date = rate_date
        n += 1
    await session.flush()
    return n
