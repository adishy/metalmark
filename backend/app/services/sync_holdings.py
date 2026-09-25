"""The bank's holdings, landed as positions (ADR-0051).

SimpleFIN sends a ``holdings`` list for brokerage accounts: symbol, description,
shares, market value, cost basis. Sync used to read it only to guess that an
account is an investment account. This module makes each one a position:

* **A security** per instrument — by ticker when the bank gives one, by
  description when it does not, and one ``cash`` security per currency for a
  cash line. A security that already exists is reused and never edited: its name
  and type may be a human's corrections.
* **A price** for the day — market value ÷ shares, source ``auto``. A price a
  human entered for that day stands.
* **A holding** with ``source = 'simplefin'`` — the shares and total basis. Lots of
  one instrument arrive as several lines and are summed into its one position.

The account stays ``stated`` (ADR-0021). Its value is still the bank's balance.
The positions explain it, and whatever they do not cover (usually cash the bank
did not list) is the unaccounted-cash remainder, read at valuation time.

Sync mirrors the bank. A synced position the bank stops reporting is removed. A
hand-entered position, or one whose trades are recorded (ADR-0034), is never
written or removed here. When the bank reports that same instrument too, the
bank's line is skipped and counted, so the hand-entered row is not overwritten.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account
from app.models.investments import CASH_SECURITY_TYPE, Holding, Security, SecurityPrice
from app.services.aggregator import ProviderAccount, ProviderHolding
from app.services.investments import history_positions

SYNCED = "simplefin"

#: Symbols bridges use for a cash line. A currency code (``USD``) is also one.
CASH_SYMBOLS = frozenset({"CASH", "$CASH", "CASH$", "CUR", "MONEY"})

_QTY = Decimal("0.00000001")
_MONEY = Decimal("0.0001")

#: Why a line was not landed — the codes in a ``holdings.synced`` event.
NOT_INVESTMENT = "account_not_investment"
DERIVED = "account_derived"
NO_SHARES = "no_shares"
NO_IDENTITY = "no_identity"
NEGATIVE_PRICE = "negative_price"
HAND_ENTERED = "hand_entered"
HAS_HISTORY = "has_history"


@dataclass(slots=True)
class HoldingsOutcome:
    """What one account's holdings became."""

    seen: int = 0
    written: int = 0
    removed: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def changed(self) -> bool:
        return bool(self.seen or self.removed)


@dataclass(slots=True)
class _Line:
    """The lines of one instrument, summed."""

    kind: str  # "ticker" | "name" | "cash"
    key: str
    name: str
    currency: str
    quantity: Decimal = Decimal(0)
    market_value: Decimal = Decimal(0)
    cost_basis: Decimal | None = None
    lines: int = 0


def _is_cash(h: ProviderHolding, currency: str) -> bool:
    symbol = (h.symbol or "").strip().upper()
    if symbol in CASH_SYMBOLS or symbol == currency:
        return True
    # A line with no symbol and no share count, named as cash.
    return not symbol and not h.shares and "cash" in (h.description or "").lower()


def _guess_type(name: str) -> str:
    """A first guess at a new security's type. It is only a default; the human's
    edit wins, because sync never updates a security it did not just create."""
    lowered = f" {name.lower()} "
    if " etf " in lowered or "exchange traded" in lowered:
        return "etf"
    if " bond" in lowered or "treasury" in lowered:
        return "bond"
    if " fund" in lowered or " index " in lowered or " trust " in lowered:
        return "mutual_fund"
    return "stock"


def _group(
    pa: ProviderAccount, account_currency: str, outcome: HoldingsOutcome
) -> dict[tuple[str, str, str], _Line]:
    lines: dict[tuple[str, str, str], _Line] = {}
    for h in pa.holdings:
        outcome.seen += 1
        currency = (h.currency or account_currency).upper()
        if _is_cash(h, currency):
            line = lines.setdefault(
                ("cash", "", currency),
                _Line(kind="cash", key="", name=f"{currency} cash", currency=currency),
            )
            line.quantity += h.market_value
            line.market_value += h.market_value
            line.lines += 1
            continue
        if not h.shares:
            outcome.skipped[NO_SHARES] += 1
            continue
        if h.symbol:
            kind, key = "ticker", h.symbol.strip().upper()[:32]
            name = h.description or key
        elif h.description:
            kind, key = "name", h.description[:200]
            name = key
        else:
            outcome.skipped[NO_IDENTITY] += 1
            continue
        if (h.market_value < 0) != (h.shares < 0) and h.market_value != 0:
            outcome.skipped[NEGATIVE_PRICE] += 1
            continue
        line = lines.setdefault(
            (kind, key, currency), _Line(kind=kind, key=key, name=name[:200], currency=currency)
        )
        line.quantity += h.shares
        line.market_value += h.market_value
        if h.cost_basis is not None:
            line.cost_basis = (line.cost_basis or Decimal(0)) + h.cost_basis
        line.lines += 1
    return lines


async def _security_for(
    session: AsyncSession, household_id: uuid.UUID, line: _Line
) -> Security:
    q = select(Security).where(Security.currency == line.currency)
    if line.kind == "ticker":
        q = q.where(Security.ticker == line.key)
    elif line.kind == "name":
        q = q.where(
            Security.ticker.is_(None),
            Security.name == line.key,
            Security.security_type != CASH_SECURITY_TYPE,
        )
    else:
        q = q.where(Security.ticker.is_(None), Security.security_type == CASH_SECURITY_TYPE)
    found = (
        await session.execute(q.order_by(Security.created_at, Security.id).limit(1))
    ).scalar_one_or_none()
    if found is not None:
        return found
    security = Security(
        household_id=household_id,
        name=line.name,
        ticker=line.key if line.kind == "ticker" else None,
        security_type=CASH_SECURITY_TYPE if line.kind == "cash" else _guess_type(line.name),
        currency=line.currency,
        is_manual=False,
    )
    session.add(security)
    await session.flush()
    return security


async def _record_price(
    session: AsyncSession, household_id: uuid.UUID, security: Security, *, on: date,
    price: Decimal,
) -> None:
    existing = (
        await session.execute(
            select(SecurityPrice).where(
                SecurityPrice.security_id == security.id, SecurityPrice.price_date == on
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            SecurityPrice(
                household_id=household_id,
                security_id=security.id,
                price_date=on,
                price=price,
                currency=security.currency,
                source="auto",
            )
        )
    elif existing.source != "manual":
        existing.price = price


async def sync_holdings(
    session: AsyncSession, account: Account, pa: ProviderAccount, *, on: date
) -> HoldingsOutcome:
    """Make this account's synced positions what the bank just reported."""
    outcome = HoldingsOutcome()
    if account.type != "investment":
        # A human re-typed it, or it was never typed investment. Positions there
        # would be valued by nothing (ADR-0033 §1).
        if pa.holdings:
            outcome.seen = len(pa.holdings)
            outcome.skipped[NOT_INVESTMENT] = len(pa.holdings)
        return outcome
    if account.balance_source == "derived":
        # Its value is the sum of positions a human entered (migration 0006 kept
        # exactly those derived). Adding the bank's beside them would count twice.
        if pa.holdings:
            outcome.seen = len(pa.holdings)
            outcome.skipped[DERIVED] = len(pa.holdings)
        return outcome

    lines = _group(pa, account.currency, outcome)
    existing = {
        h.security_id: h
        for h in (
            await session.execute(select(Holding).where(Holding.account_id == account.id))
        ).scalars()
    }
    history = await history_positions(session, account_ids=[account.id])
    kept: set[uuid.UUID] = set()

    for line in lines.values():
        security = await _security_for(session, account.household_id, line)
        quantity = line.quantity.quantize(_QTY, rounding=ROUND_HALF_EVEN)
        if quantity == 0:
            continue  # closed; if synced before, it is removed below
        held = existing.get(security.id)
        if held is not None and held.source != SYNCED:
            outcome.skipped[HAND_ENTERED] += line.lines
            continue
        if (account.id, security.id) in history:
            outcome.skipped[HAS_HISTORY] += line.lines
            continue
        price = (
            Decimal(1)
            if line.kind == "cash"
            else (line.market_value / line.quantity).quantize(_QTY, rounding=ROUND_HALF_EVEN)
        )
        await _record_price(session, account.household_id, security, on=on, price=price)
        basis = (
            None if line.cost_basis is None
            else line.cost_basis.quantize(_MONEY, rounding=ROUND_HALF_EVEN)
        )
        if held is None:
            session.add(
                Holding(
                    household_id=account.household_id,
                    account_id=account.id,
                    security_id=security.id,
                    quantity=quantity,
                    cost_basis=basis,
                    as_of=on,
                    source=SYNCED,
                )
            )
        else:
            held.quantity = quantity
            held.cost_basis = basis
            held.as_of = on
        kept.add(security.id)
        outcome.written += line.lines

    gone = [
        h.id for sid, h in existing.items() if h.source == SYNCED and sid not in kept
    ]
    if gone:
        await session.execute(delete(Holding).where(Holding.id.in_(gone)))
        outcome.removed = len(gone)
    await session.flush()
    return outcome


__all__ = ["HoldingsOutcome", "sync_holdings"]
