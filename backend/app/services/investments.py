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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import convert, quantize_storage
from app.models import Account, Holding, InvestmentTransaction, Security, SecurityPrice
from app.models.investments import CASH_SECURITY_TYPE
from app.services import fx
from app.services.ledger import upsert_balance_snapshot

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

    holding_id: uuid.UUID
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
    holdings: list[Holding],
    securities: dict[uuid.UUID, Security],
    *,
    on: date,
    base_ccy: str,
    account_currency: str,
) -> list[HoldingValue]:
    prices = await latest_prices(session, {h.security_id for h in holdings}, on)
    out: list[HoldingValue] = []
    for h in holdings:
        sec = securities[h.security_id]
        point = prices.get(h.security_id)
        base = HoldingValue(
            holding_id=h.id,
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
    rows = (
        await session.execute(select(Holding).where(Holding.account_id == account.id))
    ).scalars().all()
    if not rows:
        valuation.market_value_base = ZERO
        valuation.market_value_account = ZERO
        if account.balance_source == "stated":
            # A stated account with no holdings: the whole balance is the plug.
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
            if group_by == "security":
                _add(h.security_id, h.ticker or h.name, h.value_base)
            elif group_by == "type":
                _add(h.security_type, h.security_type, h.value_base)
            elif group_by == "currency":
                _add(h.price_currency or v.currency, h.price_currency or v.currency, h.value_base)
            else:
                _add(v.account_id, v.name, h.value_base)
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
    return {
        "base_currency": base_ccy,
        "on": on,
        "group_by": group_by,
        "total_base": total,
        "rows": rows,
        "unpriced_positions": len(unpriced),
        "max_stale_days": max(
            (v.max_stale_days for v in valuations if v.max_stale_days is not None),
            default=None,
        ),
    }


# ---- cost basis (ADR-0020) -------------------------------------------------


async def cost_basis_from_history(
    session: AsyncSession, *, account_id: uuid.UUID, security_id: uuid.UUID
) -> Decimal | None:
    """Average-cost basis derived from ``investment_transactions`` (ADR-0020).

    Returns ``None`` when this position has **no** trade history, which is the
    signal for the caller to fall back to the manually-entered
    ``holdings.cost_basis``. That is ADR-0020's "single authority" rule: the two
    are never both authoritative, and the discriminator is whether history exists
    at all — not who wrote last.

    Average cost, so a sale removes basis at the position's average:
    ``removed = basis × (shares_sold / shares_held)``. With no lots there is no
    other defensible answer, and the alternative (removing the original cash paid
    for those exact shares) is precisely the specific-lot tracking v1 defers.
    """
    txs = list(
        (
            await session.execute(
                select(InvestmentTransaction)
                .where(
                    InvestmentTransaction.account_id == account_id,
                    InvestmentTransaction.security_id == security_id,
                )
                .order_by(
                    InvestmentTransaction.trade_date,
                    InvestmentTransaction.created_at,
                    InvestmentTransaction.id,
                )
            )
        ).scalars().all()
    )
    if not txs:
        return None

    basis = ZERO
    quantity = ZERO
    for t in txs:
        qty = t.quantity or ZERO
        if t.type == "buy":
            # `amount` is negative for a buy (cash out, ADR-0033's sign convention),
            # so the cost added is its negation. A buy whose `amount` disagrees with
            # quantity × price (a foreign-currency trade, or a fee baked into the
            # trade) uses the cash actually paid, which is the better basis.
            basis += -t.amount
            quantity += qty
        elif t.type == "sell":
            if quantity != 0:
                basis -= basis * (min(-qty, quantity) / quantity)
            quantity += qty  # qty is negative
        elif t.type == "split":
            # A share split changes the share count and not the money invested.
            # `quantity` on the row carries the *delta*, and `price` is unused.
            quantity += qty
        # dividend/interest/fee/transfer do not touch basis: the first two are
        # income, the third is a cost of holding rather than of acquiring, and a
        # transfer is the boundary itself (ADR-0033 §3).
    return quantize_storage(basis)


async def effective_cost_basis(
    session: AsyncSession, holding: Holding
) -> tuple[Decimal | None, str]:
    """``(basis, source)`` where source is ``history`` or ``manual`` (ADR-0020)."""
    derived = await cost_basis_from_history(
        session, account_id=holding.account_id, security_id=holding.security_id
    )
    if derived is not None:
        return derived, "history"
    return holding.cost_basis, "manual"


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
