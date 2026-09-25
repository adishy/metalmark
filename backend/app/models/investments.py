"""First-class investments (ADR-0011): securities, holdings, prices, and
investment transactions. All household-scoped (RLS).

**Sign conventions — the load-bearing part of this file.** Every quantity and
amount here is *signed*, so that a position or a cash balance is a plain ``SUM``
and no reader has to know the direction rules to compute one:

    buy     amount −   quantity +      money leaves the cash holding, shares arrive
    sell    amount +   quantity −
    dividend/interest  amount +        income; enters cash flow (ADR-0032 §2)
    fee                amount −        expense; enters cash flow (ADR-0032 §2)
    transfer           amount ±        a contribution (+) or withdrawal (−)
    split              amount 0        quantity = the share *delta*, no cash effect

``amount`` is the cash effect **on the account, in the account's currency** —
the same convention ``transactions.amount`` uses (+ = money in). A buy is
therefore negative, which reads oddly for one line and makes
``SUM(amount)`` mean "the change in this account's cash" everywhere else.

**A buy is not a transaction** (ADR-0033). Investment accounts have no
``transactions`` rows at all — their balance is Σ(holding market values) per
ADR-0011, so a cash-ledger row there would contribute to nothing and
double-count against the holdings it bought. Their history is this table.

Money is ``NUMERIC(19,4)``/``Decimal`` (ADR-0005). Quantities and prices are
``NUMERIC(19,8)``: a price is not money, and ``$0.00000412`` is a real quote
that four decimals would round to zero.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.ledger import MONEY

#: A quantity or a price. Eight decimals, for the reason above.
QTY = Numeric(19, 8)

#: Who wrote a ``holdings`` row. ``simplefin`` rows are the bank's report of the
#: position and are rewritten or removed by every sync (ADR-0051); ``manual`` rows
#: are a human's and sync never touches them.
HOLDING_SOURCES = ("manual", "simplefin")

SECURITY_TYPES = ("stock", "etf", "mutual_fund", "bond", "option", "crypto", "cash", "other")
INVESTMENT_TX_TYPES = ("buy", "sell", "dividend", "interest", "fee", "split", "transfer")

#: The one security type that is money rather than an instrument. ADR-0033 §4:
#: uninvested cash inside a *derived* account is a real holding, which is what
#: makes "a buy does not move the account balance" true.
CASH_SECURITY_TYPE = "cash"

#: The event types whose entire effect on a **cash** position is to move currency
#: into or out of it — ADR-0033 §4's table, minus the trade rows, which are the
#: same events seen from the securities side. `fold_history` is the only reader.
CASH_MOVING_EVENT_TYPES = ("dividend", "interest", "fee", "transfer")


class Security(UUIDPkMixin, TimestampMixin, Base):
    """A reusable instrument ("VTI", "USD cash", "AAPL"), shared by the
    household's accounts so the allocation view can aggregate one row per
    instrument across all of them (ADR-0011).

    ``currency`` is the currency the security is *quoted* in, which is why it is
    part of the ticker's uniqueness: the same symbol on two exchanges is two
    instruments, and summing their market values without that distinction would
    silently add GBP to USD.
    """

    __tablename__ = "securities"
    __table_args__ = (
        CheckConstraint(
            "security_type IN (" + ", ".join(f"'{t}'" for t in SECURITY_TYPES) + ")",
            name="security_type_valid",
        ),
        # Cash has no ticker and there may be several (per currency), so the
        # ticker is the identity only where one exists.
        Index(
            "uq_securities_household_ticker",
            "household_id",
            "ticker",
            "currency",
            unique=True,
            postgresql_where=text("ticker IS NOT NULL"),
        ),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False, default="stock")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Mirrors `accounts.is_manual`: a hand-entered instrument that no provider
    # vouches for, which is the position ADR-0010 requires to be first-class.
    is_manual: Mapped[bool] = mapped_column(nullable=False, default=True)

    prices: Mapped[list[SecurityPrice]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )


class Holding(UUIDPkMixin, TimestampMixin, Base):
    """A position: one security, in one account (ADR-0011).

    **Market value is not stored.** It is ``quantity × latest price on or before
    the point's date``, converted to base — derived, because a stored market
    value is a second copy of a fact that changes when a price changes, and the
    two would drift on the day someone forgets to recompute it. ADR-0032 §5 also
    needs the price *series*, not one number, so the price is the thing worth
    keeping.

    ``cost_basis`` is the **total** basis for the whole position, in the
    account's currency — not per share. Total is the safer of the two: it is what
    ``investment_transactions`` derive by summing (ADR-0020), it is what
    ADR-0032's appreciation term wants, and it stays meaningful at
    ``quantity = 0`` where a per-share figure is a division by zero. Average
    cost per share is ``cost_basis / quantity``, computed by the caller that
    actually wants a per-share number.

    Under ADR-0020 ``cost_basis`` here is authoritative **only** when no
    ``investment_transactions`` exist for the (security, account); otherwise the
    history is, and a write to this column is refused rather than ignored.

    **A ``stated`` account's "unaccounted cash" plug (ADR-0021) is not a row
    here.** It is ``stated balance − Σ(other holdings)``, computed at read time.
    Storing it would make it a second writer of a derived fact that every price
    and every stated balance can invalidate — precisely the drift ADR-0021 exists
    to prevent. The distinction against ADR-0033 §4 is worth keeping straight: an
    uninvested cash position in a *derived* account is money the household
    actually holds and *is* a row; the plug is an arithmetic remainder and is not.
    """

    __tablename__ = "holdings"
    __table_args__ = (
        # One position per security per account. This is what makes Σ(holdings)
        # well-defined and lets an importer upsert a position instead of
        # appending a second one that would double the account's value.
        UniqueConstraint("account_id", "security_id"),
        CheckConstraint("quantity <> 0", name="quantity_nonzero"),
        CheckConstraint(
            "source IN (" + ", ".join(f"'{s}'" for s in HOLDING_SOURCES) + ")",
            name="source_valid",
        ),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    # Signed: a short position is negative, and then Σ(quantity × price) stays
    # right without a special case. ``<> 0`` because a zero-quantity position is
    # "closed", and a closed position is the absence of a row — keeping one would
    # make the allocation view render a 0% line forever.
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    cost_basis: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # When a human last confirmed this position. Prices carry their own age
    # (ADR-0032 §5); this is the other half of staleness — a holding nobody has
    # touched in a year is worth flagging even if its price is current.
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ``simplefin`` when sync wrote the row from the bank's holdings (ADR-0051).
    # Such a row is the provider's statement, replaced on each sync and removed
    # when the bank stops reporting it, so a hand edit to it would not survive.
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual", server_default="manual"
    )

    security: Mapped[Security] = relationship()


class SecurityPrice(UUIDPkMixin, TimestampMixin, Base):
    """The price series — one price per security per day.

    The uniqueness is the point: "the latest price on or before date D"
    (ADR-0032 §5) is only well-defined if there is exactly one candidate, and a
    second price for the same day would make net worth depend on insertion order.

    Prices are manually entered in v1 (ADR-0011's deferral); ``source`` exists so
    an automatic fetcher can add rows without a migration to distinguish them.
    """

    __tablename__ = "security_prices"
    __table_args__ = (
        UniqueConstraint("security_id", "price_date"),
        CheckConstraint("source IN ('auto','manual')", name="source_valid"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    # The security's quote currency, denormalized so a price read for a net-worth
    # series does not need the security row to know how to convert it.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")

    security: Mapped[Security] = relationship(back_populates="prices")


class InvestmentTransaction(UUIDPkMixin, TimestampMixin, Base):
    """One investment event (ADR-0011). The investment analogue of
    ``transactions``, and deliberately not the same table (ADR-0033).

    ``security_id`` is **nullable**: an advisory fee, an account-level interest
    credit and a cash contribution have no instrument, and forcing one would mean
    inventing a security to satisfy a column.

    ``transfer_group_id`` is how money crosses between the cash world and the
    investment world (ADR-0033 §3): a contribution is one ``transfer_groups`` row
    with a ``transactions`` leg on the funding account and an
    ``investment_transactions`` leg here. Sharing the group — rather than a new
    link table — is what makes ADR-0008's exclusion apply unchanged, so funding a
    brokerage account stops reading as an expense.

    ``external_id`` / ``import_hash`` / ``field_sources`` exist because an
    importer will populate this table: OFX investment rows are counted and
    skipped today (ADR-0030 §5) and wiring them here is the stated follow-up.
    They mirror ``transactions`` exactly, including the two partial unique
    indexes, so the dedupe rules are the same ones already tested.

    Deliberately absent: ``is_pending`` and ``review_status``. Nothing in v1
    produces a pending investment event, and a trade does not need categorizing —
    it needs a security and a quantity, which are not reviewable fields. Adding
    either later is one column; modelling a workflow no writer uses is a reader's
    permanent tax.
    """

    __tablename__ = "investment_transactions"
    __table_args__ = (
        CheckConstraint(
            "type IN (" + ", ".join(f"'{t}'" for t in INVESTMENT_TX_TYPES) + ")",
            name="type_valid",
        ),
        CheckConstraint("source IN ('simplefin','csv','ofx','manual')", name="source_valid"),
        Index(
            "ix_investment_transactions_household_account_trade",
            "household_id", "account_id", "trade_date",
        ),
        # The same dedupe pair as transactions, with the same partiality rules.
        Index(
            "uq_investment_transactions_account_external_id",
            "account_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
        Index(
            "uq_investment_transactions_account_import_hash",
            "account_id",
            "import_hash",
            unique=True,
            postgresql_where="external_id IS NULL AND import_hash IS NOT NULL",
        ),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("securities.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(10), nullable=False)

    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    import_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # A trade happens on a day; a settlement datetime would imply a precision
    # nothing in v1 supplies.
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Signed — see the module docstring. Both are nullable because a dividend or
    # a fee has cash and no shares, and a share split has shares and no cash.
    # A `split` row carries the share *delta* (a 2-for-1 split of 10 shares is
    # +10), not the 2:1 ratio: every other quantity in this table is a signed
    # delta, so a ratio here would be the one row whose sign and scale both mean
    # something different, and `Σ(quantity)` — the sum ADR-0034 derives a position
    # from — would be wrong for it.
    quantity: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(QTY, nullable=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ≡ account.currency

    # FX cache, exactly as on `transactions` (ADR-0017): base_amount is a cache
    # recomputed on rate change and never the source of truth.
    base_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    fx_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfer_groups.id", ondelete="SET NULL"), nullable=True
    )
    field_sources: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
