"""Transactions, splits, transfers (WS-L core).

Provenance (``field_sources``): manual writes tag touched fields ``user`` so a
future sync/rule can never clobber them (ADR-0007/0019). ``base_amount`` is a
cache filled from the FX service. Listing is keyset-paginated (perf).
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import allocate, minor_unit, quantize_storage
from app.models import (
    Account,
    Transaction,
    TransactionSplit,
    TransactionTag,
    TransferGroup,
)
from app.schemas.patch import is_set
from app.schemas.transactions import SplitIn, TransactionCreate, TransactionUpdate
from app.services import fx, rules
from app.services.errors import LedgerError
from app.services.ledger import base_currency
from app.services.ownership import require_owners


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
                             data: TransactionCreate,
                             *, source: str = "manual",
                             rules_loaded: rules.LoadedRules | None = None) -> Transaction:
    """Create one transaction, then run the household's rules over it.

    ``source`` records where the row came from (``manual`` by hand, ``csv`` from an
    import file, ``simplefin`` from sync). It does not change provenance: everything
    below is still a ``user`` decision, because a human supplied the values either
    way. The distinction that matters for sync is ADR-0019's manual-origin boundary,
    which keys off ``external_id`` — an imported row has none, so sync never
    overwrites it.

    Rules run here rather than at each caller because *every* way a row arrives is
    supposed to get them (ARCHITECTURE §4: sync "runs the rules engine on newly
    ingested transactions"), and three call sites applying them independently is
    three chances to forget. The engine is provenance-gated, so a value the caller
    set above is marked ``user`` and no rule may touch it — a row the human
    categorized comes back exactly as they typed it.

    ``rules_loaded`` is the batching hook: a caller inserting many rows — the CSV
    importer — loads and compiles the rules once and passes them in, which takes
    the per-row cost to zero when the household has no rules that tag.
    """
    acct = await _account(session, data.account_id)
    await require_owners(session, [data.owner_id])
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
    if data.owner_id is not None:
        # The key stays "owner" — provenance is keyed by field, and the field is
        # still the owner, whatever column backs it (ADR-0007/0026).
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
        owner_id=data.owner_id,
        is_pending=data.is_pending,
        # a human entering a categorized txn has effectively reviewed it
        review_status="reviewed" if data.category_id else "needs_review",
        notes=data.notes,
        source=source,
        field_sources=field_sources,
    )
    session.add(txn)
    await session.flush()
    if data.tag_ids:
        await _set_tags(session, txn.id, data.tag_ids)
    await session.flush()
    # Last, and after the caller's own tags are on the row: the engine reads the
    # existing tags to union its own onto them, so running it first would let
    # ``_set_tags`` above overwrite what a rule had just added. It flushes what it
    # changes, so the caller reads back the row the rules produced.
    await rules.apply_to_transaction(session, household_id, txn, loaded=rules_loaded)
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

    # ``is_set`` rather than ``is not None``: for the nullable fields an explicit
    # null is how a client clears a value, and treating it as "absent" made a PATCH
    # that unset an owner silently do nothing. The required fields are guarded by
    # TransactionUpdate's validator, so arriving here they are non-null.
    if is_set(data, "amount"):
        txn.amount = data.amount
        _mark(fs, ["amount"], "user")
        recompute = True
    if is_set(data, "transacted_at"):
        txn.transacted_at = data.transacted_at
        recompute = True
    if is_set(data, "posted_at"):
        txn.posted_at = data.posted_at
    if is_set(data, "description"):
        txn.description = data.description
    if is_set(data, "merchant"):
        txn.merchant = data.merchant
        _mark(fs, ["merchant"], "user")
    if is_set(data, "category_id"):
        txn.category_id = data.category_id
        _mark(fs, ["category"], "user")
    if is_set(data, "owner_id"):
        # null = inherit the account's owner; "owner" is still user-sourced either
        # way, so a rule or the sync writer cannot reattribute it later.
        await require_owners(session, [data.owner_id])
        txn.owner_id = data.owner_id
        _mark(fs, ["owner"], "user")
    if is_set(data, "is_pending"):
        txn.is_pending = data.is_pending
    if is_set(data, "is_hidden"):
        txn.is_hidden = data.is_hidden
        _mark(fs, ["is_hidden"], "user")
    if is_set(data, "review_status"):
        txn.review_status = data.review_status
        _mark(fs, ["review_status"], "user")
    if is_set(data, "notes"):
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
    await require_owners(session, [s.owner_id for s in splits])
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
                category_id=s.category_id, owner_id=s.owner_id, notes=s.notes,
            )
        )
    txn.is_split_parent = True
    fs = dict(txn.field_sources or {})
    _mark(fs, ["splits"], "user")
    txn.field_sources = fs
    await session.flush()
    return await get_transaction(session, txn_id)


# ---- Transfers ------------------------------------------------------------

# How far apart two legs' base amounts may sit and still be called a match, as a
# fraction of the amount being matched. It exists because FX moves: a
# cross-currency pair is never equal-and-opposite in native terms (ADR-0008's own
# cost note), and the bank's rate on the day lands either side of the ledger's, so
# equality would fail to match exactly the pairs ADR-0018 was written for. The
# floor keeps a transfer small enough that the percentage rounds away from having
# a zero-width window.
_FX_MATCH_TOLERANCE = Decimal("0.02")

# Candidates feed a picker, not a report. ``days`` already bounds the time window;
# capping the rows too is what keeps the endpoint's cost independent of how much
# history the household has accumulated — a busy checking account inside a
# 30-day window would otherwise return every one of its postings.
CANDIDATE_LIMIT = 25


def _residual_base(a: Transaction, b: Transaction) -> Decimal | None:
    """Σ base_amount of two legs: what linking them stores as ``fx_cost_base``.

    The whole of ADR-0018 is in this sum. Same-currency legs cancel to exactly 0,
    because ``base_amount`` is the same native number converted the same way;
    cross-currency legs leave the spread the bank took, which the cash-flow
    exclusion would otherwise swallow. ``None`` means "no residual to report" —
    either the sum really is 0, or a leg has no rate at all (ADR-0017's "no rate"
    flag, never a silent 0).

    Both ``link_transfer`` and the candidate picker call this, so the number a
    user accepts before linking is by construction the number they end up with.
    """
    if a.base_amount is None or b.base_amount is None:
        return None
    residual = quantize_storage(a.base_amount + b.base_amount)
    return residual if residual != 0 else None


def _matches_on_amounts(subject: Transaction, other: Transaction,
                        residual: Decimal | None, tol: Decimal) -> bool:
    """Whether these two legs "match" in amount terms (ADR-0018).

    Same-currency legs are compared in their own currency — equal-and-opposite or
    not, with no tolerance, because there is no rate between them that could have
    moved. Only cross-currency legs need one, and they match when the residual is
    inside it. A residual of ``None`` is not a match: with no rate on a leg there
    is nothing to compare, and answering "unknown" with "matches" is the silent
    zero the FX module exists to prevent.
    """
    if subject.currency == other.currency:
        return subject.amount + other.amount == 0
    if residual is None:
        return False
    return abs(residual) <= tol


async def link_transfer(session: AsyncSession, household_id: uuid.UUID,
                        from_txn_id: uuid.UUID, to_txn_id: uuid.UUID) -> TransferGroup:
    a = await get_transaction(session, from_txn_id)
    b = await get_transaction(session, to_txn_id)
    if a.account_id == b.account_id:
        raise LedgerError("A transfer must span two different accounts", 400)
    if (a.amount > 0) == (b.amount > 0):
        raise LedgerError("Transfer legs must have opposite signs", 400)

    # Residual base value: 0 for same-currency, the FX spread for cross-currency.
    group = TransferGroup(household_id=household_id, matched_by="manual",
                          fx_cost_base=_residual_base(a, b))
    session.add(group)
    await session.flush()
    a.transfer_group_id = group.id
    b.transfer_group_id = group.id
    await session.flush()
    return group


async def _transfer_group(session: AsyncSession, household_id: uuid.UUID,
                          group_id: uuid.UUID) -> TransferGroup:
    """One household's transfer group, or a 404.

    Household-scoped twice over — the explicit predicate and RLS — so another
    household's group reads as missing rather than as someone else's data.
    """
    group = (
        await session.execute(
            select(TransferGroup).where(
                TransferGroup.id == group_id,
                TransferGroup.household_id == household_id,
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise LedgerError("Transfer group not found", 404)
    return group


async def get_transfer_group(session: AsyncSession, household_id: uuid.UUID,
                             group_id: uuid.UUID) -> tuple[TransferGroup, list[Transaction]]:
    """A group and its legs, for a detail view that has to show both sides.

    Ordered by date then id, so the pair reads the same way on every load — a
    sheet whose legs reshuffled between fetches would look like the data had
    changed underneath the user. The legs carry their splits, because they are
    serialized through the same transaction shape as everywhere else.
    """
    group = await _transfer_group(session, household_id, group_id)
    legs = (
        await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.splits))
            .where(Transaction.transfer_group_id == group_id)
            .order_by(Transaction.transacted_at.asc(), Transaction.id.asc())
        )
    ).scalars().all()
    return group, list(legs)


async def unlink_transfer(session: AsyncSession, household_id: uuid.UUID,
                          group_id: uuid.UUID) -> None:
    """Undo a link: both legs become ordinary transactions again.

    This is what makes ADR-0008's exclusion safe to apply at all. The exclusion is
    a property of the *link*, never of the rows — every report asks "is this in a
    transfer group?" — so removing the link is the whole of the repair and the
    legs return to cash-flow and spending with no other trace. The group row goes
    with it: a group with no legs is not a state this model has, and leaving an
    empty one behind would make every later "is this pair already linked?" query
    answer yes for a link that no longer exists.
    """
    group = await _transfer_group(session, household_id, group_id)

    # Cleared through the loaded objects, exactly as ``link_transfer`` sets them.
    # A Core UPDATE would leave an already-loaded leg in this session still
    # carrying the group id, so a report or a re-serialization later in the same
    # request would still exclude it — the unlink would appear to have worked
    # while the numbers said otherwise.
    legs = (
        await session.execute(
            select(Transaction).where(Transaction.transfer_group_id == group_id)
        )
    ).scalars().all()
    for leg in legs:
        leg.transfer_group_id = None
    await session.flush()  # drop the references before the row they point at
    await session.delete(group)
    await session.flush()


@dataclass(frozen=True)
class TransferCandidate:
    """A row that could be the subject's other leg, priced.

    A dataclass rather than the bare ORM row because the useful part is what is
    *derived from the pair* — how far apart in time the legs are, and the residual
    linking would store — and neither can be read off one row alone. ``txn`` is
    serialized through the ordinary transaction shape, so a candidate renders in
    the UI exactly like any other transaction.
    """

    txn: Transaction
    days_apart: int
    fx_cost_base: Decimal | None
    within_tolerance: bool


async def list_transfer_candidates(
    session: AsyncSession,
    household_id: uuid.UUID,
    txn_id: uuid.UUID,
    *,
    days: int = 5,
    limit: int = CANDIDATE_LIMIT,
) -> list[TransferCandidate]:
    """Counterpart legs for ``txn_id``, best first, each with the cost of linking it.

    The filters are ``link_transfer``'s own rules — a different account, opposite
    signs — plus the two that make a row a *candidate*: not the subject itself,
    and not already spoken for by another group. A row that survives the query is
    therefore a row the link endpoint would accept, which is the property that
    keeps the picker from offering choices that then fail.

    Ordering is time first, base amount second: a transfer posts within days of
    its other leg (ARCHITECTURE §3 "within a few days"), so proximity in time is
    the stronger signal and the base-amount gap only breaks ties inside a date.
    Rows whose base amount is unknown sort last within their date rather than
    dropping out — "we cannot price this" is not "this is wrong".

    Hidden rows are included on purpose: hiding is a decision about the list, not
    a claim that a row is not half of a transfer.
    """
    subject = await get_transaction(session, txn_id)
    base = await base_currency(session, household_id)
    # The tolerance is a share of the amount being matched. With no base amount on
    # the subject there is no scale to take a share of, and every residual below
    # comes out None anyway — the minor-unit floor is what that degenerate case
    # falls back to.
    tol = max(abs(subject.base_amount or Decimal(0)) * _FX_MATCH_TOLERANCE, minor_unit(base))

    window = timedelta(days=days)
    conds = [
        Transaction.id != subject.id,
        Transaction.account_id != subject.account_id,
        # A leg already in a group is not free to match again, and a second link
        # would strand the group it is already in.
        Transaction.transfer_group_id.is_(None),
        Transaction.transacted_at >= subject.transacted_at - window,
        Transaction.transacted_at <= subject.transacted_at + window,
    ]
    # The complement of the sign test ``link_transfer`` rejects on, written as a
    # range so the two can never drift apart. ``amount > 0`` rather than ``< 0`` is
    # deliberate: that is the predicate over there, and a zero amount falls on its
    # negative side in both places.
    if subject.amount > 0:
        conds.append(Transaction.amount <= 0)
    else:
        conds.append(Transaction.amount > 0)

    # Distance as a number rather than an interval difference: "within N days" is
    # the only meaning the window has, and it sorts.
    distance = func.abs(extract("epoch", Transaction.transacted_at - subject.transacted_at))
    order = [distance.asc()]
    if subject.base_amount is not None:
        # NULLS LAST falls out of the arithmetic: a leg with no base amount cannot
        # be measured against the subject's, so it cannot be "closest".
        order.append(
            func.abs(Transaction.base_amount + subject.base_amount).asc().nulls_last()
        )
    order.append(Transaction.id.asc())  # a total order, so the cap is deterministic

    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.splits))
        .where(and_(*conds))
        .order_by(*order)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    candidates = []
    for row in rows:
        residual = _residual_base(subject, row)
        candidates.append(
            TransferCandidate(
                txn=row,
                days_apart=abs(row.transacted_at - subject.transacted_at).days,
                fx_cost_base=residual,
                within_tolerance=_matches_on_amounts(subject, row, residual, tol),
            )
        )
    return candidates


# ---- Listing (keyset pagination) ------------------------------------------

def _owner_predicate(owner_id: uuid.UUID):
    """Transactions this owner is effectively attributed to, for a query that has
    already joined ``accounts``.

    Two branches, because a split parent and a plain transaction answer the
    question differently — and the ``NOT has_children`` guard is load-bearing:
    without it, the plain branch matches every split parent whose *account* the
    owner holds, so filtering by one person would return a shared card's charges
    that were split entirely to somebody else.

    Parent/child is decided by ``EXISTS (child)`` rather than the denormalized
    ``is_split_parent`` flag, which a bad writer could leave stale — a flag saying
    "parent" with no children rows would make the transaction invisible to every
    owner filter.
    """
    # coalesce, not a nullable compare: accounts.owner_id is NOT NULL, so an
    # unset transaction owner always resolves to something.
    inherited = func.coalesce(Transaction.owner_id, Account.owner_id)
    has_children = (
        select(TransactionSplit.id)
        .where(TransactionSplit.parent_txn_id == Transaction.id)
        .exists()
    )
    child_matches = (
        select(TransactionSplit.id)
        .where(
            TransactionSplit.parent_txn_id == Transaction.id,
            # A child with no owner of its own inherits through the parent.
            func.coalesce(TransactionSplit.owner_id, inherited) == owner_id,
        )
        .exists()
    )
    return or_(
        and_(~has_children, inherited == owner_id),
        and_(has_children, child_matches),
    )


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
    owner_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    review_status: str | None = None,
    search: str | None = None,
    include_hidden: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Transaction], str | None]:
    stmt = select(Transaction).options(selectinload(Transaction.splits))
    if owner_id is not None:
        # Many-to-one on a NOT NULL FK, so this join cannot duplicate rows.
        stmt = stmt.join(Account, Account.id == Transaction.account_id)
    conds = []
    if account_ids:
        conds.append(Transaction.account_id.in_(account_ids))
    if owner_id is not None:
        conds.append(_owner_predicate(owner_id))
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
