"""Investments: securities, prices, holdings, investment transactions, and the
valuation/allocation reads (ADR-0011/0020/0032/0033/0034).

Two things here are not the obvious shape:

**Holdings report an effective quantity and basis, plus the source of each**
(ADR-0034). ``quantity`` is what the household actually holds — derived from
``investment_transactions`` when any exist, the stored scalar otherwise — and
``quantity_source`` says which. ``manual_quantity`` carries the stored scalar
either way, so a UI can show "you entered 10; three trades say 0" instead of
silently ignoring an entry the household can see in the database.

**Money and quantities use different scales.** Amounts are ``Decimal`` at
``NUMERIC(19,4)``; quantities and prices are ``NUMERIC(19,8)`` because a price of
``$0.00000412`` is a real quote that four decimals round to zero.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.investments import INVESTMENT_TX_TYPES, SECURITY_TYPES
from app.schemas.patch import is_set

#: ``group_by`` values the allocation endpoint accepts.
ALLOCATION_GROUPS = ("security", "type", "account", "currency")

_SECURITY_TYPE_PATTERN = "^(" + "|".join(SECURITY_TYPES) + ")$"
_TX_TYPE_PATTERN = "^(" + "|".join(INVESTMENT_TX_TYPES) + ")$"
_GROUP_PATTERN = "^(" + "|".join(ALLOCATION_GROUPS) + ")$"


# ---- securities -------------------------------------------------------------


class SecurityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    security_type: str = Field(default="stock", pattern=_SECURITY_TYPE_PATTERN)
    # The currency the security is *quoted* in. Three characters, and required:
    # a security without a quote currency cannot be valued against anything.
    currency: str = Field(min_length=3, max_length=3)


class SecurityUpdate(BaseModel):
    """Send only what changes.

    **Currency is absent deliberately.** It is half of the instrument's identity —
    the same ticker on two exchanges is two securities — so changing it would
    silently restate every price already recorded against this one. A security
    quoted in the wrong currency is a delete and a re-create.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    security_type: str | None = Field(default=None, pattern=_SECURITY_TYPE_PATTERN)

    @model_validator(mode="after")
    def _name_not_clearable(self) -> SecurityUpdate:
        if is_set(self, "name") and self.name is None:
            raise ValueError("name cannot be null")
        return self


class SecurityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    ticker: str | None
    security_type: str
    currency: str
    is_manual: bool


# ---- prices -----------------------------------------------------------------


class PriceUpsert(BaseModel):
    price_date: date
    # Explicitly not nullable: zero is a real price for a written-off position,
    # and "no price" is the absence of a row, not a row holding null.
    price: Decimal = Field(ge=0)


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    security_id: uuid.UUID
    price_date: date
    price: Decimal
    currency: str
    source: str


# ---- holdings ---------------------------------------------------------------


class HoldingCreate(BaseModel):
    account_id: uuid.UUID
    security_id: uuid.UUID
    quantity: Decimal
    cost_basis: Decimal | None = None
    as_of: date | None = None

    @model_validator(mode="after")
    def _quantity_nonzero(self) -> HoldingCreate:
        # Matching the `quantity <> 0` check constraint in the database. A zero
        # position is closed, and a closed position is the absence of a row.
        if self.quantity == 0:
            raise ValueError("quantity cannot be zero; a closed position has no row")
        return self


class HoldingUpdate(BaseModel):
    quantity: Decimal | None = None
    cost_basis: Decimal | None = None
    as_of: date | None = None

    @model_validator(mode="after")
    def _quantity_nonzero(self) -> HoldingUpdate:
        if is_set(self, "quantity") and self.quantity == 0:
            raise ValueError("quantity cannot be zero; a closed position has no row")
        return self


class HoldingOut(BaseModel):
    #: The ``holdings`` row. **Null for a position that exists only as recorded
    #: trades** — ADR-0034 makes the trades the position, so a buy recorded
    #: against a security this account has never held is a real position with no
    #: row behind it. `account_id` and `security_id` identify those.
    id: uuid.UUID | None
    account_id: uuid.UUID
    security_id: uuid.UUID
    security: SecurityOut

    #: The effective position. See the module docstring.
    quantity: Decimal
    cost_basis: Decimal | None
    #: ``history`` when ``investment_transactions`` exist for this (account,
    #: security) and are therefore authoritative; ``provider`` when sync wrote the
    #: row from the bank's holdings (ADR-0051); ``manual`` otherwise.
    quantity_source: str
    basis_source: str

    #: The manually-entered scalar, whether or not it is the one in force, so the
    #: UI can say what a household typed when history overrode it. Null when there
    #: is no row — nobody typed anything.
    manual_quantity: Decimal | None
    manual_cost_basis: Decimal | None

    as_of: date | None


# ---- investment transactions ------------------------------------------------


class InvestmentTransactionCreate(BaseModel):
    account_id: uuid.UUID
    #: Nullable: an advisory fee, an account-level interest credit and a cash
    #: contribution have no instrument, and forcing one would mean inventing a
    #: security to satisfy a column.
    security_id: uuid.UUID | None = None
    type: str = Field(pattern=_TX_TYPE_PATTERN)
    trade_date: date
    quantity: Decimal | None = None
    price: Decimal | None = None
    #: The **cash effect on the account, in the account's currency**: positive for
    #: money in, negative for money out. A buy is therefore negative (ADR-0033).
    amount: Decimal
    description: str | None = None
    notes: str | None = None
    #: ADR-0033 §3: money crossing between the cash world and the investment world
    #: is one `transfer_groups` row whose legs are a `transactions` row on the
    #: funding account and this row. Setting the same group on both is what makes
    #: ADR-0008's exclusion apply unchanged, so a contribution stops reading as an
    #: expense while still landing in the account's cash position (ADR-0033 §4).
    transfer_group_id: uuid.UUID | None = None


class InvestmentTransactionUpdate(BaseModel):
    type: str | None = Field(default=None, pattern=_TX_TYPE_PATTERN)
    trade_date: date | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    description: str | None = None
    notes: str | None = None
    transfer_group_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _required_fields_not_clearable(self) -> InvestmentTransactionUpdate:
        # `amount` and `trade_date` are NOT NULL: an explicit null is a client
        # bug, and saying so beats the IntegrityError that would answer it.
        for field in ("amount", "trade_date", "type"):
            if is_set(self, field) and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class InvestmentTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    security_id: uuid.UUID | None
    type: str
    trade_date: date
    quantity: Decimal | None
    price: Decimal | None
    amount: Decimal
    currency: str
    description: str | None
    notes: str | None
    source: str
    transfer_group_id: uuid.UUID | None


# ---- valuation and allocation ----------------------------------------------


class HoldingValueOut(BaseModel):
    """One position, valued. ``value_base`` is null **iff** ``reason`` is set.

    Unpriced is not zero: an unpriced position contributes nothing to a total but
    is reported here, because "we cannot value this" and "this is worth nothing"
    must not render identically (ADR-0032 §5).
    """

    #: Null for a position that exists only as recorded trades (ADR-0034).
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
    #: ``quantity × price``, in the security's own quote currency.
    value_native: Decimal | None
    value_account: Decimal | None
    value_base: Decimal | None
    #: Days between the valuation date and the price used. Null when there is no
    #: price at all, which is a stronger statement than "very stale".
    stale_days: int | None
    #: ``no_price`` | ``no_rate`` | null.
    reason: str | None


class AccountValuationOut(BaseModel):
    account_id: uuid.UUID
    name: str
    currency: str
    balance_source: str
    #: ``derived``: Σ(holdings). ``stated``: the account's own balance, with any
    #: remainder over the holdings reported as ``unaccounted_cash`` (ADR-0021).
    balance_account: Decimal
    market_value_account: Decimal
    market_value_base: Decimal
    #: The ``stated`` balance in base — what this account contributes to
    #: ``total_base``. ``None`` for a ``derived`` account (Σ(holdings) is the
    #: balance) and for a ``stated`` one with no rate to convert its balance:
    #: in that second case the account contributes nothing to the total, and
    #: ``unaccounted_cash_base`` is ``0``, so this field is the only thing that
    #: tells the two apart. A consumer must not render ``$0.00`` for a null.
    stated_balance_base: Decimal | None = None
    unaccounted_cash_base: Decimal
    holdings: list[HoldingValueOut]
    #: Counts. The positions themselves are in ``holdings`` with ``reason`` set.
    unpriced: int
    no_rate: int
    oldest_price_date: date | None
    max_stale_days: int | None
    is_fully_valued: bool


class PortfolioOut(BaseModel):
    as_of: date
    base_currency: str
    total_base: Decimal
    accounts: list[AccountValuationOut]


class AllocationSourceOut(BaseModel):
    """One account's contribution to an allocation row (ADR-0054) — what lets the
    UI answer "which accounts is this made of" for a row that can be one holding,
    several accounts' cash, or both."""

    account_id: uuid.UUID
    account_name: str
    institution: str | None = None
    value_base: Decimal
    share_of_group: Decimal
    #: Set only on a ``group_by=security`` row, where a source is exactly one
    #: account's position in that one security — under any other grouping an
    #: account can contribute through several holdings at different prices, and
    #: "the" quantity or price of the row would be a made-up number.
    quantity: Decimal | None = None
    price: Decimal | None = None
    price_currency: str | None = None
    price_date: date | None = None


class AllocationRowOut(BaseModel):
    key: str
    label: str
    value_base: Decimal
    percent: Decimal
    #: How many positions make up this row. A 3% line that is one holding and a 3%
    #: line that is thirty read very differently.
    holdings: int
    sources: list[AllocationSourceOut] = []


class AllocationOut(BaseModel):
    as_of: date
    base_currency: str
    group_by: str
    #: Echoes the request (ADR-0054): a stored link or a screenshot has to say
    #: whether bank cash is in the total it shows, not just the total itself.
    include_cash_accounts: bool
    total_base: Decimal
    rows: list[AllocationRowOut]

    #: Counts, not lists. The rows cannot show a position with no price, and these
    #: are what stop that from being invisible — a total quietly missing 30% of the
    #: portfolio is the failure ADR-0032 §5 exists to prevent. Two distinct counts
    #: because the two problems have different fixes (enter a price vs enter a rate),
    #: and the portfolio endpoint's per-holding ``reason`` names which rows.
    unpriced_positions: int
    no_rate_positions: int
    max_stale_days: int | None
