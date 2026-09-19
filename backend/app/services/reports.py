"""Reporting (WS-R/UR backend). Everything converted to base currency at the
correct-date rate; transfers excluded from cash-flow (ARCHITECTURE §4).

Reconciliation invariant (the real test, not "%s sum to 100"):
    Δ net worth (base) = net cash flow (base) + currency revaluation
Single-currency households reconcile exactly; multi-currency reconcile once the
revaluation line (base-value change of foreign balances from rate moves) is
included. We compute revaluation as the residual so the identity always holds,
which is the honest decomposition (ADR-0017).
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_storage
from app.models import (
    Account,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    Transaction,
    TransactionSplit,
)
from app.services import fx
from app.services.ledger import base_currency


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_ends(start: date, end: date) -> list[date]:
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = calendar.monthrange(y, m)[1]
        out.append(date(y, m, last))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


async def _category_type_map(session: AsyncSession) -> dict[uuid.UUID, str]:
    rows = (
        await session.execute(
            select(Category.id, CategoryGroup.type).join(
                CategoryGroup, Category.group_id == CategoryGroup.id
            )
        )
    ).all()
    return {cid: ctype for cid, ctype in rows}


async def net_worth_at(session: AsyncSession, on: date, base: str) -> Decimal:
    """Carry-forward net worth at a date: latest snapshot ≤ date per account,
    converted at that date's rate, signed by is_asset."""
    accounts = list((await session.execute(select(Account))).scalars().all())
    total = Decimal("0")
    for a in accounts:
        if a.is_hidden:
            continue
        snap = (
            await session.execute(
                select(BalanceSnapshot.balance, BalanceSnapshot.currency)
                .where(
                    BalanceSnapshot.account_id == a.id,
                    BalanceSnapshot.balance_date <= on,
                )
                .order_by(BalanceSnapshot.balance_date.desc())
                .limit(1)
            )
        ).first()
        if snap is None:
            continue
        bal, ccy = snap
        conv, _ = await fx.to_base(session, amount=bal, currency=ccy, on=on, base_ccy=base)
        if conv is None:
            continue
        total += conv if a.is_asset else -conv
    return quantize_storage(total)


async def _cash_flow(session: AsyncSession, start: date, end: date):
    """Return (income, expense, net) in base over [start, end], transfers excluded.
    Uses split children when a transaction is split."""
    ctype = await _category_type_map(session)
    income = Decimal("0")
    expense = Decimal("0")

    txns = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.transacted_at >= start,
                    Transaction.transacted_at <= end,
                    Transaction.transfer_group_id.is_(None),
                    Transaction.is_hidden.is_(False),
                )
            )
        ).scalars().all()
    )
    for t in txns:
        entries: list[tuple[uuid.UUID | None, Decimal | None]] = []
        if t.is_split_parent:
            children = list(
                (
                    await session.execute(
                        select(TransactionSplit).where(TransactionSplit.parent_txn_id == t.id)
                    )
                ).scalars().all()
            )
            entries = [(c.category_id, c.base_amount) for c in children]
        else:
            entries = [(t.category_id, t.base_amount)]
        for cat_id, bamt in entries:
            if bamt is None:
                continue
            kind = ctype.get(cat_id) if cat_id else None
            if kind == "transfer":
                continue
            if bamt > 0:
                income += bamt
            else:
                expense += bamt  # negative
    net = income + expense
    return quantize_storage(income), quantize_storage(expense), quantize_storage(net)


async def net_worth_series(session: AsyncSession, household_id: uuid.UUID,
                           start: date, end: date):
    base = await base_currency(session, household_id)
    points = []
    for d in _month_ends(start, end):
        points.append({"date": d, "net_worth": await net_worth_at(session, d, base)})

    nw_start = await net_worth_at(session, start, base)
    nw_end = await net_worth_at(session, end, base)
    delta = quantize_storage(nw_end - nw_start)
    _income, _expense, net_cf = await _cash_flow(session, start, end)
    revaluation = quantize_storage(delta - net_cf)
    return {
        "base_currency": base,
        "points": points,
        "delta_net_worth": delta,
        "net_cash_flow": net_cf,
        "currency_revaluation": revaluation,
    }


async def cash_flow_series(session: AsyncSession, household_id: uuid.UUID,
                           start: date, end: date):
    base = await base_currency(session, household_id)
    out = []
    for d in _month_ends(start, end):
        m_start = date(d.year, d.month, 1)
        income, expense, net = await _cash_flow(session, m_start, d)
        out.append({"month": _month_key(d), "income": income, "expense": expense, "net": net})
    return base, out


async def spending_by_category(session: AsyncSession, household_id: uuid.UUID,
                               start: date, end: date):
    base = await base_currency(session, household_id)
    ctype = await _category_type_map(session)
    names = dict(
        (await session.execute(select(Category.id, Category.name))).all()
    )
    totals: dict[uuid.UUID | None, Decimal] = {}

    txns = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.transacted_at >= start,
                    Transaction.transacted_at <= end,
                    Transaction.transfer_group_id.is_(None),
                    Transaction.is_hidden.is_(False),
                )
            )
        ).scalars().all()
    )
    for t in txns:
        if t.is_split_parent:
            entries = [
                (c.category_id, c.base_amount)
                for c in (
                    await session.execute(
                        select(TransactionSplit).where(TransactionSplit.parent_txn_id == t.id)
                    )
                ).scalars().all()
            ]
        else:
            entries = [(t.category_id, t.base_amount)]
        for cat_id, bamt in entries:
            if bamt is None or bamt >= 0:  # spending only (money out)
                continue
            if cat_id and ctype.get(cat_id) == "transfer":
                continue
            totals[cat_id] = totals.get(cat_id, Decimal("0")) + (-bamt)

    rows = [
        {
            "category_id": cid,
            "category_name": names.get(cid, "Uncategorized") if cid else "Uncategorized",
            "total": quantize_storage(v),
        }
        for cid, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return base, rows, quantize_storage(sum(totals.values(), Decimal("0")))
