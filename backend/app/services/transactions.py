"""Transactions, splits, transfers (WS-L core).

Provenance (``field_sources``): manual writes tag touched fields ``user`` so a
future sync/rule can never clobber them (ADR-0007/0019). ``base_amount`` is a
cache filled from the FX service. Listing is keyset-paginated (perf).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import allocate
from app.models import (
    Account,
    Transaction,
    TransactionSplit,
    TransactionTag,
    TransferGroup,
)
from app.schemas.transactions import SplitIn, TransactionCreate, TransactionUpdate
from app.services import fx
from app.services.ledger import LedgerError, base_currency


def _mark(field_sources: dict, fields: list[str], origin: str = "user") -> None:
    for f in fields:
        field_sources[f] = origin


async def _account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    acct = (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if acct is None:
        raise LedgerError("Account not found", 404)
    return acct


async def _set_tags(session: AsyncSession, txn_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> None:
    await session.execute(delete(TransactionTag).where(TransactionTag.transaction_id == txn_id))
    for tid in tag_ids:
        session.add(TransactionTag(transaction_id=txn_id, tag_id=tid))


async def _tag_ids_for(session: AsyncSession, txn_ids: list[uuid.UUID]) -> dict:
    if not txn_ids:
        return {}
    rows = (
        await session.execute(
            select(TransactionTag.transaction_id, TransactionTag.tag_id).where(
                TransactionTag.transaction_id.in_(txn_ids)
            )
        )
    ).all()
    out: dict = {}
    for txn_id, tag_id in rows:
        out.setdefault(txn_id, []).append(tag_id)
    return out


async def create_transaction(session: AsyncSession, household_id: uuid.UUID,
                             data: TransactionCreate) -> Transaction:
    acct = await _account(session, data.account_id)
    base = await base_currency(session, household_id)
    conv, rate_date = await fx.to_base(
        session, amount=data.amount, currency=acct.currency,
        on=data.transacted_at.date(), base_ccy=base,
    )
    field_sources: dict = {}
    _mark(field_sources, ["amount"], "user")
    if data.category_id is not None:
        _mark(field_sources, ["category"], "user")
    if data.merchant is not None:
        _mark(field_sources, ["merchant"], "user")
    if data.owner_user_id is not None:
        _mark(field_sources, ["owner"], "user")

    txn = Transaction(
        household_id=household_id,
        account_id=acct.id,
        amount=data.amount,
        currency=acct.currency,
        base_amount=conv,
        fx_rate_date=rate_date,
        transacted_at=data.transacted_at,
        posted_at=data.posted_at,
        description=data.description,
        merchant=data.merchant,
        category_id=data.category_id,
        owner_user_id=data.owner_user_id,
        is_pending=data.is_pending,
        # a human entering a categorized txn has effectively reviewed it
        review_status="reviewed" if data.category_id else "needs_review",
        notes=data.notes,
        source="manual",
        field_sources=field_sources,
    )
    session.add(txn)
    await session.flush()
    if data.tag_ids:
        await _set_tags(session, txn.id, data.tag_ids)
    await session.flush()
    return txn


async def get_transaction(session: AsyncSession, txn_id: uuid.UUID) -> Transaction:
    txn = (
        await session.execute(
            select(Transaction).where(Transaction.id == txn_id).options(
                selectinload(Transaction.splits)
            )
        )
    ).scalar_one_or_none()
    if txn is None:
        raise LedgerError("Transaction not found", 404)
    return txn


async def update_transaction(session: AsyncSession, household_id: uuid.UUID,
                             txn_id: uuid.UUID, data: TransactionUpdate) -> Transaction:
    txn = await get_transaction(session, txn_id)
    fs = dict(txn.field_sources or {})
    recompute = False

    if data.amount is not None:
        txn.amount = data.amount
        _mark(fs, ["amount"], "user")
        recompute = True
    if data.transacted_at is not None:
        txn.transacted_at = data.transacted_at
        recompute = True
    if data.posted_at is not None:
        txn.posted_at = data.posted_at
    if data.description is not None:
        txn.description = data.description
    if data.merchant is not None:
        txn.merchant = data.merchant
        _mark(fs, ["merchant"], "user")
    if data.category_id is not None:
        txn.category_id = data.category_id
        _mark(fs, ["category"], "user")
    if data.owner_user_id is not None:
        txn.owner_user_id = data.owner_user_id
        _mark(fs, ["owner"], "user")
    if data.is_pending is not None:
        txn.is_pending = data.is_pending
    if data.is_hidden is not None:
        txn.is_hidden = data.is_hidden
        _mark(fs, ["is_hidden"], "user")
    if data.review_status is not None:
        txn.review_status = data.review_status
        _mark(fs, ["review_status"], "user")
    if data.notes is not None:
        txn.notes = data.notes

    txn.field_sources = fs

    if recompute:
        base = await base_currency(session, household_id)
        acct = await _account(session, txn.account_id)
        conv, rate_date = await fx.to_base(
            session, amount=txn.amount, currency=acct.currency,
            on=txn.transacted_at.date(), base_ccy=base,
        )
        txn.base_amount = conv
        txn.fx_rate_date = rate_date

    if data.tag_ids is not None:
        await _set_tags(session, txn.id, data.tag_ids)

    await session.flush()
    return txn


async def delete_transaction(session: AsyncSession, txn_id: uuid.UUID) -> None:
    txn = await get_transaction(session, txn_id)
    await session.delete(txn)
    await session.flush()


# ---- Splits ---------------------------------------------------------------

async def replace_splits(session: AsyncSession, txn_id: uuid.UUID,
                         splits: list[SplitIn]) -> Transaction:
    txn = await get_transaction(session, txn_id)
    if not splits:
        # un-split (delete-orphan cascade removes children)
        txn.splits.clear()
        txn.is_split_parent = False
        await session.flush()
        return await get_transaction(session, txn_id)

    use_pct = any(s.pct is not None for s in splits)
    if use_pct:
        if not all(s.pct is not None for s in splits):
            raise LedgerError("Mix of pct and amount splits is not allowed", 400)
        weights = [s.pct for s in splits]
        native = allocate(txn.amount, weights, currency=txn.currency)
    else:
        native = [s.amount for s in splits]
        if sum(native, Decimal(0)) != txn.amount:
            raise LedgerError("Split amounts must sum to the transaction amount", 400)

    # Allocate base_amount so children sum EXACTLY to the parent base (no drift).
    if txn.base_amount is not None:
        base_alloc = allocate(txn.base_amount, [abs(a) or Decimal(1) for a in native],
                              currency=(await base_currency(session, txn.household_id)))
    else:
        base_alloc = [None] * len(native)

    txn.splits.clear()
    await session.flush()  # apply delete-orphan before inserting the new set
    for s, amt, bamt in zip(splits, native, base_alloc, strict=True):
        txn.splits.append(
            TransactionSplit(
                amount=amt, base_amount=bamt,
                category_id=s.category_id, owner_user_id=s.owner_user_id, notes=s.notes,
            )
        )
    txn.is_split_parent = True
    fs = dict(txn.field_sources or {})
    _mark(fs, ["splits"], "user")
    txn.field_sources = fs
    await session.flush()
    return await get_transaction(session, txn_id)


# ---- Transfers ------------------------------------------------------------

async def link_transfer(session: AsyncSession, household_id: uuid.UUID,
                        from_txn_id: uuid.UUID, to_txn_id: uuid.UUID) -> TransferGroup:
    a = await get_transaction(session, from_txn_id)
    b = await get_transaction(session, to_txn_id)
    if a.account_id == b.account_id:
        raise LedgerError("A transfer must span two different accounts", 400)
    if (a.amount > 0) == (b.amount > 0):
        raise LedgerError("Transfer legs must have opposite signs", 400)

    # Residual base value: 0 for same-currency, the FX spread for cross-currency.
    fx_cost = None
    if a.base_amount is not None and b.base_amount is not None:
        residual = a.base_amount + b.base_amount
        fx_cost = residual if residual != 0 else None

    group = TransferGroup(household_id=household_id, matched_by="manual", fx_cost_base=fx_cost)
    session.add(group)
    await session.flush()
    a.transfer_group_id = group.id
    b.transfer_group_id = group.id
    await session.flush()
    return group


# ---- Listing (keyset pagination) ------------------------------------------

def _encode_cursor(transacted_at: datetime, txn_id: uuid.UUID) -> str:
    raw = json.dumps({"t": transacted_at.isoformat(), "id": str(txn_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return datetime.fromisoformat(raw["t"]), uuid.UUID(raw["id"])


async def list_transactions(
    session: AsyncSession,
    *,
    account_ids: list[uuid.UUID] | None = None,
    category_ids: list[uuid.UUID] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    review_status: str | None = None,
    search: str | None = None,
    include_hidden: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Transaction], str | None]:
    stmt = select(Transaction).options(selectinload(Transaction.splits))
    conds = []
    if account_ids:
        conds.append(Transaction.account_id.in_(account_ids))
    if category_ids:
        conds.append(Transaction.category_id.in_(category_ids))
    if start:
        conds.append(Transaction.transacted_at >= start)
    if end:
        conds.append(Transaction.transacted_at <= end)
    if review_status:
        conds.append(Transaction.review_status == review_status)
    if not include_hidden:
        conds.append(Transaction.is_hidden.is_(False))
    if search:
        like = f"%{search}%"
        conds.append(or_(Transaction.description.ilike(like), Transaction.merchant.ilike(like)))
    if cursor:
        c_t, c_id = _decode_cursor(cursor)
        conds.append(
            or_(
                Transaction.transacted_at < c_t,
                and_(Transaction.transacted_at == c_t, Transaction.id < c_id),
            )
        )
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(Transaction.transacted_at.desc(), Transaction.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(last.transacted_at, last.id)
    return rows, next_cursor
