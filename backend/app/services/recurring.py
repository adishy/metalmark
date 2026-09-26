"""Recurring series and the detector behind them (ADR-0053).

Two halves:

* **The series itself** — CRUD over what the household states happens regularly,
  plus the read-time derivation of what it currently matches. A series owns no
  transactions; "which ones are mine" is answered by ``_matches`` against the
  ledger on every read, so editing a series never rewrites history and deleting
  one never orphans a transaction.
* **The detector** — ``suggestions`` groups the household's own transactions and
  offers the ones that already look regular. Nothing is stored: a suggestion is
  a proposal, and it stops being offered as soon as a series covers it. That is
  the "pick which transactions are recurring" flow — the person confirms, the
  server never guesses on their behalf.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import Account, Category, Transaction
from app.models.recurring import RecurringSeries
from app.schemas.patch import is_set
from app.schemas.recurring import (
    RecurringCreate,
    RecurringListOut,
    RecurringOut,
    RecurringSuggestionOut,
    RecurringTotalsOut,
    RecurringUpdate,
)
from app.services.errors import LedgerError
from app.services.ledger import base_currency

#: Annual-equivalent multiplier per cadence — the same convention, and the same
#: values, ``services/income.ANNUAL_MULTIPLIER`` states for a pay frequency
#: (ADR-0052); the two extra cadences are the ones a bill keeps to and a
#: paycheque does not. A test pins the five shared names equal, so the two
#: features cannot drift apart on what "biweekly" is worth.
CADENCE_ANNUAL_MULTIPLIER = {
    "weekly": 52,
    "biweekly": 26,
    "semimonthly": 24,
    "monthly": 12,
    "quarterly": 4,
    "semiannual": 2,
    "annual": 1,
}

#: Nominal length of one period, in days. Used to read a cadence off the gaps
#: the detector finds, and to project the next due date.
CADENCE_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "semimonthly": 15,
    "monthly": 30,
    "quarterly": 91,
    "semiannual": 182,
    "annual": 365,
}

#: How far a median gap may sit from a cadence's nominal length, as a fraction —
#: so "monthly" accepts 23-37 days, which is a real bill moving over a
#: weekend, and a 60-day gap is nobody's monthly.
_CADENCE_TOLERANCE = Decimal("0.25")
#: How much the individual gaps may spread around their median: at least 3 days
#: (a weekly series shifting by a day), and never more than 35% of the median
#: (a monthly series drifting a week is not monthly).
_GAP_TOLERANCE_RATIO = Decimal("0.35")
_GAP_TOLERANCE_FLOOR_DAYS = 3
#: Amounts may vary — a utility bill does — by 15% or a dollar, whichever is
#: larger. A series whose amounts swing by more than that is not one pattern.
_AMOUNT_TOLERANCE_RATIO = Decimal("0.15")
_AMOUNT_TOLERANCE_FLOOR = Decimal("1.00")
#: Three occurrences make a pattern for anything that recurs within a month or
#: so. Quarterly and slower clear at two: two utility bills a year apart is
#: already a yearly bill, and waiting for a third would take three years.
_MIN_OCCURRENCES = 3
_MIN_OCCURRENCES_SLOW = 2
_SLOW_CADENCE_DAYS = 60
#: How far back the detector looks. Three years is what an annual series needs
#: to clear its own two-occurrence bar with room to spare.
DETECTION_WINDOW_DAYS = 3 * 365


def monthly_multiplier(cadence: str) -> Decimal:
    return Decimal(CADENCE_ANNUAL_MULTIPLIER[cadence]) / 12


def monthly_amount(amount: Decimal, cadence: str) -> Decimal:
    """What one occurrence is worth per month, so a weekly bill and an annual
    one are comparable in the same list."""
    return (amount * monthly_multiplier(cadence)).quantize(Decimal("0.01"))


def _sign(amount: Decimal) -> int:
    return 1 if amount > 0 else -1


def _match_text(merchant: str | None, description: str | None) -> str:
    """The text a series is matched by: the merchant when the household (or a
    rule, or the bank) named one, the description otherwise — which is all a
    hand-entered or CSV-imported row usually has."""
    return (merchant or description or "").strip()


def _matches(
    series: RecurringSeries, *, account_id: uuid.UUID | None, text: str, amount: Decimal,
) -> bool:
    """Whether one transaction counts as an occurrence of this series.

    Deliberately the one predicate: the occurrence counts a person sees and the
    "already tracked" check the detector makes are the same question, so they
    cannot disagree about what a series covers.
    """
    if series.account_id is not None and series.account_id != account_id:
        return False
    if series.merchant:
        needle = series.merchant.strip().lower()
        if not needle or needle not in text.lower():
            return False
    return _sign(series.amount) == _sign(amount)


# ---- the query and the two derivations that read it ----------------------------


async def _transactions(session: AsyncSession) -> list[Transaction]:
    """Every transaction a series could match: not hidden, not pending.

    Pending rows are excluded because a phantom that never posts would inflate
    an occurrence count; hidden rows because hiding one is how a person says it
    should not be read.
    """
    return list(
        (
            await session.execute(
                select(Transaction)
                .where(Transaction.is_hidden.is_(False), Transaction.is_pending.is_(False))
                .order_by(Transaction.transacted_at.desc())
            )
        )
        .scalars()
        .all()
    )


def _stats(series: RecurringSeries, rows: list[Transaction]) -> tuple[int, date | None]:
    count = 0
    last: date | None = None
    for t in rows:
        text = _match_text(t.merchant, t.description)
        if not text:
            continue
        if _matches(series, account_id=t.account_id, text=text, amount=t.amount):
            count += 1
            seen = t.transacted_at.date()
            if last is None or seen > last:
                last = seen
    return count, last


def _out(series: RecurringSeries, rows: list[Transaction]) -> RecurringOut:
    occurrences, last_seen = _stats(series, rows)
    return RecurringOut(
        id=series.id,
        name=series.name,
        merchant=series.merchant,
        account_id=series.account_id,
        category_id=series.category_id,
        amount=series.amount,
        currency=series.currency,
        cadence=series.cadence,
        next_due_date=series.next_due_date,
        is_active=series.is_active,
        transaction_id=series.transaction_id,
        monthly_amount=monthly_amount(series.amount, series.cadence),
        occurrences=occurrences,
        last_seen_date=last_seen,
        created_at=series.created_at,
        updated_at=series.updated_at,
    )


def totals(series_list: list[RecurringOut], base: str) -> list[RecurringTotalsOut]:
    """Per-currency monthly totals, the base currency first.

    Sums stay inside one currency: a total that added euros to dollars would be
    a conversion nobody asked for (ADR-0005/ADR-0035). ``monthly_out`` is
    negative, like every expense amount in the ledger.

    Paused series are left out, which is what pausing them means — and they stay
    out whatever the list is filtered to, so the figure means one thing. A row
    that is shown but not counted is marked as paused in the list it appears in,
    so the difference is visible rather than a surprise.
    """
    by_currency: dict[str, list[Decimal]] = {}
    for item in series_list:
        if item.is_active:
            by_currency.setdefault(item.currency, []).append(item.monthly_amount)

    out: list[RecurringTotalsOut] = []
    for currency in sorted(by_currency, key=lambda c: (c != base, c)):
        monthly = by_currency[currency]
        # The accumulators start at ``0.00`` rather than ``0`` so an empty side
        # still serialises as an amount ("0.00"): a money field that reads as a
        # bare integer in one response and a decimal in the next is a field a
        # client has to special-case.
        money_in = sum((m for m in monthly if m > 0), Decimal("0.00"))
        money_out = sum((m for m in monthly if m < 0), Decimal("0.00"))
        out.append(
            RecurringTotalsOut(
                currency=currency,
                monthly_in=money_in,
                monthly_out=money_out,
                net_monthly=money_in + money_out,
            )
        )
    return out


# ---- CRUD ---------------------------------------------------------------------


async def get(session: AsyncSession, series_id: uuid.UUID) -> RecurringSeries:
    series = (
        await session.execute(select(RecurringSeries).where(RecurringSeries.id == series_id))
    ).scalar_one_or_none()
    if series is None:
        raise LedgerError("Recurring series not found", 404)
    return series


async def to_out(session: AsyncSession, series: RecurringSeries) -> RecurringOut:
    """One series in the shape every route answers with — stats included, so a
    single-object read and the list can never describe the same series two
    different ways."""
    return _out(series, await _transactions(session))


async def list_series(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    direction: str | None = None,
    q: str | None = None,
) -> RecurringListOut:
    stmt = select(RecurringSeries).order_by(
        RecurringSeries.is_active.desc(), RecurringSeries.name
    )
    if account_id is not None:
        stmt = stmt.where(RecurringSeries.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(RecurringSeries.category_id == category_id)
    if is_active is not None:
        stmt = stmt.where(RecurringSeries.is_active.is_(is_active))
    if direction == "in":
        stmt = stmt.where(RecurringSeries.amount > 0)
    elif direction == "out":
        stmt = stmt.where(RecurringSeries.amount < 0)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            RecurringSeries.name.ilike(needle) | RecurringSeries.merchant.ilike(needle)
        )

    rows = list((await session.execute(stmt)).scalars().all())
    txns = await _transactions(session)
    items = [_out(series, txns) for series in rows]
    # Totals are per currency, and the household's own base currency leads the
    # list — the one a reader expects first (ADR-0052's summary does the same).
    return RecurringListOut(
        items=items, totals=totals(items, await base_currency(session, household_id))
    )


async def create(
    session: AsyncSession, household_id: uuid.UUID, data: RecurringCreate,
) -> RecurringSeries:
    """Create a series, hand-entered or seeded from a picked transaction.

    Sent fields always win; anything left absent is filled from the picked
    transaction. That is what lets the UI post a whole suggestion back (or just
    an id plus a cadence) without the server second-guessing either one.
    """
    seed: Transaction | None = None
    if data.transaction_id is not None:
        seed = (
            await session.execute(
                select(Transaction).where(Transaction.id == data.transaction_id)
            )
        ).scalar_one_or_none()
        if seed is None:
            raise LedgerError("Transaction not found", 404)

    account_id = data.account_id if is_set(data, "account_id") else (
        seed.account_id if seed is not None else None
    )
    if account_id is not None:
        await _account(session, account_id)

    if is_set(data, "category_id"):
        category_id = data.category_id
    else:
        category_id = seed.category_id if seed is not None else None
    if category_id is not None:
        await _category(session, category_id)

    if is_set(data, "name") and data.name and data.name.strip():
        name = data.name.strip()
    else:
        name = (_match_text(seed.merchant, seed.description) if seed is not None else "").strip()
    if not name:
        raise LedgerError("name is required", 422)
    if len(name) > 120:
        raise LedgerError("name must be 120 characters or fewer", 422)

    amount = data.amount if is_set(data, "amount") else (
        seed.amount if seed is not None else None
    )
    if amount is None:
        raise LedgerError("amount is required", 422)

    if is_set(data, "merchant"):
        merchant = data.merchant
    elif seed is not None:
        merchant = _match_text(seed.merchant, seed.description) or None
    else:
        merchant = None

    if data.currency:
        currency = data.currency
    elif account_id is not None:
        currency = (await _account(session, account_id)).currency
    elif seed is not None:
        currency = seed.currency
    else:
        currency = await base_currency(session, household_id)

    series = RecurringSeries(
        household_id=household_id,
        name=name,
        merchant=merchant,
        account_id=account_id,
        category_id=category_id,
        amount=amount,
        currency=currency,
        cadence=data.cadence,
        next_due_date=data.next_due_date,
        is_active=data.is_active,
        transaction_id=seed.id if seed is not None else None,
    )
    session.add(series)
    await session.flush()
    return series


async def update(
    session: AsyncSession, series_id: uuid.UUID, data: RecurringUpdate,
) -> RecurringSeries:
    series = await get(session, series_id)

    if is_set(data, "name"):
        if not data.name or not data.name.strip():
            raise LedgerError("name cannot be emptied", 422)
        series.name = data.name.strip()[:120]
    if is_set(data, "merchant"):
        series.merchant = data.merchant
    if is_set(data, "account_id"):
        if data.account_id is not None:
            account = await _account(session, data.account_id)
            series.account_id = account.id
            series.currency = account.currency
        else:
            series.account_id = None
    if is_set(data, "category_id"):
        if data.category_id is not None:
            await _category(session, data.category_id)
        series.category_id = data.category_id
    if is_set(data, "amount") and data.amount is not None:
        series.amount = data.amount
    if is_set(data, "currency") and data.currency is not None:
        series.currency = data.currency
    if is_set(data, "cadence") and data.cadence is not None:
        series.cadence = data.cadence
    if is_set(data, "next_due_date"):
        series.next_due_date = data.next_due_date
    if is_set(data, "is_active") and data.is_active is not None:
        series.is_active = data.is_active

    await session.flush()
    # ``updated_at`` is the database's (``onupdate=func.now()``), and a flush
    # that set it by SQL leaves the attribute expired — reading it then is a
    # lazy load, which in an async session is not something the response
    # serialiser can do. Fetch it back so the row the route returns is whole.
    await session.refresh(series)
    return series


async def delete(session: AsyncSession, series_id: uuid.UUID) -> None:
    """Delete the series. Its transactions are not touched — it never owned
    them (ADR-0053)."""
    series = await get(session, series_id)
    await session.delete(series)
    await session.flush()


async def _account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise LedgerError("Account not found", 404)
    return account


async def _category(session: AsyncSession, category_id: uuid.UUID) -> Category:
    category = (
        await session.execute(select(Category).where(Category.id == category_id))
    ).scalar_one_or_none()
    if category is None:
        raise LedgerError("Category not found", 404)
    return category


# ---- detection ----------------------------------------------------------------


def _median[T: (int, Decimal)](values: list[T]) -> T:
    """The upper median: for an even count, the later of the two middle values.

    Either middle value would do — this is a robust centre, not a statistic —
    and taking the upper one keeps the function total for any non-empty list
    without an average that would turn ints into the float this codebase does
    not put near money (ADR-0005).
    """
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _cadence_for(median_gap: int) -> str | None:
    """The cadence whose nominal length is nearest the observed gap, or None
    when the nearest one is still too far off to claim."""
    cadence = min(CADENCE_DAYS, key=lambda c: abs(CADENCE_DAYS[c] - median_gap))
    nominal = CADENCE_DAYS[cadence]
    if abs(median_gap - nominal) > nominal * _CADENCE_TOLERANCE:
        return None
    return cadence


def detect(
    rows: list[Transaction], series: list[RecurringSeries], *, today: date | None = None,
) -> list[RecurringSuggestionOut]:
    """Offer the regular patterns this household's own ledger already shows.

    Grouped by (account, match text, direction), then kept only when the dates,
    the gaps and the amounts all agree on one cadence. Nothing here is stored
    and nothing is decided for the person: a suggestion is a question.

    Hidden and pending rows are dropped here as well as in ``_transactions``
    (which is what the routes read through). The rule belongs to the detector's
    meaning, not to one caller's query: a pattern must not appear or vanish
    depending on how the rows reached this function.
    """
    today = today or date.today()
    window_start = today - timedelta(days=DETECTION_WINDOW_DAYS)

    groups: dict[tuple, list[Transaction]] = {}
    for t in rows:
        if t.is_hidden or t.is_pending:
            continue
        if t.transacted_at.date() < window_start:
            continue
        text = _match_text(t.merchant, t.description)
        if not text:
            continue
        groups.setdefault((t.account_id, text.lower(), _sign(t.amount)), []).append(t)

    suggestions: list[RecurringSuggestionOut] = []
    for (account_id, _key, _direction), occurrences in groups.items():
        if len(occurrences) < 2:
            continue
        occurrences.sort(key=lambda t: t.transacted_at)
        dates = [t.transacted_at.date() for t in occurrences]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        median_gap = _median(gaps)
        cadence = _cadence_for(median_gap)
        if cadence is None:
            continue
        allowed_spread = max(
            Decimal(_GAP_TOLERANCE_FLOOR_DAYS), Decimal(median_gap) * _GAP_TOLERANCE_RATIO
        )
        if max(gaps) - min(gaps) > allowed_spread:
            continue
        needed = (
            _MIN_OCCURRENCES_SLOW if CADENCE_DAYS[cadence] >= _SLOW_CADENCE_DAYS
            else _MIN_OCCURRENCES
        )
        if len(occurrences) < needed:
            continue
        amounts = [t.amount for t in occurrences]
        median_amount = _median(amounts)
        tolerance = max(_AMOUNT_TOLERANCE_FLOOR, abs(median_amount) * _AMOUNT_TOLERANCE_RATIO)
        if any(abs(a - median_amount) > tolerance for a in amounts):
            continue

        latest = occurrences[-1]
        text = _match_text(latest.merchant, latest.description)
        # Already tracked — by an accepted suggestion or by hand. The same
        # predicate that counts occurrences answers this, so a series and the
        # suggestions it suppresses can never disagree.
        if any(
            _matches(s, account_id=account_id, text=text, amount=median_amount) for s in series
        ):
            continue

        category_counts: dict[uuid.UUID, int] = {}
        for t in occurrences:
            if t.category_id is not None:
                category_counts[t.category_id] = category_counts.get(t.category_id, 0) + 1
        category_id = (
            max(category_counts, key=lambda c: category_counts[c]) if category_counts else None
        )

        suggestions.append(
            RecurringSuggestionOut(
                name=text,
                merchant=latest.merchant,
                account_id=account_id,
                category_id=category_id,
                amount=median_amount,
                currency=latest.currency,
                cadence=cadence,
                monthly_amount=monthly_amount(median_amount, cadence),
                occurrences=len(occurrences),
                first_date=dates[0],
                last_date=dates[-1],
                next_due_date=dates[-1] + timedelta(days=CADENCE_DAYS[cadence]),
                transaction_id=latest.id,
            )
        )

    suggestions.sort(key=lambda s: abs(s.monthly_amount), reverse=True)
    return suggestions


async def suggestions(
    session: AsyncSession, *, today: date | None = None,
) -> list[RecurringSuggestionOut]:
    rows = await _transactions(session)
    series = list((await session.execute(select(RecurringSeries))).scalars().all())
    return detect(rows, series, today=today)
