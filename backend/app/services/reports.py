"""Reporting (WS-R/UR backend). Everything converted to base currency at the
correct-date rate; transfers excluded from cash-flow (ARCHITECTURE §4).

Reconciliation invariant (the real test, not "%s sum to 100") — ADR-0032:

    Δ net worth = net cash flow + currency revaluation + market appreciation
                  + unexplained

**Every term but the last is computed from data, and that is the point.** The
identity used to be a tautology — revaluation *was* whatever made it work — so it
could not fail, and the label on the residual quietly became a lie as soon as
investments existed. A market move has no transaction anywhere, so it would have
been absorbed into a line named "currency revaluation" and stayed invisible for
weeks. Three independently computable terms and **exactly one** residual leaves
room for a real error to show up in the one place it can be seen. There is room
for one unnamed term and no more: two cannot be told apart.

``unexplained`` is therefore expected to be **zero**, and when it is not, the
response says why — a missing FX rate and a stale price are the two cases in
ADR-0032 §4, and both are reported rather than smoothed away.

Investments enter here twice, by ADR-0033: ``dividend``/``interest``/``fee`` are
cash flow (money really moved, and it stays in or leaves), while ``buy``/``sell``
are not — they are movements between two asset classes the household owns, which
for an investment account means they do not exist as cash flow *at all*, because
the account has no cash-ledger rows.

Owner filters mean two *different* things here, and that is deliberate (ADR-0026):
net worth is **account-scoped** ("the accounts Alex owns") so its decomposition
still reconciles, while cash-flow and spending are **row-scoped** ("entries
attributed to Alex") so one person's report never totals another's share of a
shared charge. The two views are therefore not additive; ``attribution`` on the
net-worth response says which question it answered.

Row-scoping is applied in ``_load_entries`` and nowhere else, so it is *total*:
every entry in a row-scoped report is judged on its own effective owner, with no
term carved out. The one entry with no owner of its own is an investment event,
which falls to its account's — the answer ``effective_owner_id`` already gives
when there is nothing more specific, and the only one available.
"""

from __future__ import annotations

import uuid
from bisect import bisect_right
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import Date, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import quantize_storage
from app.models import (
    Account,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    InvestmentTransaction,
    Security,
    Transaction,
    TransferGroup,
)
from app.models.investments import CASH_SECURITY_TYPE
from app.services import fx, periods
from app.services import investments as inv
from app.services.ledger import base_currency
from app.services.ownership import account_owner_map, effective_owner_id

#: ADR-0033 §2. The investment events that are cash flow, and the ones that are
#: not. A `transfer` crosses the boundary into or out of the investment world and
#: is handled exactly as a transfer leg is in `transactions` — excluded for the
#: whole household, counted when a subset of accounts is being measured.
INVESTMENT_INCOME_TYPES = ("dividend", "interest")
INVESTMENT_EXPENSE_TYPES = ("fee",)
INVESTMENT_TRADE_TYPES = ("buy", "sell")

#: A residual below this is rounding on a converted balance, not a finding. Every
#: term is quantized to storage precision, so a household with many accounts can
#: disagree by a fraction of a cent with nothing wrong anywhere. One value for both
#: questions — "is there anything to attribute?" and "is the remainder
#: attributable?" — so that a residual the report calls unexplained is a residual
#: that was chased to an account.
UNEXPLAINED_TOLERANCE = Decimal("0.05")

#: What an investment event is called in the cash-flow graph. Keyed by the
#: ``InvestmentTransaction.type`` that produced it, **not** by which side of the
#: graph it lands on: a reversed dividend is a negative dividend, and naming it a
#: fee because its amount came out on the expense side would be the label
#: contradicting the data. A reader gets the same word either way and can see the
#: sign for themselves.
INVESTMENT_LABELS = {
    "dividend": "Investment income",
    "interest": "Investment income",
    "fee": "Investment fees",
    "transfer": "Investment transfer",
}


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
    # Half-open on the upper bound: ``transacted_at`` is a timestamptz and ``end``
    # is a date, which Postgres coerces to midnight — so ``<= end`` silently drops
    # everything posted *after* 00:00 on the range's last day, which is most of a
    # day's activity. ``< end + 1 day`` includes that whole day and stays sargable
    # (an expression on the bound, not on the column).
    #
    # **A hidden account is out of every report, not just out of net worth.** The
    # account's balance is already excluded there, and a term one endpoint cannot
    # see is a term the reconciliation cannot balance: its rows would appear as cash
    # flow with no account behind them, and the difference would land in
    # `unexplained` wearing a "something is wrong" label when nothing was. One rule,
    # applied here, is what makes the identity exact rather than nearly exact.
    stmt = (
        select(Transaction)
        .where(
            Transaction.transacted_at >= start,
            Transaction.transacted_at < end + timedelta(days=1),
            Transaction.is_hidden.is_(False),
            Transaction.account_id.in_(
                select(Account.id).where(Account.is_hidden.is_(False))
            ),
        )
        .options(selectinload(Transaction.splits))
    )
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    return list((await session.execute(stmt)).scalars().all())


async def earliest_activity(session: AsyncSession) -> date | None:
    """The first day this household has anything to report about, or ``None``.

    ``start`` is optional on every report so a reader can ask for *all of it* —
    the one range a client cannot work out for itself, because only the server
    knows where the data begins. A client that guessed would either clip the
    household's first months or open on an empty year of its own invention.

    Three tables carry a date, and all three count: transactions are the obvious
    one, but a household whose first act was to type a balance has snapshots and
    no transactions, and an investment-only household has neither and a trade.
    Every other source of a dated row in this schema is derived from these.

    Hidden accounts are excluded, exactly as every report excludes them. A window
    opening on a hidden account's first row would draw a flat line before the
    account's own money appears in it, which reads as a gap in the data rather
    than as the scope rule it is.

    The cast is to ``Date`` in SQL, not in Python: Postgres reads a `date` against
    a ``timestamptz`` in the **session** timezone, so casting anywhere else would
    be a second opinion about which day a row is in, and "all time" would clip its
    own first day for anyone not sitting in UTC.
    """
    visible = select(Account.id).where(Account.is_hidden.is_(False))
    rows = (
        await session.execute(
            select(func.min(Transaction.transacted_at).cast(Date)).where(
                Transaction.is_hidden.is_(False),
                Transaction.account_id.in_(visible),
            )
        )
    ).scalar()
    snapshot = (
        await session.execute(
            select(func.min(BalanceSnapshot.balance_date)).where(
                BalanceSnapshot.account_id.in_(visible)
            )
        )
    ).scalar()
    trade = (
        await session.execute(
            select(func.min(InvestmentTransaction.trade_date)).where(
                InvestmentTransaction.account_id.in_(visible)
            )
        )
    ).scalar()
    days = [d for d in (rows, snapshot, trade) if d is not None]
    return min(days) if days else None


async def accounts_owned_by(session: AsyncSession, owner_id: uuid.UUID) -> set[uuid.UUID]:
    """The accounts an owner filter means — hidden ones included in the *exclusion*
    for the same reason `_reporting_transactions` drops their rows: `net_worth_at`
    skips them, so a scope that kept them would decompose a total they are not in.
    """
    rows = (
        await session.execute(
            select(Account.id).where(
                Account.owner_id == owner_id, Account.is_hidden.is_(False)
            )
        )
    ).all()
    return {r.id for r in rows}


def _is_derived_investment(a: Account) -> bool:
    """An account whose balance the ledger derives from prices, not one a provider
    states (ADR-0021)."""
    return a.type == "investment" and a.balance_source == "derived"


async def _net_worth_parts(
    session: AsyncSession,
    dates: list[date],
    base: str,
    *,
    account_ids: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, list[Decimal]]:
    """Per-account net worth at each of ``dates``, in base, **unquantized**.

    One pass for all accounts and all dates. The alternative — ``net_worth_at``
    once per account per date — is what made a 15-year monthly series over 24
    accounts **7,108 queries and 1.4 s**, and what made the residual's
    decomposition quadratic in the household's account count: each of those calls
    re-read the account list and asked for its own account's snapshots, so
    attributing one number cost O(accounts²) round trips.

    Every included account gets an entry, **including one that contributes
    nothing** — no snapshot on or before a date, or a balance with no rate. "This
    account is zero here" and "this account is not in the answer here" are
    different, and a caller decomposing a total has to tell them apart.

    Unquantized so each caller rounds where it rounds today: ``net_worth_points``
    over the total, ``net_worth_points_by_account`` over each account's own
    figure. See those two for why it matters.

    **Derived investment accounts are exempt from the carry-forward.** Their
    history is not a series of balances at all — it is quantities and a price
    series — so ADR-0032 §5 requires them at ``latest price on or before the
    point's date``, recomputed. Reading their stored snapshots instead would give
    a value pinned whenever someone last happened to recompute, which is the "flat
    line that means no new data" ADR-0032 §5 refuses to render as a flat market.
    """
    accounts = list((await session.execute(select(Account))).scalars().all())
    included = [
        a
        for a in accounts
        if not a.is_hidden and (account_ids is None or a.id in account_ids)
    ]
    parts: dict[uuid.UUID, list[Decimal]] = {
        a.id: [Decimal("0")] * len(dates) for a in included
    }
    if not dates:
        return parts

    # Balances: one query for every account's snapshots up to the last date asked
    # about, then a cursor per account. `balance_date` is unique per account, so
    # "latest ≤ date" is well defined without a tie-break.
    stated = [a for a in included if not _is_derived_investment(a)]
    if stated:
        converter = await fx.converter(
            session,
            base_ccy=base,
            currencies={a.currency for a in stated},
            until=max(dates),
        )
        rows = (
            await session.execute(
                select(
                    BalanceSnapshot.account_id,
                    BalanceSnapshot.balance_date,
                    BalanceSnapshot.balance,
                    BalanceSnapshot.currency,
                )
                .where(
                    BalanceSnapshot.account_id.in_([a.id for a in stated]),
                    BalanceSnapshot.balance_date <= max(dates),
                )
                .order_by(BalanceSnapshot.account_id, BalanceSnapshot.balance_date)
            )
        ).all()
        history: dict[uuid.UUID, list[tuple[date, Decimal, str]]] = {}
        for account_id, balance_date, balance, currency in rows:
            history.setdefault(account_id, []).append((balance_date, balance, currency))

        for a in stated:
            series = history.get(a.id)
            if not series:
                continue
            # Per date, not per snapshot: a date between two snapshots is the
            # earlier one carried forward, which is the whole meaning of "latest
            # snapshot ≤ date" and the reason this is a bisect over the dates
            # asked about rather than a walk over the snapshots.
            days = [d for d, _, _ in series]
            for i, on in enumerate(dates):
                j = bisect_right(days, on) - 1
                if j < 0:
                    continue
                _, balance, currency = series[j]
                conv, _rate_date = await converter.to_base(
                    amount=balance, currency=currency, on=on, base_ccy=base
                )
                if conv is None:
                    continue  # no rate: the account is not countable at this date
                parts[a.id][i] += conv if a.is_asset else -conv

    derived = [a for a in included if _is_derived_investment(a)]
    if derived:
        ids = {a.id for a in derived}
        for i, on in enumerate(dates):
            # Two queries per *date*, not per date and account: the valuation
            # already splits by account before it sums.
            quantities = await inv.quantities_at(session, account_ids=ids, on=on)
            values = await inv.securities_value_by_account(
                session,
                quantities=quantities,
                on=on,
                base_ccy=base,
                # Net worth counts the account's cash too; it is the *appreciation*
                # term that must leave cash out, because a dividend is income.
                exclude_cash=False,
            )
            for a in derived:
                value = values.get(a.id)
                if value is not None:
                    parts[a.id][i] += value if a.is_asset else -value
    return parts


async def net_worth_points(
    session: AsyncSession,
    dates: list[date],
    base: str,
    *,
    account_ids: set[uuid.UUID] | None = None,
) -> list[Decimal]:
    """Net worth at each date: latest snapshot ≤ date per account, converted at
    that date's rate, signed by ``is_asset``.

    Rounded **once over the total**, which is what makes a series and the delta
    taken from its ends agree: quantizing each account first and adding those
    would let twenty-four roundings accumulate into a cent that shows up as
    unexplained change at a point where nothing happened.
    """
    parts = await _net_worth_parts(session, dates, base, account_ids=account_ids)
    return [
        quantize_storage(sum((p[i] for p in parts.values()), Decimal("0")))
        for i in range(len(dates))
    ]


async def net_worth_points_by_account(
    session: AsyncSession,
    dates: list[date],
    base: str,
    *,
    account_ids: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, list[Decimal]]:
    """The same values, split by account and rounded **per account**.

    The form the residual's decomposition needs, because the number it prints is
    that account's own (ADR-0032 §5) — and the form it used to build by calling
    ``net_worth_at`` twice per account, which valued the whole household to answer
    a question about one of them.
    """
    parts = await _net_worth_parts(session, dates, base, account_ids=account_ids)
    return {
        account_id: [quantize_storage(value) for value in values]
        for account_id, values in parts.items()
    }


async def net_worth_at(
    session: AsyncSession, on: date, base: str, *, account_ids: set[uuid.UUID] | None = None
) -> Decimal:
    """Net worth at one date — ``net_worth_points`` with a list of one."""
    return (await net_worth_points(session, [on], base, account_ids=account_ids))[0]


async def _investment_base_amount(
    session: AsyncSession, row: InvestmentTransaction, *, base_ccy: str
) -> Decimal | None:
    """The row's amount in base, converted at its own date when the cache is empty.

    The cache is authoritative when present (ADR-0017 recomputes it on a rate
    change and never treats it as the source of truth). Nothing writes it for
    investment rows today, so in practice this converts — and ``None`` is returned
    rather than a guessed 1:1, because the identity is only exact when every row's
    rate is knowable and a guess would turn a reported gap into a silent wrong
    answer.
    """
    if row.base_amount is not None:
        return row.base_amount
    conv, _ = await fx.to_base(
        session, amount=row.amount, currency=row.currency, on=row.trade_date,
        base_ccy=base_ccy,
    )
    return conv


class Flow(NamedTuple):
    """One reporting entry, in base currency, after every exclusion.

    The unit both cash-flow reports are built from — the totals here, the
    category rows in ``cash_flow_sankey`` — so that a second report cannot
    disagree with the first about what counts.

    ``amount`` is signed: positive is money in. ``owner_id`` is the **effective**
    owner (``ownership.effective_owner_id``) and is carried rather than looked up
    at the point of use, because a split child is owned by whoever the split says
    and not by whoever owns the account.

    ``category_id`` may be ``None``, and that is an answer rather than a gap:
    nothing has classified this entry. ``source`` says *why* when it is ``None`` —
    an investment event has no category column at all, so it is unclassified for a
    reason the reader cannot fix by filing it, and the two must not be labelled
    the same way.

    ``source`` is ``"transaction"`` or ``"investment:<type>"``, carrying the
    investment type rather than a bare ``"investment"`` because the type is the
    only thing that distinguishes income from fees — and the two are separate
    nodes in the cash-flow graph, which would otherwise merge. Encoding it here
    rather than in a second nullable field keeps "which type is it" and "is it an
    investment at all" from being two answers that can disagree.
    """

    category_id: uuid.UUID | None
    account_id: uuid.UUID
    owner_id: uuid.UUID
    amount: Decimal
    source: str


async def _investment_flows(
    session: AsyncSession,
    start: date,
    end: date,
    base_ccy: str,
    *,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[list[Flow], list[str]]:
    """The investment events that are cash flow, as ``Flow``s (ADR-0033 §2).

    A ``buy`` or ``sell`` never appears: for an investment account it is not an
    excluded cash flow, it is not a cash flow at all — the account has no
    cash-ledger rows for it, and counting it here would report buying a stock as
    spending (ADR-0033 §1).

    ``transfer`` is the boundary event (ADR-0033 §3) and follows the transactions
    rule exactly: excluded for the whole household, where its other leg cancels
    it, and counted when a subset of accounts is being measured, where it really
    does move that subset.

    No ``is_hidden`` filter: an investment transaction has no such column, and
    hiding one is what deleting it is for.
    """
    stmt = select(InvestmentTransaction).where(
        InvestmentTransaction.trade_date >= start,
        InvestmentTransaction.trade_date <= end,
    )
    if account_ids is not None:
        stmt = stmt.where(InvestmentTransaction.account_id.in_(account_ids))
    rows = list((await session.execute(stmt)).scalars().all())
    owners = await account_owner_map(session)

    flows: list[Flow] = []
    warnings: list[str] = []
    for t in rows:
        if t.type in INVESTMENT_TRADE_TYPES:
            continue
        if t.type == "transfer":
            if account_ids is None:
                continue
        elif t.type not in INVESTMENT_INCOME_TYPES + INVESTMENT_EXPENSE_TYPES:
            continue  # `split` has no cash effect at all
        base_amount = await _investment_base_amount(session, t, base_ccy=base_ccy)
        if base_amount is None:
            warnings.append(
                f"no FX rate for {t.currency} on {t.trade_date}: one investment "
                f"event is missing from cash flow"
            )
            continue
        flows.append(
            Flow(
                category_id=None,
                account_id=t.account_id,
                owner_id=owners[t.account_id],
                amount=base_amount,
                source=f"investment:{t.type}",
            )
        )
    return flows, warnings


async def _load_entries(
    session: AsyncSession,
    start: date,
    end: date,
    base_ccy: str,
    *,
    owner_id: uuid.UUID | None = None,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[list[Flow], list[str]]:
    """Every cash-flow entry in ``[start, end]``, after every exclusion.

    The one place the exclusions live — transfers, hidden rows, hidden accounts,
    transfer-typed categories, the owner scope — so that a report built on these
    entries cannot have a different idea of what counts than the totals beside it.

    ``owner_id`` selects *rows* and is applied here, once, to everything: each
    entry is judged on its own effective owner, so one person's report never
    totals another's share of a shared charge. That includes investment events,
    which have no per-row owner and therefore fall to their account's — the last
    fallback of ``effective_owner_id`` and the only answer available.

    That last sentence is a correction. Investment income used to skip this
    filter, on the reasoning that it feeds a decomposition that is account-scoped.
    The reasoning was right and the conclusion was wrong: it is true of the
    *net-worth* call site, which passes ``account_ids`` and never ``owner_id``,
    and false of ``/reports/cash-flow``, which declares ``attribution: "row"`` —
    where the carve-out meant one owner's report quietly totalled the whole
    household's dividends.

    ``account_ids`` selects *accounts*, and is how the net-worth decomposition is
    narrowed. That mode counts transfer legs instead of excluding them: a transfer
    crossing the boundary of an account subset does move that subset's balance, and
    dropping it would break ``ΔNW = cash flow + revaluation + appreciation`` for
    that subset. For the whole household the legs cancel, which is why excluding
    them there is exact.
    """
    ctype = await _category_type_map(session)
    owners = await account_owner_map(session)

    flows: list[Flow] = []
    for t in await _reporting_transactions(session, start, end, account_ids):
        if account_ids is None and t.transfer_group_id is not None:
            continue
        for cat_id, bamt, entry_owner in _entries(t, owners):
            if bamt is None:
                continue
            if cat_id and ctype.get(cat_id) == "transfer":
                continue
            flows.append(
                Flow(
                    category_id=cat_id,
                    account_id=t.account_id,
                    owner_id=entry_owner,
                    amount=bamt,
                    source="transaction",
                )
            )

    investment_flows, warnings = await _investment_flows(
        session, start, end, base_ccy, account_ids=account_ids
    )
    flows.extend(investment_flows)

    if owner_id is not None:
        flows = [f for f in flows if f.owner_id == owner_id]
    return flows, warnings


def _fold(
    flows: Iterable[Flow],
) -> tuple[Decimal, Decimal, dict[uuid.UUID, Decimal]]:
    """``(income, expense, by_account)`` — the one place the sign rule lives.

    Income is the sum of the positive entries and expense the sum of the negative
    ones, so ``expense`` is *negative* and ``income + expense`` is the window's
    net. Two reports read these three numbers and they have to mean the same thing
    in both, which is the whole reason this is a function rather than a loop
    written twice.

    ``by_account`` is the same money split by the account it moved through, and is
    what lets the reconciliation name an account rather than print one number.
    """
    income = Decimal("0")
    expense = Decimal("0")
    by_account: dict[uuid.UUID, Decimal] = {}
    for f in flows:
        if f.amount > 0:
            income += f.amount
        elif f.amount < 0:
            expense += f.amount
        by_account[f.account_id] = by_account.get(f.account_id, Decimal("0")) + f.amount
    return income, expense, by_account


async def _cash_flow(
    session: AsyncSession,
    start: date,
    end: date,
    base_ccy: str,
    *,
    owner_id: uuid.UUID | None = None,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[Decimal, Decimal, Decimal, dict[uuid.UUID, Decimal], list[str]]:
    """``(income, expense, net, by_account, warnings)`` in base over [start, end].

    A thin shell over ``_load_entries`` + ``_fold``: the exclusions and the sign
    rule each have exactly one definition, and this is where they are quantized to
    storage precision for the wire.
    """
    flows, warnings = await _load_entries(
        session, start, end, base_ccy, owner_id=owner_id, account_ids=account_ids
    )
    income, expense, by_account = _fold(flows)
    return (
        quantize_storage(income),
        quantize_storage(expense),
        quantize_storage(income + expense),
        by_account,
        warnings,
    )


async def _appreciation(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None,
) -> tuple[Decimal, dict[uuid.UUID, Decimal], list[str]]:
    """``(total, by_account, warnings)`` — ADR-0032 §3, computed from the price
    series and the trades, never from what is left over.

    The formula is a decomposition of position *values*, not of prices:

        (market value at end − market value at start) − net buys/sells

    A price-delta formula (``Σ quantity × Δprice``) is wrong by an amount
    proportional to how much the household traded — it credits a mid-window buy
    with the whole window's move — which is to say it is most wrong for exactly the
    accounts this exists for. Unwinding the trades to get the *start* value is what
    makes the subtraction mean something.

    **Only ``derived`` accounts.** A ``stated`` account has no holdings to compute
    from, and inventing a plug that appreciates would be inventing a market return
    (ADR-0032 §6). Its balance change stays a stated balance change, and if it does
    not reconcile it belongs in ``unexplained`` where it can be seen.

    **Cash is excluded from both values — and from the trade sum with them.** An
    account's uninvested cash is moved by dividends, fees and contributions, all of
    which the cash-flow term already accounts for, so counting it here too would
    double-count every one of them.

    The symmetry is not cosmetic. Cash is a holding like any other (ADR-0033 §4), so
    a household moving money *inside* the account — buying VTI out of its own cash
    — does it by selling the cash position. Excluding cash from the market values
    while still counting that cash sale in ``net_buys`` makes the two legs cancel to
    nothing, and the whole purchase then reads as appreciation: the account bought
    $1,200 of stock at $1,200 and the report would call it a $1,200 gain. Leaving
    cash in the market values, or out of ``net_buys``, are both wrong; only "out of
    both" is the internal transfer it actually is.
    """
    stmt = select(Account).where(
        Account.type == "investment",
        Account.balance_source == "derived",
        Account.is_hidden.is_(False),
    )
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    ids = {a.id for a in (await session.execute(stmt)).scalars().all()}
    if not ids:
        return Decimal("0"), {}, []

    start_quantities = await inv.quantities_at(session, account_ids=ids, on=start)
    end_quantities = await inv.quantities_at(session, account_ids=ids, on=end)
    value_start = await inv.securities_value_base(
        session, quantities=start_quantities, on=start, base_ccy=base_ccy
    )
    value_end = await inv.securities_value_base(
        session, quantities=end_quantities, on=end, base_ccy=base_ccy
    )

    # A trade with no security is not a trade in anything the values above can see
    # (`positions_for` ignores it for the same reason), so it stays out of the sum
    # alongside the cash trades: `net_buys` must move exactly the value that the
    # market-value difference moves, or the two stop being comparable.
    cash_security_ids = select(Security.id).where(
        Security.security_type == CASH_SECURITY_TYPE
    )
    trades = (
        await session.execute(
            select(InvestmentTransaction).where(
                InvestmentTransaction.account_id.in_(ids),
                InvestmentTransaction.type.in_(INVESTMENT_TRADE_TYPES),
                InvestmentTransaction.trade_date >= start,
                InvestmentTransaction.trade_date <= end,
                or_(
                    InvestmentTransaction.security_id.is_(None),
                    InvestmentTransaction.security_id.not_in(cash_security_ids),
                ),
            )
        )
    ).scalars().all()
    net_buys = Decimal("0")
    net_buys_by_account: dict[uuid.UUID, Decimal] = {}
    warnings: list[str] = []
    for t in trades:
        amount = await _investment_base_amount(session, t, base_ccy=base_ccy)
        if amount is None:
            warnings.append(
                f"no FX rate for {t.currency} on {t.trade_date}: a trade is missing "
                f"from appreciation"
            )
            continue
        # `amount` is the cash effect, so money *into* securities is its negation:
        # a buy (negative) increases what was invested, a sell decreases it.
        net_buys += -amount
        net_buys_by_account[t.account_id] = (
            net_buys_by_account.get(t.account_id, Decimal("0")) - amount
        )

    # Per account, off the *same* two maps the total is computed from: a value is a
    # sum of independent positions, so splitting one by its account key splits the
    # number exactly — the attribution cannot disagree with the term it explains.
    by_account: dict[uuid.UUID, Decimal] = {}
    for account_id in ids:
        in_account = {key: qty for key, qty in start_quantities.items() if key[0] == account_id}
        value_then = await inv.securities_value_base(
            session, quantities=in_account, on=start, base_ccy=base_ccy
        )
        in_account = {key: qty for key, qty in end_quantities.items() if key[0] == account_id}
        value_now = await inv.securities_value_base(
            session, quantities=in_account, on=end, base_ccy=base_ccy
        )
        by_account[account_id] = (
            value_now - value_then - net_buys_by_account.get(account_id, Decimal("0"))
        )

    appreciation = quantize_storage(value_end - value_start - net_buys)
    return appreciation, by_account, warnings


class Revaluation(NamedTuple):
    """The revaluation term, its per-account parts, and its conversion cost.

    ``by_account`` exists for the residual attribution (``_unexplained_by_account``)
    — it is the same arithmetic, split so the reconciliation can name an account
    instead of printing one number. ``conversion`` is broken out for the same
    reason: it is a pair-level cost that belongs to no single account, so the
    attribution has to be able to say that rather than blame one.
    """

    total: Decimal
    by_account: dict[uuid.UUID, Decimal]
    conversion: Decimal
    warnings: list[str]


async def _conversion_cost(
    session: AsyncSession, *, start: date, end: date, account_ids: set[uuid.UUID] | None
) -> Decimal:
    """The FX spread cross-currency transfer legs actually paid — ADR-0018.

    ``fx_cost_base`` is Σ ``base_amount`` of a group's legs, so it is non-zero
    exactly when the conversion was not at the market rate the two legs were each
    booked at. Linking removes both legs from cash flow, and that exclusion is
    exact only when the legs cancel: the moment they do not, the household's net
    worth moved by the difference and nothing on the report says so. This is the
    term that says so.

    ``ARCHITECTURE.md`` calls for it in as many words — "the real FX spread/fee —
    stored as ``fx_cost_base`` and surfaced (as a fee/revaluation), **never
    hidden** by the transfer exclusion".

    It is a *flow* at a rate nobody recorded, which is why it belongs in this term
    and not in cash flow: the legs are excluded there precisely because money
    moving between two accounts the household owns is not income or spending.

    Household scope only. When a subset of accounts is measured, ``_cash_flow``
    counts the legs instead of excluding them, so their residual — this same
    number — is already inside its total, and adding it here would double-count it.
    """
    if account_ids is not None:
        return Decimal("0")
    rows = (
        await session.execute(
            select(
                TransferGroup.id,
                TransferGroup.fx_cost_base,
                Account.is_hidden,
                Transaction.is_hidden,
            )
            .join(Transaction, Transaction.transfer_group_id == TransferGroup.id)
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.transacted_at >= start,
                Transaction.transacted_at < end + timedelta(days=1),
            )
        )
    ).all()
    # A group counts only if *every* leg is visible: half a transfer is not a
    # transfer, and both reports that would have shown the other half are not
    # showing it either.
    hidden = {gid for gid, _fx, acct_hidden, txn_hidden in rows if acct_hidden or txn_hidden}
    costs = {gid: fx for gid, fx, _ah, _th in rows if gid not in hidden}
    return sum((fx for fx in costs.values() if fx is not None), Decimal("0"))


async def _revaluation(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None,
) -> Revaluation:
    """The base-value change of foreign balances from rate moves — ADR-0032 §4.

    Also computed, for the same reason ``appreciation`` is: it used to be the
    residual, so the identity could not fail and the label could quietly become a
    lie. Holding €1,000 at 1.10 and ending at 1.20 is a real +$100 that no
    transaction recorded.

    Two parts, per account, both from data — the opening balance and the rate move,
    then each flow at the difference between the closing rate and its own:

        sign × (B_start × (R_end − R_start) + R_end × Σ(amounts)) − Σ(base amounts)

    where ``sign`` is ``+1`` for an asset and ``−1`` for a liability, because net
    worth counts a card's balance against you — an FX move *increases* what a euro
    balance of debt costs, and that only comes out right if the sign follows the
    account into the rate term.

    That expression is algebraically the account's change in base value minus the
    cash flow it reports. Computing it *that* way would be circular; computing it
    from balances and rates is a second, independent number, and two numbers that
    must agree is what turns the identity from a definition into a test.

    Investment accounts are **out of scope**, by ADR-0017 §5's standing deferral of
    position-level FX: the rate move on a foreign security's *value* is inside
    ``appreciation``, which is computed in base and therefore already reflects it.

    The term also carries ``_conversion_cost`` — the spread a cross-currency
    transfer paid. It is a rate effect on a flow rather than on a balance, so it
    belongs to this term and not to cash flow, whose whole treatment of a linked
    transfer assumes the legs cancel.
    """
    stmt = select(Account).where(Account.is_hidden.is_(False))
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    accounts = list((await session.execute(stmt)).scalars().all())

    total = Decimal("0")
    by_account: dict[uuid.UUID, Decimal] = {}
    warnings: list[str] = []
    for a in accounts:
        # A derived investment account's balance is Σ(holdings), so its "opening
        # balance in its own currency" is not one number and its rate move is
        # already inside `appreciation`.
        if a.currency == base_ccy or _is_derived_investment(a):
            continue
        rates = await fx.get_multiplier(
            session, from_ccy=a.currency, to_ccy=base_ccy, on=start, base_ccy=base_ccy
        )
        rate_end = await fx.get_multiplier(
            session, from_ccy=a.currency, to_ccy=base_ccy, on=end, base_ccy=base_ccy
        )
        if rates is None or rate_end is None:
            warnings.append(
                f"no FX rate for {a.currency}: {a.name}'s currency move is not "
                f"attributable over this period"
            )
            continue

        opening = (
            await session.execute(
                select(BalanceSnapshot.balance)
                .where(
                    BalanceSnapshot.account_id == a.id,
                    BalanceSnapshot.balance_date <= start,
                )
                .order_by(BalanceSnapshot.balance_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        # No snapshot at or before `start` means `net_worth_at` counted nothing for
        # this account then, so the opening balance it decomposed is genuinely zero
        # — not unknown. The two have to agree or the identity is off by exactly the
        # account.
        opening = Decimal("0") if opening is None else opening

        rows = (
            await session.execute(
                select(Transaction).where(
                    Transaction.account_id == a.id,
                    Transaction.transacted_at >= start,
                    Transaction.transacted_at < end + timedelta(days=1),
                )
            )
        ).scalars().all()
        moved = Decimal("0")
        moved_base = Decimal("0")
        for t in rows:
            if t.base_amount is None:
                warnings.append(
                    f"no rate for a {t.currency} transaction on "
                    f"{t.transacted_at.date()}: it is not attributable"
                )
                continue
            moved += t.amount
            moved_base += t.base_amount

        sign = 1 if a.is_asset else -1
        part = sign * (opening * (rate_end - rates) + rate_end * moved) - moved_base
        total += part
        by_account[a.id] = part

    conversion = await _conversion_cost(
        session, start=start, end=end, account_ids=account_ids
    )
    return Revaluation(
        total=quantize_storage(total + conversion),
        by_account=by_account,
        conversion=conversion,
        warnings=warnings,
    )


async def _unexplained_by_account(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None,
    residual: Decimal,
    cash_flow: dict[uuid.UUID, Decimal],
    revaluation: Revaluation,
    appreciation: dict[uuid.UUID, Decimal],
) -> tuple[list[dict], Decimal]:
    """Which accounts the residual came from — ``(rows, covered)``.

    The reconciliation's residual is one number, and a number with no name is not a
    finding. Every term is linear in the set of accounts, so the same arithmetic
    run per account gives parts that sum back to the household's residual — which is
    *not* a second opinion, it is the same opinion, arranged so the reader can see
    which account to look at.

    **Both kinds of part are real, and neither is a bug on its own.** An account
    observed only periodically — a seeded balance, a bank feed that reports a
    balance and an overlapping window of transactions — moves between two
    observations by whatever happened in between, and the flows we hold explain only
    the part inside the window. The first sync of a real bank is the same shape: the
    balance is the bank's number from today and the history starts where the pull
    window starts, so the difference is the account's own past. Naming the accounts
    turns "unexplained $38,850.60" into something a person can check.

    ``covered`` is the sum over *every* account, not just the material ones
    returned, so the caller can state exactly what is left over instead of implying
    the list is the whole story.
    """
    stmt = select(Account).where(Account.is_hidden.is_(False))
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    accounts = list((await session.execute(stmt)).scalars().all())

    # Both dates for every account, in one pass. Asking per account — which is
    # what this did — read the account list once and each account's balances once
    # *per call*, so attributing one number cost O(accounts²) queries.
    values = await net_worth_points_by_account(
        session, [start, end], base_ccy, account_ids=account_ids
    )

    parts: dict[uuid.UUID, Decimal] = {}
    for a in accounts:
        # Missing means the valuation counted nothing for it — no snapshot on or
        # before the date, or a balance with no rate. Zero is that answer, not a
        # guess standing in for one.
        value_start, value_end = values.get(a.id, (Decimal("0"), Decimal("0")))
        parts[a.id] = (
            (value_end - value_start)
            - cash_flow.get(a.id, Decimal("0"))
            - revaluation.by_account.get(a.id, Decimal("0"))
            - appreciation.get(a.id, Decimal("0"))
        )
    covered = sum(parts.values(), Decimal("0"))

    # Materiality is relative to the residual, not absolute: a household with a big
    # unexplained number deserves the accounts behind it, and one whose residual is
    # a rounding cent does not deserve a list of every account it ever had.
    material = max(Decimal("1"), abs(residual) * Decimal("0.01"))
    named = {a.id: a for a in accounts}
    rows = [
        {
            "account_id": str(account_id),
            "name": named[account_id].name,
            "amount": quantize_storage(part),
        }
        for account_id, part in sorted(parts.items(), key=lambda kv: abs(kv[1]), reverse=True)
        if abs(part) >= material
    ]
    return rows, covered


async def net_worth_series(session: AsyncSession, household_id: uuid.UUID,
                           start: date, end: date,
                           owner_id: uuid.UUID | None = None,
                           granularity: periods.GranularityIn = "auto"):
    """Net worth over time, plus the reconciliation of its change.

    An owner filter narrows the *accounts* and everything derived from them — the
    series, the delta, and the cash-flow term alike — so the identity still holds.
    A row-scoped cash flow here would leave the identity asserting something false.

    ``granularity`` sets the **points only**. Every term of the identity and every
    per-account decomposition stays scoped to the whole window, because the identity
    is a statement about the window and nothing else: bucketing the terms would make
    a report whose totals move when the reader switches the chart from months to
    quarters, which is the opposite of a reconciliation.
    """
    base = await base_currency(session, household_id)
    resolved = periods.resolve(granularity, start, end)
    account_ids = None if owner_id is None else await accounts_owned_by(session, owner_id)

    # The level at the window's own start, then at the end of each bucket in it.
    #
    # `_month_ends` gave a series that began at the first month-*end* — so a window
    # opening on 15 January had nothing to say about the 15th — and ran to the
    # last month-end, which for a window closing on 20 September asked
    # `net_worth_at(2026-09-30)`. That reads snapshots dated after the window, so
    # the chart's final point was a level from ten days the reader had not
    # requested. `points[0].date == start` is the property both faults violate.
    dates = [start] + [
        bucket.end
        for bucket in periods.buckets(start, end, resolved)
        if bucket.end != start
        # A one-day window: the baseline already *is* this bucket's end.
    ]
    # One pass for every point, rather than a `net_worth_at` per point: the dates
    # are known before the first value is computed, so nothing has to be asked
    # twice and the whole series is one account query.
    points = [
        {"date": on, "net_worth": value}
        # `strict`: a value for every date is the function's contract, and a
        # reconciliation that silently paired a date with the wrong level would be
        # a chart that lies rather than one that fails.
        for on, value in zip(
            dates,
            await net_worth_points(session, dates, base, account_ids=account_ids),
            strict=True,
        )
    ]

    # The last point *is* the window end: the buckets partition the window, so the
    # final bucket's end is `end` — and in the one-day case the baseline is. No
    # second computation, and no chance of the chart's last point and the reported
    # delta disagreeing about what the window ended at.
    nw_start = points[0]["net_worth"]
    nw_end = points[-1]["net_worth"]
    delta = quantize_storage(nw_end - nw_start)
    _income, _expense, net_cf, cash_flow_by_account, warnings = await _cash_flow(
        session, start, end, base, account_ids=account_ids
    )
    reval = await _revaluation(
        session, start=start, end=end, base_ccy=base, account_ids=account_ids
    )
    revaluation = reval.total
    appreciation, appreciation_by_account, price_warnings = await _appreciation(
        session, start=start, end=end, base_ccy=base, account_ids=account_ids
    )
    warnings += reval.warnings + price_warnings

    # The one term that is computed by subtraction — and therefore the only one
    # that can be wrong. Everything else is read off balances, transactions, rates
    # and prices, so this residual is a real check rather than a restatement: if it
    # is not zero, something in the four terms above does not account for the
    # household's money and the UI has to be able to say so (ADR-0032 §5).
    unexplained = quantize_storage(delta - net_cf - revaluation - appreciation)

    # Attribution costs a query per account, so it is paid only when there is
    # something to attribute. A residual of a cent is rounding, and a list of every
    # account in the household to explain it would be noise wearing a finding's
    # clothes.
    unexplained_by_account: list[dict] = []
    if abs(unexplained) > UNEXPLAINED_TOLERANCE:
        unexplained_by_account, covered = await _unexplained_by_account(
            session,
            start=start,
            end=end,
            base_ccy=base,
            account_ids=account_ids,
            residual=unexplained,
            cash_flow=cash_flow_by_account,
            revaluation=reval,
            appreciation=appreciation_by_account,
        )
        # Everything the accounts do not have a name for. The conversion cost is
        # the usual occupant: it is real, it is already inside `currency_revaluation`
        # where ARCHITECTURE.md puts it, and it is the one part of the residual that
        # genuinely belongs to a *pair* of accounts rather than to one.
        leftover = unexplained - covered
        if abs(leftover) > UNEXPLAINED_TOLERANCE:
            conversion = abs(reval.conversion)
            rest = leftover + reval.conversion
            bits = []
            if conversion:
                bits.append(
                    f"{quantize_storage(conversion)} {base} is conversion cost on "
                    f"cross-currency transfers"
                )
            if abs(rest) > UNEXPLAINED_TOLERANCE:
                bits.append(
                    f"{quantize_storage(abs(rest))} {base} is not attributable to any "
                    f"account or transfer"
                )
            warnings.append("Of the unexplained change, " + " and ".join(bits) + ".")
    return {
        "base_currency": base,
        "granularity": resolved,
        "points": points,
        "delta_net_worth": delta,
        "net_cash_flow": net_cf,
        "currency_revaluation": revaluation,
        "market_appreciation": appreciation,
        "unexplained": unexplained,
        "unexplained_by_account": unexplained_by_account,
        "warnings": warnings,
        # Net worth decomposes by account, never by row: an owner filter here means
        # "the accounts Alex owns". Cash-flow and spending answer the other question.
        "attribution": "account",
    }


async def cash_flow_series(session: AsyncSession, household_id: uuid.UUID,
                           start: date, end: date,
                           owner_id: uuid.UUID | None = None,
                           granularity: periods.GranularityIn = "auto"):
    """Income, expense and net per bucket over ``[start, end]``.

    Each bucket is summed over **its own clipped span**, so the bars add up to the
    window and to nothing else. The version this replaced derived a month-end list
    from the window and then summed each month from its 1st to its own last day —
    which quietly included the days of the first and last months that lay outside
    the requested range, and reported them as if the reader had asked for them.

    Returns ``(base_currency, granularity, points)``. The granularity is returned
    rather than assumed because ``auto`` is resolved here: a chart that guessed
    would label twelve monthly bars as quarters if it resolved the span itself and
    disagreed with us, and the response is the only place the answer can be stated
    once for both readers.

    Each point is dated by its bucket's **first day within the window**, so every
    point's date is inside ``[start, end]`` by construction. The first bucket of a
    window that opens mid-period is therefore labelled by where it begins rather
    than by the period it belongs to, which is the only one of the two that is
    always a real day of the report.
    """
    base = await base_currency(session, household_id)
    resolved = periods.resolve(granularity, start, end)
    out = []
    for bucket in periods.buckets(start, end, resolved):
        income, expense, net, _by_account, _ = await _cash_flow(
            session, bucket.start, bucket.end, base, owner_id=owner_id
        )
        out.append(
            {
                "date": bucket.start,
                "income": income,
                "expense": expense,
                "net": net,
            }
        )
    return base, resolved, out


def _flow_node(f: Flow, names: dict[uuid.UUID, str]) -> tuple[str, str]:
    """The graph node a flow belongs to — ``(key, label)``.

    ``key`` is an *identity*, and the reason this returns two things instead of
    one. A label is not unique: a household may name a category "Uncategorized",
    and nothing stops two categories from sharing a name — so grouping by the
    string a reader sees would merge two unrelated rows into one node and silently
    move money between them. The key is what a chart builds its node ids from, and
    the sentinel rows get a key of their own so they cannot collide with a real
    category either.
    """
    if f.source != "transaction":
        # `source` is "investment:<type>"; the label follows the *type* and never
        # the sign, so a negative dividend still reads as investment income.
        return f.source, INVESTMENT_LABELS.get(f.source.partition(":")[2], "Investment")
    if f.category_id is None:
        return "uncategorized", "Uncategorized"
    return f"cat:{f.category_id}", names.get(f.category_id, "Uncategorized")


async def cash_flow_sankey(
    session: AsyncSession,
    household_id: uuid.UUID,
    start: date,
    end: date,
    owner_id: uuid.UUID | None = None,
):
    """The window's cash flow as two sets of named buckets, ready to be drawn.

    Built on ``_load_entries``, so it is the *same money* ``/reports/cash-flow``
    totals over the same window under the same owner filter — a claim a test can
    falsify by comparing the two, which is the only thing that makes a second
    report of one quantity worth having rather than a second place to be wrong.

    **Rows, not nodes and links.** Grouping is the whole of the arithmetic here;
    the graph's shape follows from it mechanically. Emitting the shape would put a
    layout decision on the wire where nothing downstream could tell a bad layout
    from bad data. The server knows which entries belong together, says that, and
    stops.

    **Two lists, each of magnitudes.** The side a row is in carries the direction,
    so every figure here is positive and ``total_income``/``total_expense`` are
    their own lists' sums. This is a departure from ``_fold``, where ``expense`` is
    negative, and it is forced: a Sankey encodes direction by *where a node sits*,
    and a value that is negative on both sides is not a picture anyone can draw.
    ``net`` is the one signed number, and it is the one the middle of the graph is
    drawn at.

    **A category can appear on both sides and is never netted.** A month with a
    purchase and a refund in one category is two flows moving opposite ways;
    collapsing them to their difference would draw a graph that balances while
    telling the reader nothing happened. Each side sums its own entries, so a
    refund reduces the income side rather than hiding inside an expense total.

    ``warnings`` carries more weight here than anywhere else it appears: a flow
    dropped for want of an FX rate is a missing branch of a picture whose entire
    claim is that it is the whole picture, and the response has to be able to say
    so.
    """
    base = await base_currency(session, household_id)
    flows, warnings = await _load_entries(session, start, end, base, owner_id=owner_id)
    names = dict((await session.execute(select(Category.id, Category.name))).all())

    income_rows: dict[str, dict] = {}
    expense_rows: dict[str, dict] = {}
    for f in flows:
        if f.amount == 0:
            # Neither income nor expense, which is how `_fold` reads it too — so
            # the two reports agree about a zero instead of one of them drawing the
            # single node in the graph with nothing behind it.
            continue
        key, label = _flow_node(f, names)
        bucket = income_rows if f.amount > 0 else expense_rows
        row = bucket.get(key)
        if row is None:
            row = {
                "key": key,
                "label": label,
                "category_id": f.category_id,
                "total": Decimal("0"),
            }
            bucket[key] = row
        # Quantized per entry rather than on the total, so the totals below are
        # sums of numbers already at storage precision: "the figure beside the
        # rows" and "the rows added up" are then the same number by construction,
        # with no second rounding that could disagree by a cent.
        row["total"] += quantize_storage(abs(f.amount))

    def ordered(rows: dict[str, dict]) -> list[dict]:
        return sorted(rows.values(), key=lambda r: r["total"], reverse=True)

    income = ordered(income_rows)
    expense = ordered(expense_rows)
    total_income = sum((r["total"] for r in income), Decimal("0"))
    total_expense = sum((r["total"] for r in expense), Decimal("0"))
    return {
        "base_currency": base,
        "start": start,
        "end": end,
        "income": income,
        "expense": expense,
        "total_income": total_income,
        "total_expense": total_expense,
        "net": total_income - total_expense,
        "warnings": warnings,
        # Entries, not accounts — the same question `/reports/cash-flow` answers
        # with the same filter, which is what lets a reader put the two side by
        # side (ADR-0026).
        "attribution": "row",
    }


async def spending_by_category(session: AsyncSession, household_id: uuid.UUID,
                               start: date, end: date,
                               owner_id: uuid.UUID | None = None):
    """The window's money out, bucketed — the expense half of the graph, as rows.

    Built on ``_load_entries``, the same call ``/reports/cash-flow`` makes, so all
    three row-scoped reports count one set of entries: ``total`` here is
    ``-expense`` there and ``total_expense`` on the graph, and a test can hold all
    three to the same figure. That is not tidiness. Three reports of one quantity
    that disagree are three answers to "what did we spend", and the reader has no
    way to tell which of them is right.

    **Which is a change, and the visible one is investment fees.** This used to
    walk the ledger alone, so an advisory fee was money the household paid that no
    spending report counted — while the income-vs-expense bars and the graph beside
    them both did. A fee has no category, so its row carries ``category_id: None``
    and is named for what it was (``INVESTMENT_LABELS``), exactly as the graph
    names the same node — and it is why every row carries a ``key``. Two rows can
    share a null category and must not share an identity, or a reader comparing
    "Investment fees" against "Uncategorized" is comparing one figure with itself.

    Quantized per entry and then summed, rather than summed and then quantized:
    that is what makes the total the sum of the rows above it exactly, with no
    second rounding that could differ by a cent (ADR-0005).
    """
    base = await base_currency(session, household_id)
    flows, warnings = await _load_entries(session, start, end, base, owner_id=owner_id)
    names = dict((await session.execute(select(Category.id, Category.name))).all())

    totals: dict[str, dict] = {}
    for f in flows:
        if f.amount >= 0:
            # Income is not spending, and neither is a zero — the same reading
            # `_fold` gives it, so the two reports agree about nothing happening
            # instead of one of them growing a row with nothing behind it.
            continue
        key, label = _flow_node(f, names)
        row = totals.get(key)
        if row is None:
            row = {
                "key": key,
                "category_id": f.category_id,
                "category_name": label,
                "total": Decimal("0"),
            }
            totals[key] = row
        row["total"] += quantize_storage(-f.amount)

    rows = sorted(totals.values(), key=lambda r: r["total"], reverse=True)
    total = sum((r["total"] for r in rows), Decimal("0"))
    return base, rows, total, warnings
