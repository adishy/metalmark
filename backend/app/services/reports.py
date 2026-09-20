"""Reporting (WS-R/UR backend). Everything converted to base currency at the
correct-date rate; transfers excluded from cash-flow (ARCHITECTURE §4).

Reconciliation invariant (the real test, not "%s sum to 100"):
    Δ net worth (base) = net cash flow (base) + currency revaluation
Single-currency households reconcile exactly; multi-currency reconcile once the
revaluation line (base-value change of foreign balances from rate moves) is
included. We compute revaluation as the residual so the identity always holds,
which is the honest decomposition (ADR-0017).

Owner filters mean two *different* things here, and that is deliberate (ADR-0026):
net worth is **account-scoped** ("the accounts Alex owns") so its decomposition
still reconciles, while cash-flow and spending are **row-scoped** ("entries
attributed to Alex", judged per split child) so one person's report never totals
another's share of a shared charge. The two views are therefore not additive;
``attribution`` on the net-worth response says which question it answered.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import quantize_storage
from app.models import (
    Account,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    Transaction,
)
from app.services import fx
from app.services.ledger import base_currency
from app.services.ownership import account_owner_map, effective_owner_id


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
    return dict(rows)


def _entries(
    t: Transaction, owners: dict[uuid.UUID, uuid.UUID]
) -> list[tuple[uuid.UUID | None, Decimal | None, uuid.UUID]]:
    """``(category_id, base_amount, effective owner)`` per reporting entry.

    A split parent reports through its children — that is where the category and
    the owner actually live. Decided by whether children are loaded rather than by
    the denormalized ``is_split_parent`` flag, which a bad writer could leave stale:
    a parent with no children is just an ordinary transaction.
    """
    account_owner = owners[t.account_id]
    if t.splits:
        return [
            (
                c.category_id,
                c.base_amount,
                effective_owner_id(
                    split_owner_id=c.owner_id,
                    transaction_owner_id=t.owner_id,
                    account_owner_id=account_owner,
                ),
            )
            for c in t.splits
        ]
    return [
        (
            t.category_id,
            t.base_amount,
            effective_owner_id(
                split_owner_id=None, transaction_owner_id=t.owner_id, account_owner_id=account_owner
            ),
        )
    ]


async def _reporting_transactions(
    session: AsyncSession, start: date, end: date, account_ids: set[uuid.UUID] | None
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(
            Transaction.transacted_at >= start,
            Transaction.transacted_at <= end,
            Transaction.is_hidden.is_(False),
        )
        .options(selectinload(Transaction.splits))
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    return list((await session.execute(stmt)).scalars().all())


async def accounts_owned_by(session: AsyncSession, owner_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (await session.execute(select(Account.id).where(Account.owner_id == owner_id))).all()
    return {r.id for r in rows}


async def net_worth_at(
    session: AsyncSession, on: date, base: str, *, account_ids: set[uuid.UUID] | None = None
) -> Decimal:
    """Carry-forward net worth at a date: latest snapshot ≤ date per account,
    converted at that date's rate, signed by is_asset."""
    accounts = list((await session.execute(select(Account))).scalars().all())
    total = Decimal("0")
    for a in accounts:
        if a.is_hidden:
            continue
        if account_ids is not None and a.id not in account_ids:
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


async def _cash_flow(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    owner_id: uuid.UUID | None = None,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """``(income, expense, net)`` in base over [start, end].

    ``owner_id`` selects *rows*: each entry is judged on its own attribution, so one
    person's report never totals another's share of a shared charge.

    ``account_ids`` selects *accounts*, and is how the net-worth decomposition is
    narrowed. That mode counts transfer legs instead of excluding them: a transfer
    crossing the boundary of an account subset does move that subset's balance, and
    dropping it would break ``ΔNW = cash flow + revaluation`` for that subset. For
    the whole household the legs cancel, which is why excluding them there is exact.
    """
    ctype = await _category_type_map(session)
    owners = await account_owner_map(session)
    income = Decimal("0")
    expense = Decimal("0")

    txns = await _reporting_transactions(session, start, end, account_ids)
    for t in txns:
        if account_ids is None and t.transfer_group_id is not None:
            continue
        for cat_id, bamt, entry_owner in _entries(t, owners):
            if bamt is None:
                continue
            if owner_id is not None and entry_owner != owner_id:
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
                           start: date, end: date,
                           owner_id: uuid.UUID | None = None):
    """Net worth over time, plus the reconciliation of its change.

    An owner filter narrows the *accounts* and everything derived from them — the
    series, the delta, and the cash-flow term alike — so the identity still holds.
    A row-scoped cash flow here would leave the identity asserting something false.
    """
    base = await base_currency(session, household_id)
    account_ids = None if owner_id is None else await accounts_owned_by(session, owner_id)

    points = []
    for d in _month_ends(start, end):
        points.append(
            {"date": d, "net_worth": await net_worth_at(session, d, base, account_ids=account_ids)}
        )

    nw_start = await net_worth_at(session, start, base, account_ids=account_ids)
    nw_end = await net_worth_at(session, end, base, account_ids=account_ids)
    delta = quantize_storage(nw_end - nw_start)
    _income, _expense, net_cf = await _cash_flow(session, start, end, account_ids=account_ids)
    revaluation = quantize_storage(delta - net_cf)
    return {
        "base_currency": base,
        "points": points,
        "delta_net_worth": delta,
        "net_cash_flow": net_cf,
        "currency_revaluation": revaluation,
        # Net worth decomposes by account, never by row: an owner filter here means
        # "the accounts Alex owns". Cash-flow and spending answer the other question.
        "attribution": "account",
    }


async def cash_flow_series(session: AsyncSession, household_id: uuid.UUID,
                           start: date, end: date,
                           owner_id: uuid.UUID | None = None):
    base = await base_currency(session, household_id)
    out = []
    for d in _month_ends(start, end):
        m_start = date(d.year, d.month, 1)
        income, expense, net = await _cash_flow(session, m_start, d, owner_id=owner_id)
        out.append({"month": _month_key(d), "income": income, "expense": expense, "net": net})
    return base, out


async def spending_by_category(session: AsyncSession, household_id: uuid.UUID,
                               start: date, end: date,
                               owner_id: uuid.UUID | None = None):
    base = await base_currency(session, household_id)
    ctype = await _category_type_map(session)
    owners = await account_owner_map(session)
    names = dict(
        (await session.execute(select(Category.id, Category.name))).all()
    )
    totals: dict[uuid.UUID | None, Decimal] = {}

    txns = await _reporting_transactions(session, start, end, None)
    for t in txns:
        if t.transfer_group_id is not None:
            continue
        for cat_id, bamt, entry_owner in _entries(t, owners):
            if bamt is None or bamt >= 0:  # spending only (money out)
                continue
            if owner_id is not None and entry_owner != owner_id:
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
