"""Ledger core: accounts, categories, tags, FX-rate writes + net worth.

The manual write path is canonical (ADR-0010). All queries run in the request's
household-scoped session (RLS), except FX rates which are shared reference data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import allocate, quantize_storage
from app.models import (
    Account,
    AccountConnection,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    FxRate,
    Holding,
    Household,
    InvestmentTransaction,
    Tag,
    Transaction,
)
from app.schemas.ledger import AccountCreate, AccountUpdate, is_asset_for
from app.schemas.patch import is_set
from app.services import fx
from app.services.errors import LedgerError
from app.services.owners import ensure_shared_owner, get_owner


def today() -> date:
    """The ledger's today — the one ``record_balance`` files an undated balance on."""
    return datetime.now(UTC).date()


async def base_currency(session: AsyncSession, household_id: uuid.UUID) -> str:
    return (
        await session.execute(
            select(Household.base_currency).where(Household.id == household_id)
        )
    ).scalar_one()


# ---- Accounts -------------------------------------------------------------

async def upsert_balance_snapshot(session: AsyncSession, account: Account) -> None:
    """Upsert a balance snapshot for the account's balance_date (net-worth history).

    Public because sync owns the other caller: a synced balance is history too,
    and a second implementation of "which snapshot does this balance land on"
    would be a second answer to a question that has one.

    Whether to call this for a *derived* investment account (ADR-0021) is the
    caller's decision, not this function's — M1a's manual path has always
    snapshotted those, and changing that here to suit sync would silently alter
    behaviour the ledger tests pin.
    """
    await _upsert_snapshot_at(
        session, account, on=account.balance_date or today(), balance=account.current_balance
    )


async def _upsert_snapshot_at(
    session: AsyncSession, account: Account, *, on: date, balance: Decimal
) -> None:
    existing = (
        await session.execute(
            select(BalanceSnapshot).where(
                BalanceSnapshot.account_id == account.id, BalanceSnapshot.balance_date == on
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.balance = balance
        existing.currency = account.currency
    else:
        session.add(
            BalanceSnapshot(
                household_id=account.household_id,
                account_id=account.id,
                balance_date=on,
                balance=balance,
                currency=account.currency,
            )
        )


async def record_balance(
    session: AsyncSession,
    account: Account,
    *,
    balance: Decimal,
    on: date,
    snapshot: bool = True,
) -> bool:
    """The one rule for where a balance lands — every writer calls this.

    The snapshot is written at the balance's **own** date, and the account's
    ``current_balance``/``balance_date`` move to it only when that date is not
    older than the one the account has. Returns whether it became current.

    Each half fixes a way the net-worth history was being rewritten. The edit
    dialog sent the account's old ``balance_date`` back with a new balance, which
    overwrote a past point with today's number; sync did the same whenever the
    provider sent no date, because the stale date was all it had; and an older OFX
    statement, imported after a newer one, moved the headline backwards. A balance
    for an earlier day is a correction to history, and history is where it goes.

    ``snapshot=False`` is ADR-0021's guard for a derived account, whose history is
    its holdings': the columns still move, the series does not get a second author.
    """
    current = account.balance_date is None or on >= account.balance_date
    if current:
        account.current_balance = balance
        account.balance_date = on
    await session.flush()
    if snapshot:
        await _upsert_snapshot_at(session, account, on=on, balance=balance)
        await session.flush()
    return current


async def create_account(session: AsyncSession, household_id: uuid.UUID,
                          data: AccountCreate) -> Account:
    # An account always has an owner: unset means the household's Shared owner, so
    # the attribution chain terminates instead of needing a null case (ADR-0026).
    # A supplied id is fetched under RLS, which turns a foreign id into a 404 rather
    # than a 500 from the foreign key.
    owner_id = (
        (await get_owner(session, data.owner_id)).id
        if data.owner_id is not None
        else (await ensure_shared_owner(session, household_id)).id
    )
    acct = Account(
        household_id=household_id,
        name=data.name,
        type=data.type,
        currency=data.currency.upper(),
        subtype=data.subtype,
        institution=data.institution,
        current_balance=data.current_balance,
        is_asset=is_asset_for(data.type),
        balance_source="derived" if data.type == "investment" else None,
        owner_id=owner_id,
        is_manual=True,
    )
    session.add(acct)
    await session.flush()
    # An opening balance is an observation only when someone gave one. The default
    # zero is a placeholder: snapshotting it put a $0 point at today in front of
    # every account opened to receive an imported statement, so the chart dropped
    # to zero on the day the account was created and the statement's own, older
    # balance could never become current.
    if is_set(data, "current_balance"):
        await record_balance(
            session, acct, balance=data.current_balance, on=data.balance_date or today()
        )
    return acct


async def list_accounts(session: AsyncSession,
                        owner_id: uuid.UUID | None = None) -> list[Account]:
    stmt = select(Account).order_by(Account.name)
    if owner_id is not None:
        # Plain equality: the column is NOT NULL, so there is no "unowned" case to
        # fold in and no inheritance to resolve (ADR-0026).
        stmt = stmt.where(Account.owner_id == owner_id)
    return list((await session.execute(stmt)).scalars().all())


#: How far a synced account's balance may lag its connection's last successful
#: sync before the account is called stale. A week, not a day or three: some
#: banks do not post a new balance over a weekend or a holiday.
STALE_AFTER = timedelta(days=7)


async def stale_since(session: AsyncSession) -> dict[uuid.UUID, date]:
    """Synced accounts the bank has stopped reporting → the date of their last
    balance.

    Its connection still syncs, but this account's balance has not moved with it:
    closed, or dropped by the bridge. The net-worth line carries that last balance
    forward — right while it is the last thing known, wrong once the account is
    gone — so the Accounts page says so, and hiding or closing it stays a person's
    call. No column: it is a comparison of two dates the ledger already holds.
    """
    rows = (
        await session.execute(
            select(Account.id, Account.balance_date, AccountConnection.last_synced_at)
            .join(AccountConnection, AccountConnection.id == Account.connection_id)
            .where(
                AccountConnection.last_synced_at.is_not(None),
                Account.balance_date.is_not(None),
            )
        )
    ).all()
    return {
        account_id: balance_date
        for account_id, balance_date, synced_at in rows
        if balance_date < synced_at.date() - STALE_AFTER
    }


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
    # is_set, not "is not None": an explicit null clears subtype/institution, and
    # the required fields are guarded by AccountUpdate's validator.
    for field in ("name", "subtype", "institution", "is_hidden"):
        if is_set(data, field):
            setattr(acct, field, getattr(data, field))
    if is_set(data, "type") and data.type != acct.type:
        await _retype(session, acct, data.type)
    if is_set(data, "owner_id"):
        # Resolved under RLS so a foreign id is a 404, not a foreign-key 500.
        acct.owner_id = (await get_owner(session, data.owner_id)).id
    await session.flush()
    if is_set(data, "current_balance") or is_set(data, "balance_date"):
        # A balance with no date is today's — never the account's last date, which
        # would put today's number on a day that already had one. A date with no
        # balance restates the current balance on that day.
        await record_balance(
            session,
            acct,
            balance=(
                data.current_balance
                if is_set(data, "current_balance")
                else acct.current_balance
            ),
            on=data.balance_date or today(),
        )
    return acct


async def _retype(session: AsyncSession, acct: Account, new_type: str) -> None:
    """Change an account's type — a correction sync's name-based guess needs.

    Balances are untouched: they are signed (ADR-0043), so a card mistyped as
    ``other`` already holds its debt as a negative number and only its label,
    ``is_asset``, changes. That is the reason the type could be made editable at
    all without rewriting history.

    An account leaving ``investment`` is refused while it has a holding or a
    trade: its value is those positions, and a depository account has nowhere to
    put them. One *becoming* an investment account keeps its balance history as a
    ``stated`` account — it has snapshots and no positions, and ``derived`` would
    value it from nothing.
    """
    if acct.type == "investment":
        held = (
            await session.execute(
                select(Holding.id).where(Holding.account_id == acct.id).limit(1)
            )
        ).first() or (
            await session.execute(
                select(InvestmentTransaction.id)
                .where(InvestmentTransaction.account_id == acct.id)
                .limit(1)
            )
        ).first()
        if held:
            raise LedgerError(
                "This account has holdings or investment transactions; remove them "
                "before changing it to a non-investment type.",
                409,
            )
    acct.type = new_type
    acct.is_asset = is_asset_for(new_type)
    acct.balance_source = "stated" if new_type == "investment" else None


async def delete_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    acct = await get_account(session, account_id)
    await session.delete(acct)
    await session.flush()


async def net_worth(session: AsyncSession, household_id: uuid.UUID,
                    owner_id: uuid.UUID | None = None):
    base = await base_currency(session, household_id)
    accounts = await list_accounts(session, owner_id)
    assets = Decimal("0")
    liabilities = Decimal("0")
    unconverted: set[str] = set()
    for a in accounts:
        if a.is_hidden:
            continue
        # Today's rate, not the balance's date's: this is the *current* view, and
        # ARCHITECTURE §2 values a current view at the latest rate. The chart's
        # point for today reads the same balance at the same rate, so the two agree.
        conv, _ = await fx.to_base(
            session, amount=a.current_balance, currency=a.currency,
            on=today(), base_ccy=base,
        )
        if conv is None:
            unconverted.add(a.currency)
            continue
        # Balances are signed (ADR-0043): debt is negative. `liabilities` is
        # reported as the amount owed, so it is the negation of what the
        # liability accounts sum to — and `assets - liabilities` is their sum.
        if a.is_asset:
            assets += conv
        else:
            liabilities -= conv
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


async def delete_category(session: AsyncSession, category_id: uuid.UUID) -> None:
    obj = (
        await session.execute(select(Category).where(Category.id == category_id))
    ).scalar_one_or_none()
    if obj is None:
        raise LedgerError("Category not found", 404)
    await session.delete(obj)
    await session.flush()


async def delete_category_group(session: AsyncSession, group_id: uuid.UUID) -> None:
    obj = (
        await session.execute(select(CategoryGroup).where(CategoryGroup.id == group_id))
    ).scalar_one_or_none()
    if obj is None:
        raise LedgerError("Category group not found", 404)
    await session.delete(obj)
    await session.flush()


async def delete_tag(session: AsyncSession, tag_id: uuid.UUID) -> None:
    obj = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if obj is None:
        raise LedgerError("Tag not found", 404)
    await session.delete(obj)
    await session.flush()


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
    """Recompute base_amount for foreign-currency transactions in the household.

    A split parent's children are re-allocated from its new base amount exactly as
    ``transactions.replace_splits`` allocates them, so they keep summing to it.
    They are what every report reads for a split, and without this a parent that
    gained a rate — a typed one, or the daily fetch (ADR-0046) — left children
    created without one at ``None``, out of cash flow for good.
    """
    base = await base_currency(session, household_id)
    txns = list(
        (
            await session.execute(
                select(Transaction)
                .where(Transaction.currency != base)
                .options(selectinload(Transaction.splits))
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
        if t.splits:
            realloc = (
                allocate(conv, [abs(sp.amount) or Decimal(1) for sp in t.splits], currency=base)
                if conv is not None
                else [None] * len(t.splits)
            )
            for split, base_amount in zip(t.splits, realloc, strict=True):
                split.base_amount = base_amount
        n += 1
    await session.flush()
    return n
