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
attributed to Alex", judged per split child) so one person's report never totals
another's share of a shared charge. The two views are therefore not additive;
``attribution`` on the net-worth response says which question it answered.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
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
)
from app.models.investments import CASH_SECURITY_TYPE
from app.services import fx
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


async def net_worth_at(
    session: AsyncSession, on: date, base: str, *, account_ids: set[uuid.UUID] | None = None
) -> Decimal:
    """Net worth at a date: latest snapshot ≤ date per account, converted at that
    date's rate, signed by is_asset.

    **Derived investment accounts are exempt from the carry-forward.** Their
    history is not a series of balances at all — it is quantities and a price
    series — so ADR-0032 §5 requires them at ``latest price on or before the
    point's date``, recomputed. Reading their stored snapshots instead would give
    a value pinned whenever someone last happened to recompute, which is the "flat
    line that means no new data" ADR-0032 §5 refuses to render as a flat market.
    """
    accounts = list((await session.execute(select(Account))).scalars().all())
    total = Decimal("0")
    derived = [
        a
        for a in accounts
        if not a.is_hidden
        and _is_derived_investment(a)
        and (account_ids is None or a.id in account_ids)
    ]
    if derived:
        quantities = await inv.quantities_at(
            session, account_ids={a.id for a in derived}, on=on
        )
        total += await inv.securities_value_base(
            session,
            quantities=quantities,
            on=on,
            base_ccy=base,
            # Net worth counts the account's cash too; it is the *appreciation*
            # term that must leave cash out, because a dividend is income.
            exclude_cash=False,
        )

    for a in accounts:
        if a.is_hidden or _is_derived_investment(a):
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


async def _investment_cash_flow(
    session: AsyncSession,
    start: date,
    end: date,
    base_ccy: str,
    *,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[Decimal, Decimal, list[str]]:
    """``(income, expense, warnings)`` from investment events (ADR-0033 §2).

    A ``buy`` or ``sell`` never appears: for an investment account it is not an
    excluded cash flow, it is not a cash flow at all — the account has no
    cash-ledger rows for it, and counting it here would report buying a stock as
    spending (ADR-0033 §1).

    ``transfer`` is the boundary event (ADR-0033 §3) and follows the transactions
    rule exactly: excluded for the whole household, where its other leg cancels
    it, and counted when a subset of accounts is being measured, where it really
    does move that subset.
    """
    # No `is_hidden` filter: an investment transaction has no such column, and
    # hiding one is what deleting it is for.
    stmt = select(InvestmentTransaction).where(
        InvestmentTransaction.trade_date >= start,
        InvestmentTransaction.trade_date <= end,
    )
    if account_ids is not None:
        stmt = stmt.where(InvestmentTransaction.account_id.in_(account_ids))
    rows = list((await session.execute(stmt)).scalars().all())

    income = Decimal("0")
    expense = Decimal("0")
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
        if base_amount > 0:
            income += base_amount
        else:
            expense += base_amount
    return quantize_storage(income), quantize_storage(expense), warnings


async def _cash_flow(
    session: AsyncSession,
    start: date,
    end: date,
    base_ccy: str,
    *,
    owner_id: uuid.UUID | None = None,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[Decimal, Decimal, Decimal, list[str]]:
    """``(income, expense, net, warnings)`` in base over [start, end].

    ``owner_id`` selects *rows*: each entry is judged on its own attribution, so one
    person's report never totals another's share of a shared charge.

    ``account_ids`` selects *accounts*, and is how the net-worth decomposition is
    narrowed. That mode counts transfer legs instead of excluding them: a transfer
    crossing the boundary of an account subset does move that subset's balance, and
    dropping it would break ``ΔNW = cash flow + revaluation + appreciation`` for
    that subset. For the whole household the legs cancel, which is why excluding
    them there is exact.

    **``owner_id`` does not filter investment income, and that is deliberate.**
    Attribution of a dividend would have to be the account's owner (there is no
    per-row owner on an investment event), and this term feeds a decomposition that
    is account-scoped — so row-scoping half of it would make the identity assert
    something false. The net-worth response already says ``attribution: account``.
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

    inv_income, inv_expense, warnings = await _investment_cash_flow(
        session, start, end, base_ccy, account_ids=account_ids
    )
    income += inv_income
    expense += inv_expense
    net = income + expense
    return (
        quantize_storage(income),
        quantize_storage(expense),
        quantize_storage(net),
        warnings,
    )


async def _appreciation(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None,
) -> tuple[Decimal, list[str]]:
    """Market appreciation over [start, end] — ADR-0032 §3, computed from the price
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
        return Decimal("0"), []

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

    appreciation = quantize_storage(value_end - value_start - net_buys)
    return appreciation, warnings


async def _revaluation(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None,
) -> tuple[Decimal, list[str]]:
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
    """
    stmt = select(Account).where(Account.is_hidden.is_(False))
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    accounts = list((await session.execute(stmt)).scalars().all())

    total = Decimal("0")
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
        total += sign * (opening * (rate_end - rates) + rate_end * moved) - moved_base

    return quantize_storage(total), warnings


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
    _income, _expense, net_cf, warnings = await _cash_flow(
        session, start, end, base, account_ids=account_ids
    )
    revaluation, fx_warnings = await _revaluation(
        session, start=start, end=end, base_ccy=base, account_ids=account_ids
    )
    appreciation, price_warnings = await _appreciation(
        session, start=start, end=end, base_ccy=base, account_ids=account_ids
    )
    warnings += fx_warnings + price_warnings

    # The one term that is computed by subtraction — and therefore the only one
    # that can be wrong. Everything else is read off balances, transactions, rates
    # and prices, so this residual is a real check rather than a restatement: if it
    # is not zero, something in the four terms above does not account for the
    # household's money and the UI has to be able to say so (ADR-0032 §5).
    unexplained = quantize_storage(delta - net_cf - revaluation - appreciation)
    return {
        "base_currency": base,
        "points": points,
        "delta_net_worth": delta,
        "net_cash_flow": net_cf,
        "currency_revaluation": revaluation,
        "market_appreciation": appreciation,
        "unexplained": unexplained,
        "warnings": warnings,
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
        income, expense, net, _ = await _cash_flow(
            session, m_start, d, base, owner_id=owner_id
        )
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
