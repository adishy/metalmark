"""Investments: valuation, allocation, and cost basis (ADR-0011/0020/0021, and
the reconciliation terms of ADR-0032).

Everything here is **as of a date**. That is the central difference from the rest
of the ledger: a cash account's historical balance comes from a carried-forward
``balance_snapshots`` row, but a holding's value is
``quantity × the latest price on or before the date``, recomputed. Two reasons it
has to work that way rather than by snapshotting: ADR-0032 §3 computes market
appreciation *from the price series*, which a carried-forward scalar cannot
reconstruct; and a snapshot taken at the moment a position was entered would pin
the value at entry forever, which is exactly the "flat line that means no new
data" ADR-0032 §5 refuses to render as a flat market.

**Unpriced is not zero.** A position with no price on or before the date
contributes nothing to the total but is returned in ``unpriced``; so is a position
whose price exists but has no FX rate to base. Both would otherwise understate net
worth silently, and "we cannot value this" and "this is worth nothing" must not
render identically. The caller surfaces the lists; this module never drops them.

Nothing here writes. ``recompute_derived_balance`` is the one exception and it is
explicit, because writing a balance is a decision about *when*, and ADR-0021 makes
that decision per account (``derived`` vs ``stated``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import convert, quantize_storage
from app.models import Account, Holding, InvestmentTransaction, Security, SecurityPrice
from app.models.investments import (
    CASH_SECURITY_TYPE,
    INVESTMENT_TX_TYPES,
    SECURITY_TYPES,
)
from app.schemas.patch import is_set
from app.services import fx
from app.services.errors import LedgerError
from app.services.ledger import base_currency, upsert_balance_snapshot

ZERO = Decimal("0")

#: Why a position could not be valued. Both are reported, never silently zeroed.
NO_PRICE = "no_price"
NO_RATE = "no_rate"


@dataclass(frozen=True)
class PricePoint:
    price: Decimal
    currency: str
    price_date: date


@dataclass
class HoldingValue:
    """One position, valued. ``value_base is None`` iff ``reason`` is set."""

    #: The ``holdings`` row, when there is one. **Null for a position that exists
    #: only as recorded trades** — see ``positions_for`` — which is a real position
    #: with a real value and simply has no row to point at.
    holding_id: uuid.UUID | None
    account_id: uuid.UUID
    security_id: uuid.UUID
    name: str
    ticker: str | None
    security_type: str
    quantity: Decimal
    price: Decimal | None
    price_date: date | None
    price_currency: str | None
    value_native: Decimal | None
    value_account: Decimal | None
    value_base: Decimal | None
    #: Days between the valuation date and the price used — ADR-0032 §5. ``None``
    #: when there is no price, which is a stronger statement than "very stale".
    stale_days: int | None
    reason: str | None = None

    @property
    def is_priced(self) -> bool:
        return self.value_base is not None


@dataclass
class AccountValuation:
    account_id: uuid.UUID
    name: str
    currency: str
    balance_source: str | None
    on: date
    base_currency: str
    holdings: list[HoldingValue] = field(default_factory=list)
    #: Σ of the holdings that could be valued, in base.
    market_value_base: Decimal = ZERO
    #: Σ of the *same* holdings in the account's own currency. This is what
    #: ``accounts.current_balance`` holds for a derived account.
    market_value_account: Decimal = ZERO
    #: ADR-0021: the gap for a ``stated`` account, in base. Computed, never a row.
    unaccounted_cash_base: Decimal | None = None
    #: The account's own balance, in its own currency, for a ``stated`` account —
    #: which is the authoritative number there, not Σ(holdings). ``None`` for a
    #: ``derived`` one, where Σ(holdings) *is* the balance.
    stated_balance_account: Decimal | None = None

    @property
    def balance_account(self) -> Decimal:
        """What the account is worth in its own currency: the stated balance where
        a provider vouches for one, Σ(holdings) where the ledger derives it."""
        if self.balance_source == "stated":
            return self.stated_balance_account or ZERO
        return self.market_value_account

    @property
    def unpriced(self) -> list[HoldingValue]:
        return [h for h in self.holdings if h.reason == NO_PRICE]

    @property
    def no_rate(self) -> list[HoldingValue]:
        return [h for h in self.holdings if h.reason == NO_RATE]

    @property
    def oldest_price_date(self) -> date | None:
        dates = [h.price_date for h in self.holdings if h.price_date is not None]
        return min(dates) if dates else None

    @property
    def max_stale_days(self) -> int | None:
        ages = [h.stale_days for h in self.holdings if h.stale_days is not None]
        return max(ages) if ages else None

    @property
    def is_fully_valued(self) -> bool:
        return not self.unpriced and not self.no_rate


async def latest_prices(
    session: AsyncSession, security_ids: set[uuid.UUID] | list[uuid.UUID], on: date
) -> dict[uuid.UUID, PricePoint]:
    """The latest price per security on or before ``on`` — one query, not N.

    ``DISTINCT ON`` is the Postgres idiom for "latest row per group", and doing it
    in the database is what keeps ``net_worth_at`` from issuing one price query per
    holding per point on the series.
    """
    ids = list(security_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                SecurityPrice.security_id,
                SecurityPrice.price,
                SecurityPrice.currency,
                SecurityPrice.price_date,
            )
            .where(SecurityPrice.security_id.in_(ids), SecurityPrice.price_date <= on)
            .distinct(SecurityPrice.security_id)
            .order_by(SecurityPrice.security_id, SecurityPrice.price_date.desc())
        )
    ).all()
    return {
        sid: PricePoint(price=price, currency=ccy, price_date=pd)
        for sid, price, ccy, pd in rows
    }


async def _value_holdings(
    session: AsyncSession,
    positions: list[PositionRecord],
    securities: dict[uuid.UUID, Security],
    *,
    on: date,
    base_ccy: str,
    account_currency: str,
) -> list[HoldingValue]:
    prices = await latest_prices(session, {p.security_id for p in positions}, on)
    out: list[HoldingValue] = []
    for h in positions:
        sec = securities[h.security_id]
        point = prices.get(h.security_id)
        base = HoldingValue(
            holding_id=h.holding_id,
            account_id=h.account_id,
            security_id=h.security_id,
            name=sec.name,
            ticker=sec.ticker,
            security_type=sec.security_type,
            quantity=h.quantity,
            price=None,
            price_date=None,
            price_currency=None,
            value_native=None,
            value_account=None,
            value_base=None,
            stale_days=None,
        )
        if point is None:
            base.reason = NO_PRICE
            out.append(base)
            continue

        native = h.quantity * point.price
        # Two hops, and they are not the same hop: → the account, because that is
        # the unit its balance is denominated in, and → base, because that is the
        # unit net worth is. A holding whose security trades in the account's own
        # currency short-circuits both (get_multiplier returns 1 for identity).
        to_account = await _convert_ccy(
            session,
            amount=native,
            from_ccy=point.currency,
            to_ccy=account_currency,
            on=on,
            base_ccy=base_ccy,
        )
        to_base = await _convert_ccy(
            session,
            amount=native,
            from_ccy=point.currency,
            to_ccy=base_ccy,
            on=on,
            base_ccy=base_ccy,
        )
        base.price = point.price
        base.price_date = point.price_date
        base.price_currency = point.currency
        base.value_native = native
        base.value_account = to_account
        base.value_base = to_base
        base.stale_days = (on - point.price_date).days
        if to_base is None:
            base.reason = NO_RATE
        out.append(base)
    return out


async def _convert_ccy(
    session: AsyncSession, *, amount: Decimal, from_ccy: str, to_ccy: str, on: date, base_ccy: str
) -> Decimal | None:
    """``amount`` between two arbitrary currencies, or ``None`` for "no rate".

    Deliberately not ``fx.to_base``: this has to convert *into the account's*
    currency as well as into base, and going via base for the first hop would add
    a conversion that can fail for a pair that is directly available.
    """
    m = await fx.get_multiplier(
        session, from_ccy=from_ccy, to_ccy=to_ccy, on=on, base_ccy=base_ccy
    )
    if m is None:
        return None
    return convert(amount, m)


async def value_account(
    session: AsyncSession, account: Account, on: date, base_ccy: str
) -> AccountValuation:
    """Value one investment account as of ``on`` (ADR-0011, ADR-0021)."""
    valuation = AccountValuation(
        account_id=account.id,
        name=account.name,
        currency=account.currency,
        balance_source=account.balance_source,
        on=on,
        base_currency=base_ccy,
    )
    # Not `select(Holding)`: a position can exist as recorded trades alone, and
    # valuing only the rows would silently omit it from Σ(holdings) — money the
    # household holds, missing from net worth.
    rows = list((await positions_for(session, account_ids=[account.id])).values())
    if not rows:
        valuation.market_value_base = ZERO
        valuation.market_value_account = ZERO
        if account.balance_source == "stated":
            # A stated account with no holdings: the whole balance is the plug.
            valuation.stated_balance_account = account.current_balance
            stated = await _convert_ccy(
                session,
                amount=account.current_balance,
                from_ccy=account.currency,
                to_ccy=base_ccy,
                on=on,
                base_ccy=base_ccy,
            )
            valuation.unaccounted_cash_base = (
                None if stated is None else quantize_storage(stated)
            )
        return valuation

    sec_ids = {h.security_id for h in rows}
    securities = {
        s.id: s
        for s in (
            await session.execute(select(Security).where(Security.id.in_(sec_ids)))
        ).scalars().all()
    }
    valuation.holdings = await _value_holdings(
        session,
        list(rows),
        securities,
        on=on,
        base_ccy=base_ccy,
        account_currency=account.currency,
    )
    valuation.market_value_base = quantize_storage(
        sum((h.value_base for h in valuation.holdings if h.value_base is not None), ZERO)
    )
    valuation.market_value_account = quantize_storage(
        sum((h.value_account for h in valuation.holdings if h.value_account is not None), ZERO)
    )

    if account.balance_source == "stated":
        # ADR-0021: the plug is what makes the allocation view reconcile to the
        # provider's balance. Computed here and never stored — see the note on
        # `Holding` for why a stored plug is a second writer of a derived fact.
        valuation.stated_balance_account = account.current_balance
        stated = await _convert_ccy(
            session,
            amount=account.current_balance,
            from_ccy=account.currency,
            to_ccy=base_ccy,
            on=on,
            base_ccy=base_ccy,
        )
        if stated is not None:
            valuation.unaccounted_cash_base = quantize_storage(
                stated - valuation.market_value_base
            )
    return valuation


async def value_portfolio(
    session: AsyncSession,
    *,
    on: date,
    base_ccy: str,
    account_ids: set[uuid.UUID] | None = None,
) -> tuple[list[AccountValuation], Decimal]:
    """Every investment account, valued, plus the household's securities in base.

    Returns ``(valuations, total_base)``. The total counts a ``stated`` account at
    its stated balance (ADR-0021 makes that the authoritative number for that
    account) and a ``derived`` one at Σ(holdings). This is the same rule
    ``net_worth_at`` must apply, and it lives here so the two cannot diverge.
    """
    stmt = select(Account).where(
        Account.type == "investment", Account.is_hidden.is_(False)
    )
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    accounts = list((await session.execute(stmt)).scalars().all())

    valuations: list[AccountValuation] = []
    total = ZERO
    for account in accounts:
        v = await value_account(session, account, on, base_ccy)
        valuations.append(v)
        if account.balance_source == "stated":
            stated = await _convert_ccy(
                session,
                amount=account.current_balance,
                from_ccy=account.currency,
                to_ccy=base_ccy,
                on=on,
                base_ccy=base_ccy,
            )
            if stated is not None:
                total += stated
            continue
        total += v.market_value_base
    return valuations, quantize_storage(total)


async def allocation(
    session: AsyncSession, *, on: date, base_ccy: str, group_by: str = "security"
) -> dict:
    """The consolidated cross-account allocation view (ADR-0011).

    One row per group with its share of the portfolio. ``unaccounted cash`` from
    ADR-0021 is folded into the ``cash`` group rather than listed as an unnamed
    line, so the percentages still sum to 100 and the thing the user sees is
    something they can reason about. A ``derived`` account's own cash holding is a
    real security and lands in the same group.
    """
    if group_by not in ("security", "type", "account", "currency"):
        raise ValueError(f"unsupported group_by: {group_by!r}")

    valuations, _total = await value_portfolio(session, on=on, base_ccy=base_ccy)
    groups: dict[tuple, dict] = {}

    def _add(key, label, value: Decimal) -> None:
        row = groups.setdefault(
            key, {"key": key, "label": label, "value_base": ZERO, "holdings": 0}
        )
        row["value_base"] += value
        row["holdings"] += 1

    for v in valuations:
        for h in v.holdings:
            # Unpriced positions are absent from the rows and reported apart. For a
            # `stated` account their value is not lost from the view either — it
            # lands in the plug below, because the plug is defined as everything the
            # stated balance is not accounted for by. `unpriced_positions` in the
            # response is what stops that from being invisible.
            if h.value_base is None:
                continue
            # Keys are strings even when they are ids: the grouping is a wire
            # vocabulary, and a UUID-or-string union would put the JSON type of
            # `key` at the mercy of which group_by the caller chose.
            if group_by == "security":
                _add(str(h.security_id), h.ticker or h.name, h.value_base)
            elif group_by == "type":
                _add(h.security_type, h.security_type, h.value_base)
            elif group_by == "currency":
                _add(h.price_currency or v.currency, h.price_currency or v.currency, h.value_base)
            else:
                _add(str(v.account_id), v.name, h.value_base)
        if v.unaccounted_cash_base:
            _add(CASH_SECURITY_TYPE, "Unaccounted cash", v.unaccounted_cash_base)

    total = quantize_storage(sum(r["value_base"] for r in groups.values()))
    rows = []
    for row in groups.values():
        value = quantize_storage(row["value_base"])
        pct = (
            Decimal("0") if total == 0 else (value / total * Decimal("100"))
        )
        rows.append({**row, "value_base": value, "percent": quantize_storage(pct)})
    rows.sort(key=lambda r: r["value_base"], reverse=True)

    unpriced = [h for v in valuations for h in v.unpriced]
    no_rate = [h for v in valuations for h in v.no_rate]
    return {
        "base_currency": base_ccy,
        "on": on,
        "group_by": group_by,
        "total_base": total,
        "rows": rows,
        "unpriced_positions": len(unpriced),
        "no_rate_positions": len(no_rate),
        "max_stale_days": max(
            (v.max_stale_days for v in valuations if v.max_stale_days is not None),
            default=None,
        ),
    }


# ---- cost basis (ADR-0020) -------------------------------------------------


HISTORY = "history"
MANUAL = "manual"


@dataclass(frozen=True)
class Position:
    """A position, resolved: what the household holds and what it cost.

    Both fields come from the *same* writer, always (ADR-0034) — mixing a
    history-derived basis with a scalar quantity is what makes ``basis / quantity``
    a ratio of unrelated numbers.
    """

    quantity: Decimal
    cost_basis: Decimal
    source: str


def fold_history(txs) -> tuple[Decimal, Decimal]:
    """``(quantity, basis)`` from one position's trade history, in order.

    Pure and ordered, so the same fold serves the single-position lookup and the
    batched one. Average cost, so a sale removes basis at the position's average:
    ``removed = basis × (shares_sold / shares_held)``. With no lots there is no
    other defensible answer, and the alternative — removing the cash originally
    paid for those exact shares — is precisely the specific-lot tracking v1 defers
    (ADR-0020).
    """
    basis = ZERO
    quantity = ZERO
    for t in txs:
        qty = t.quantity or ZERO
        if t.type == "buy":
            # `amount` is negative for a buy (cash out, ADR-0033's sign
            # convention), so the cost added is its negation. A buy whose `amount`
            # disagrees with quantity × price (a foreign-currency trade, or a fee
            # baked into the trade) uses the cash actually paid, which is the
            # better basis.
            basis += -t.amount
            quantity += qty
        elif t.type == "sell":
            if quantity != 0:
                # Guarded against an oversell: selling more than is held cannot
                # remove more basis than exists.
                basis -= basis * (min(-qty, quantity) / quantity)
            quantity += qty  # qty is negative
        elif t.type == "split":
            # A share split changes the share count and not the money invested.
            quantity += qty
        # dividend/interest/fee/transfer do not touch basis: the first two are
        # income, the third is a cost of holding rather than of acquiring, and a
        # transfer is the boundary itself (ADR-0033 §3).
    return quantity, quantize_storage(basis)


async def history_positions(
    session: AsyncSession, *, account_ids: set[uuid.UUID] | list[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID], Position]:
    """Every history-derived position for the given accounts, in one query.

    A list endpoint asking per holding would be one query per row; this is one for
    the lot, with the ordering the fold depends on applied in the database.
    """
    ids = list(account_ids)
    if not ids:
        return {}
    txs = list(
        (
            await session.execute(
                select(InvestmentTransaction)
                .where(
                    InvestmentTransaction.account_id.in_(ids),
                    InvestmentTransaction.security_id.is_not(None),
                )
                .order_by(
                    InvestmentTransaction.account_id,
                    InvestmentTransaction.security_id,
                    InvestmentTransaction.trade_date,
                    InvestmentTransaction.created_at,
                    InvestmentTransaction.id,
                )
            )
        ).scalars().all()
    )
    grouped: dict[tuple[uuid.UUID, uuid.UUID], list] = {}
    for t in txs:
        grouped.setdefault((t.account_id, t.security_id), []).append(t)
        # `security_id` is nullable — an account-level fee is history for no
        # position — and is filtered out above rather than folded into one.
    out: dict[tuple[uuid.UUID, uuid.UUID], Position] = {}
    for key, rows in grouped.items():
        quantity, basis = fold_history(rows)
        out[key] = Position(quantity=quantity, cost_basis=basis, source=HISTORY)
    return out


async def cost_basis_from_history(
    session: AsyncSession, *, account_id: uuid.UUID, security_id: uuid.UUID
) -> Decimal | None:
    """Average-cost basis from ``investment_transactions``, or ``None`` if none.

    ``None`` is the signal to fall back to the manual scalar — ADR-0020's single
    authority, where the discriminator is whether history exists at all and not
    who wrote last.
    """
    positions = await history_positions(session, account_ids=[account_id])
    position = positions.get((account_id, security_id))
    return None if position is None else position.cost_basis


async def effective_position(session: AsyncSession, holding: Holding) -> Position:
    """The position as it actually is: history wins if it exists (ADR-0020/0034)."""
    derived = (await history_positions(session, account_ids=[holding.account_id])).get(
        (holding.account_id, holding.security_id)
    )
    if derived is not None:
        return derived
    return Position(
        quantity=holding.quantity,
        cost_basis=holding.cost_basis if holding.cost_basis is not None else ZERO,
        source=MANUAL,
    )


async def effective_cost_basis(
    session: AsyncSession, holding: Holding
) -> tuple[Decimal | None, str]:
    """``(basis, source)`` where source is ``history`` or ``manual``."""
    position = await effective_position(session, holding)
    return position.cost_basis, position.source


@dataclass
class PositionRecord:
    """One position a household holds, resolved, with where its numbers came from.

    **A position can exist with no ``holdings`` row at all.** ADR-0034 makes the
    trades the position when trades exist, so a buy recorded against a security the
    account has never held *is* a position — ``holding`` is ``None`` and the
    quantity and basis are the fold. Requiring a hand-created row as well would be
    the double-write ADR-0034 exists to kill, and it would fail in the direction
    that costs money: a position the household owns, valued at nothing.
    """

    account_id: uuid.UUID
    security_id: uuid.UUID
    holding: Holding | None
    quantity: Decimal
    cost_basis: Decimal
    source: str

    @property
    def holding_id(self) -> uuid.UUID | None:
        return None if self.holding is None else self.holding.id

    @property
    def as_of(self) -> date | None:
        # Manual-only, by construction: a held-in-history position has no
        # confirmation date to report, and inventing one from the last trade would
        # claim a human checked something they did not.
        return None if self.holding is None else self.holding.as_of


async def positions_for(
    session: AsyncSession, *, account_ids: set[uuid.UUID] | list[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID], PositionRecord]:
    """Every position in these accounts — hand-entered rows **and** recorded
    trades, merged on ``(account, security)``.

    Two queries plus the history fold, keyed so a caller can iterate positions
    rather than rows. This is the one place that decision is made, so listing and
    valuation cannot disagree about what the household holds.
    """
    ids = list(account_ids)
    if not ids:
        return {}
    holdings = list(
        (
            await session.execute(select(Holding).where(Holding.account_id.in_(ids)))
        )
        .scalars()
        .all()
    )
    history = await history_positions(session, account_ids=ids)

    out: dict[tuple[uuid.UUID, uuid.UUID], PositionRecord] = {}
    for holding in holdings:
        out[(holding.account_id, holding.security_id)] = PositionRecord(
            account_id=holding.account_id,
            security_id=holding.security_id,
            holding=holding,
            quantity=holding.quantity,
            cost_basis=holding.cost_basis if holding.cost_basis is not None else ZERO,
            source=MANUAL,
        )
    # History overwrites, and keeps the row when there was one: the row still
    # carries `as_of`, and the FK on deleting the position still has a target.
    for key, position in history.items():
        existing = out.get(key)
        out[key] = PositionRecord(
            account_id=key[0],
            security_id=key[1],
            holding=None if existing is None else existing.holding,
            quantity=position.quantity,
            cost_basis=position.cost_basis,
            source=HISTORY,
        )
    # A closed position is the absence of a position — the rule the `quantity <> 0`
    # check constraint states for rows, applied to the derived case. This is the
    # sharper half of ADR-0034: a position fully sold reads 0 from its trades, and
    # 0 × price is 0, so keeping it would only render a 0% line forever. A negative
    # quantity is a short and is kept.
    return {key: rec for key, rec in out.items() if rec.quantity != 0}


@dataclass
class HoldingRecord:
    """A position, its instrument, and the row behind it (when there is one) —
    what a list endpoint has to show and cannot get from the row alone."""

    position: PositionRecord
    security: Security

    @property
    def holding(self) -> Holding | None:
        return self.position.holding


async def list_holdings(
    session: AsyncSession, *, account_id: uuid.UUID | None = None
) -> list[HoldingRecord]:
    """Every position with its resolved quantity and basis.

    Batched: resolving each position with ``effective_position`` would be one
    history query per row, which is the N+1 ``history_positions`` exists to avoid.
    """
    if account_id is not None:
        account_ids = [account_id]
    else:
        account_ids = list(
            (
                await session.execute(select(Account.id).where(Account.type == "investment"))
            )
            .scalars()
            .all()
        )
    positions = await positions_for(session, account_ids=account_ids)
    if not positions:
        return []
    securities = {
        s.id: s
        for s in (
            await session.execute(
                select(Security).where(Security.id.in_({k[1] for k in positions}))
            )
        )
        .scalars()
        .all()
    }
    records: list[HoldingRecord] = []
    for key in sorted(positions, key=lambda k: (str(k[0]), str(k[1]))):
        security = securities.get(key[1])
        if security is None:  # FK is ON DELETE CASCADE, so unreachable in practice
            continue
        records.append(HoldingRecord(position=positions[key], security=security))
    return records


async def _accounts_holding_security(
    session: AsyncSession, security_id: uuid.UUID
) -> set[uuid.UUID]:
    """Every account with a position in this security — from **both** writers.

    Recorded trades matter here as much as rows do: ``investment_transactions``
    references the security with ``ON DELETE SET NULL``, so deleting it leaves the
    trades in place and the position's quantity changes. Recomputing only the
    accounts with a ``holdings`` row would leave the others' derived balances
    describing a position that no longer exists.
    """
    accounts = set(
        (
            await session.execute(select(Holding.account_id).where(Holding.security_id == security_id))
        )
        .scalars()
        .all()
    )
    accounts |= set(
        (
            await session.execute(
                select(InvestmentTransaction.account_id).where(
                    InvestmentTransaction.security_id == security_id
                )
            )
        )
        .scalars()
        .all()
    )
    return accounts


async def list_securities(session: AsyncSession) -> list[Security]:
    return list(
        (
            await session.execute(select(Security).order_by(Security.name, Security.id))
        )
        .scalars()
        .all()
    )


async def list_security_prices(
    session: AsyncSession,
    *,
    security_id: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
) -> list[SecurityPrice]:
    stmt = select(SecurityPrice).where(SecurityPrice.security_id == security_id)
    if start is not None:
        stmt = stmt.where(SecurityPrice.price_date >= start)
    if end is not None:
        stmt = stmt.where(SecurityPrice.price_date <= end)
    return list(
        (await session.execute(stmt.order_by(SecurityPrice.price_date))).scalars().all()
    )


async def list_investment_transactions(
    session: AsyncSession,
    *,
    account_id: uuid.UUID | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 500,
) -> list[InvestmentTransaction]:
    stmt = select(InvestmentTransaction)
    if account_id is not None:
        stmt = stmt.where(InvestmentTransaction.account_id == account_id)
    if start is not None:
        stmt = stmt.where(InvestmentTransaction.trade_date >= start)
    if end is not None:
        stmt = stmt.where(InvestmentTransaction.trade_date <= end)
    # Newest first, then by id: `trade_date` is a day, so every trade on one day
    # would otherwise come back in whatever order the heap offered — and a page
    # boundary through an unstable sort drops and repeats rows.
    stmt = stmt.order_by(
        InvestmentTransaction.trade_date.desc(), InvestmentTransaction.id.desc()
    ).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


# ---- the derived-balance writer (ADR-0021) ---------------------------------


async def recompute_derived_balance(
    session: AsyncSession, account: Account, *, on: date | None = None, base_ccy: str
) -> Decimal | None:
    """Set a ``derived`` account's balance to Σ(holding market values).

    Returns the new balance, or ``None`` when the account is ``stated`` and was
    left alone. This is the single implementation of ADR-0011's balance rule; the
    sync path must not snapshot a derived account (ADR-0021), and anything else
    that touches holdings calls this rather than doing its own sum.

    The balance is written in the account's currency and a snapshot is taken
    through ``ledger.upsert_balance_snapshot``, so the historical series and the
    current value come from one code path.
    """
    if account.balance_source != "derived":
        return None
    d = on or date.today()
    valuation = await value_account(session, account, d, base_ccy)
    account.current_balance = valuation.market_value_account
    # Only stamp the balance date when everything could be valued: a snapshot taken
    # from a partial sum would enter the net-worth history as a real (too low)
    # balance that no later recompute would revisit, since snapshots are keyed by
    # date and this one would look authoritative.
    if valuation.is_fully_valued:
        account.balance_date = d
        await upsert_balance_snapshot(session, account)
    return account.current_balance


# ---- writes -----------------------------------------------------------------
#
# Every function that can move a derived account's value ends by recomputing it.
# That is not decoration: ADR-0011 makes the balance a function of the holdings,
# so a write that skips the recompute leaves ``current_balance`` disagreeing with
# the positions it is supposed to be the sum of, and net worth reads the stale
# number. Collecting the affected account ids *before* a delete is the part that
# is easy to get wrong — after the cascade there is nothing left to ask.


async def _recompute_accounts(
    session: AsyncSession, account_ids: set[uuid.UUID], *, on: date | None = None
) -> None:
    """Re-derive the balance of every ``derived`` account in ``account_ids``."""
    for account_id in account_ids:
        account = (
            await session.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if account is None or account.balance_source != "derived":
            continue
        base = await base_currency(session, account.household_id)
        await recompute_derived_balance(session, account, on=on, base_ccy=base)




# ---- securities -------------------------------------------------------------


async def get_security(session: AsyncSession, security_id: uuid.UUID) -> Security:
    security = (
        await session.execute(select(Security).where(Security.id == security_id))
    ).scalar_one_or_none()
    if security is None:
        raise LedgerError("Security not found", 404)
    return security


async def create_security(session: AsyncSession, household_id: uuid.UUID, data) -> Security:
    if data.security_type not in SECURITY_TYPES:
        raise LedgerError(f"Unknown security type {data.security_type!r}", 422)
    security = Security(
        household_id=household_id,
        name=data.name.strip(),
        ticker=(data.ticker or "").strip().upper() or None,
        security_type=data.security_type,
        currency=data.currency.upper(),
        is_manual=True,
    )
    session.add(security)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The partial unique index on (household, ticker, currency). Named here
        # rather than surfaced as a 500, because "you already have this ticker" is
        # a thing the household can act on.
        raise LedgerError(
            f'A security with ticker "{security.ticker}" in {security.currency} already exists',
            409,
        ) from exc
    return security


async def update_security(session: AsyncSession, security_id: uuid.UUID, data) -> Security:
    """Rename, re-ticker or re-type. **Currency is not editable**: it is half of
    the instrument's identity (see ``Security``) and changing it would silently
    restate every price already recorded against the security."""
    security = await get_security(session, security_id)
    if is_set(data, "name") and data.name is not None:
        security.name = data.name.strip()
    if is_set(data, "ticker"):
        security.ticker = (data.ticker or "").strip().upper() or None
    if is_set(data, "security_type") and data.security_type is not None:
        if data.security_type not in SECURITY_TYPES:
            raise LedgerError(f"Unknown security type {data.security_type!r}", 422)
        security.security_type = data.security_type
    try:
        await session.flush()
    except IntegrityError as exc:
        raise LedgerError(
            f'A security with ticker "{security.ticker}" already exists', 409
        ) from exc
    return security


async def delete_security(session: AsyncSession, security_id: uuid.UUID) -> None:
    """Delete a security, its prices and its positions. Recomputes the accounts
    it was held in — hence collecting them first: the cascade takes the holdings
    with it, and afterwards there is no way to ask which accounts were affected.
    """
    security = await get_security(session, security_id)
    # Collected before the delete, and from both writers: afterwards there is no
    # way to ask which accounts were affected (the holdings cascade away and the
    # trades keep their rows with a nulled security).
    affected = await _accounts_holding_security(session, security_id)
    await session.delete(security)
    await session.flush()
    await _recompute_accounts(session, affected)


# ---- prices -----------------------------------------------------------------


async def upsert_price(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    security_id: uuid.UUID,
    price_date: date,
    price: Decimal,
    source: str = "manual",
) -> SecurityPrice:
    """Write one point of the price series, then revalue what holds it.

    An upsert rather than an insert because ``(security_id, price_date)`` is
    unique: correcting today's price is a normal thing to do, and a second row for
    the same day would make "the latest price on or before D" depend on which row
    the planner reached first.
    """
    security = await get_security(session, security_id)
    if price < 0:
        raise LedgerError("A price cannot be negative", 422)

    existing = (
        await session.execute(
            select(SecurityPrice).where(
                SecurityPrice.security_id == security_id,
                SecurityPrice.price_date == price_date,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.price = price
        existing.source = source
        row = existing
    else:
        row = SecurityPrice(
            household_id=household_id,
            security_id=security_id,
            price_date=price_date,
            price=price,
            currency=security.currency,
            source=source,
        )
        session.add(row)
    await session.flush()

    # A price change is a value change for every account holding it, so the
    # balances are only correct once this runs. Valuing as of *today*, not as of
    # `price_date`: the account's current balance should reflect the newest price,
    # whether that price is dated today or is a back-dated correction.
    await _recompute_accounts(
        session, await _accounts_holding_security(session, security_id)
    )
    return row


# ---- holdings ---------------------------------------------------------------


async def get_holding(session: AsyncSession, holding_id: uuid.UUID) -> Holding:
    holding = (
        await session.execute(select(Holding).where(Holding.id == holding_id))
    ).scalar_one_or_none()
    if holding is None:
        raise LedgerError("Holding not found", 404)
    return holding


async def upsert_holding(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    security_id: uuid.UUID,
    quantity: Decimal,
    cost_basis: Decimal | None = None,
    as_of: date | None = None,
) -> Holding:
    """Create or replace a position. One position per (account, security), so a
    second call updates rather than appending a duplicate that would double the
    account's value."""
    await get_security(session, security_id)
    account = (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise LedgerError("Account not found", 404)
    if account.type != "investment":
        # The mirror of the check on `create_investment_transaction`, and the other
        # half of ADR-0033 §1: a depository account's balance is its transaction
        # sum, so a position in it would be valued by nobody and would quietly
        # misdescribe the account it hangs off.
        raise LedgerError("Holdings belong to investment accounts", 422)
    if quantity == 0:
        raise LedgerError("A zero quantity is the absence of a position", 422)

    holding = (
        await session.execute(
            select(Holding).where(
                Holding.account_id == account_id, Holding.security_id == security_id
            )
        )
    ).scalar_one_or_none()
    if holding is None:
        holding = Holding(
            household_id=household_id,
            account_id=account_id,
            security_id=security_id,
            quantity=quantity,
            cost_basis=cost_basis,
            as_of=as_of,
        )
        session.add(holding)
    else:
        await _reject_manual_write(session, holding, "quantity")
        holding.quantity = quantity
        if cost_basis is not None:
            holding.cost_basis = cost_basis
        if as_of is not None:
            holding.as_of = as_of
    await session.flush()
    await _recompute_accounts(session, {account_id})
    return holding


async def update_holding(session: AsyncSession, holding_id: uuid.UUID, data) -> Holding:
    holding = await get_holding(session, holding_id)
    if is_set(data, "quantity") and data.quantity is not None:
        await _reject_manual_write(session, holding, "quantity")
        if data.quantity == 0:
            raise LedgerError("A zero quantity is the absence of a position", 422)
        holding.quantity = data.quantity
    if is_set(data, "cost_basis") and data.cost_basis is not None:
        await _reject_manual_write(session, holding, "cost_basis")
        holding.cost_basis = data.cost_basis
    if is_set(data, "as_of"):
        holding.as_of = data.as_of
    await session.flush()
    await _recompute_accounts(session, {holding.account_id})
    return holding


async def delete_holding(session: AsyncSession, holding_id: uuid.UUID) -> None:
    holding = await get_holding(session, holding_id)
    account_id = holding.account_id
    await session.delete(holding)
    await session.flush()
    await _recompute_accounts(session, {account_id})


async def _reject_manual_write(
    session: AsyncSession, holding: Holding, field: str
) -> None:
    """409 when history owns the field (ADR-0020/0034).

    Refused rather than ignored. A silently-dropped write is worse than a
    rejection: the client believes a value was stored and the next read
    contradicts it with nothing to explain the difference.
    """
    current = (await history_positions(session, account_ids=[holding.account_id])).get(
        (holding.account_id, holding.security_id)
    )
    if current is not None:
        raise LedgerError(
            f"This position's {field} is computed from its recorded trades, so it "
            f"cannot be set by hand. Edit the trades instead, or delete them to "
            f"return the position to a manual one.",
            409,
        )


# ---- investment transactions ------------------------------------------------


async def get_investment_transaction(
    session: AsyncSession, txn_id: uuid.UUID
) -> InvestmentTransaction:
    txn = (
        await session.execute(
            select(InvestmentTransaction).where(InvestmentTransaction.id == txn_id)
        )
    ).scalar_one_or_none()
    if txn is None:
        raise LedgerError("Investment transaction not found", 404)
    return txn


async def create_investment_transaction(
    session: AsyncSession, household_id: uuid.UUID, data
) -> InvestmentTransaction:
    """Record one investment event.

    Does **not** touch a holding: since ADR-0034 a position with history has its
    quantity derived from that history, so writing a trade and *also* adjusting the
    scalar would be the double-write rule that ADR exists to prevent. The
    recompute at the end is what makes the position reflect the new row.
    """
    if data.type not in INVESTMENT_TX_TYPES:
        raise LedgerError(f"Unknown investment transaction type {data.type!r}", 422)
    if data.security_id is not None:
        await get_security(session, data.security_id)

    account = (
        await session.execute(select(Account).where(Account.id == data.account_id))
    ).scalar_one_or_none()
    if account is None:
        raise LedgerError("Account not found", 404)

    # ADR-0033 §1: an investment account has no cash-ledger transactions, and
    # symmetrically a depository account has no investment events. Enforcing it
    # here keeps the invariant the appreciation term depends on from being
    # violated by one API call.
    if account.type != "investment":
        raise LedgerError(
            "Investment transactions belong to investment accounts", 422
        )

    txn = InvestmentTransaction(
        household_id=household_id,
        account_id=data.account_id,
        security_id=data.security_id,
        type=data.type,
        trade_date=data.trade_date,
        quantity=data.quantity,
        price=data.price,
        amount=data.amount,
        currency=account.currency,  # ≡ account.currency, as on `transactions`
        description=data.description,
        notes=data.notes,
        source="manual",
        field_sources={
            f: "user"
            for f in ("amount", "trade_date", "quantity", "price", "type", "security")
        },
    )
    session.add(txn)
    await session.flush()
    await _recompute_accounts(session, {data.account_id})
    return txn


async def update_investment_transaction(
    session: AsyncSession, txn_id: uuid.UUID, data
) -> InvestmentTransaction:
    txn = await get_investment_transaction(session, txn_id)
    if is_set(data, "type") and data.type is not None:
        if data.type not in INVESTMENT_TX_TYPES:
            raise LedgerError(f"Unknown investment transaction type {data.type!r}", 422)
        txn.type = data.type
        txn.field_sources = {**(txn.field_sources or {}), "type": "user"}
    for field in ("trade_date", "quantity", "price", "amount", "description", "notes"):
        if is_set(data, field):
            setattr(txn, field, getattr(data, field))
            if field in ("trade_date", "quantity", "price", "amount"):
                txn.field_sources = {**(txn.field_sources or {}), field: "user"}
    await session.flush()
    await _recompute_accounts(session, {txn.account_id})
    return txn


async def delete_investment_transaction(session: AsyncSession, txn_id: uuid.UUID) -> None:
    txn = await get_investment_transaction(session, txn_id)
    account_id = txn.account_id
    await session.delete(txn)
    await session.flush()
    # Deleting a trade can move the position it belongs to, which is why this is a
    # recompute and not a formality (ADR-0034).
    await _recompute_accounts(session, {account_id})
