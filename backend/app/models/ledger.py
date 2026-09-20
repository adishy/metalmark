"""The manual ledger: accounts, categories, transactions, splits, transfers,
tags, balance snapshots (ARCHITECTURE §2). All household-scoped (RLS).

Money is NUMERIC(19,4)/Decimal. A transaction's ``currency`` ≡ its account's
currency (single-currency accounts, ADR-0017). ``base_amount`` is a cache of
``amount`` converted to the household base currency (recomputed on FX change).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
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

MONEY = Numeric(19, 4)


class AccountConnection(UUIDPkMixin, TimestampMixin, Base):
    """One per SimpleFIN claim (Phase 2). Present now so accounts can carry the
    nullable, ON DELETE SET NULL FK that decouples the ledger from sync
    (ARCHITECTURE §2 'Connection ↔ ledger decoupling')."""

    __tablename__ = "account_connections"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="simplefin")
    access_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Account(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # Stable identity used by reconnect remap (§3) — required for correctness.
        UniqueConstraint("household_id", "external_key"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    # Nullable + ON DELETE SET NULL: deleting a connection preserves history.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("account_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # depository|credit|investment|loan|other
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # fixed, single-currency
    current_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    available_balance: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    balance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # investment accounts only: derived (Σ holdings) | stated (ADR-0021)
    balance_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_asset: Mapped[bool] = mapped_column(Boolean, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CategoryGroup(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "category_groups"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # SINGLE source of truth for income|expense|transfer (§2).
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    sort: Mapped[int] = mapped_column(nullable=False, default=0)


class Category(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "categories"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("category_groups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rollover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort: Mapped[int] = mapped_column(nullable=False, default=0)


class Tag(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("household_id", "name"),)

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)


class TransferGroup(UUIDPkMixin, TimestampMixin, Base):
    """Links the legs of a money movement; excluded from cash-flow (ADR-0008)."""

    __tablename__ = "transfer_groups"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    # auto|manual
    matched_by: Mapped[str] = mapped_column(String(8), nullable=False, default="manual")
    # Residual base-currency FX cost of a cross-currency transfer (ADR-0018).
    fx_cost_base: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


class Transaction(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index(
            "ix_transactions_household_account_posted",
            "household_id", "account_id", "posted_at",
        ),
        # dedupe: provider rows by external_id; manual/import rows by import_hash.
        Index(
            "uq_transactions_account_external_id",
            "account_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
        Index(
            "uq_transactions_account_import_hash",
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
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    import_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)  # + = money in
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ≡ account.currency
    base_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # cache
    fx_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(300), nullable=True)

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Transaction-level owner. Attribution precedence: split.owner → transaction
    # owner → account.owner → joint (null). Fractional per-txn ownership deferred.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="needs_review")
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_split_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfer_groups.id", ondelete="SET NULL"), nullable=True
    )
    # provenance: {field: user|rule|provider} — user > rule > provider (ADR-0007/0019)
    field_sources: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # simplefin|csv|ofx|manual
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")

    splits: Mapped[list[TransactionSplit]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class TransactionSplit(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "transaction_splits"

    parent_txn_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    base_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    parent: Mapped[Transaction] = relationship(back_populates="splits")


class TransactionTag(Base):
    __tablename__ = "transaction_tags"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "balance_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    balance_date: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
