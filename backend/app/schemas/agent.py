"""Schemas for agent access (ADR-0048): token administration, and the shapes the
``/agent`` and ``/anon_debug`` routes add of their own.

Every field of every schema here that an agent can receive has a policy in
``app/agent/policies.py`` — the token-admin schemas are only ever returned to a
logged-in administrator, never to an agent, and are not in the registry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.investments import HoldingOut
from app.schemas.ledger import AccountOut, CategoryOut
from app.schemas.rules import RuleActions, RuleConditions
from app.schemas.transactions import TransactionOut
from app.schemas.transfers import TransferDetailOut

Scope = Literal[
    "agent:read",
    "debug:read",
    "transactions:write",
    "holdings:write",
    "accounts:write",
    "documents:read",
]

# ---- Token administration (session-authenticated, admin or owner) ------------


class AgentTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Scope] = Field(min_length=1)
    #: Days until the token stops working. There is no "never": a forgotten token
    #: that still works is the one that leaks.
    expires_in_days: int = Field(default=90, ge=1, le=365)


class AgentTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: str
    status: Literal["active", "expired", "revoked"]


class AgentTokenCreated(AgentTokenOut):
    #: The token itself. Returned once, here, and never again: the server keeps
    #: only its hash.
    token: str


# ---- Writes (ADR-0061) --------------------------------------------------------
#
# The agent's own request shapes, deliberately not the browser's. A pseudonym is
# keyed on the *value* it replaces, so any name or description an agent could
# submit and read back would be a dictionary: post "Safeway", compare the
# pseudonym with the real rows'. These schemas therefore carry ids, numbers and
# dates only, plus one note that is stored behind a fixed prefix and so can never
# equal anything the household wrote. Unknown fields are refused, not ignored.

#: What the stored note starts with. Also the visible mark, in the app, that a
#: row was entered by an agent rather than by a person.
AGENT_NOTE_PREFIX = "Added by agent"

_EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)


def _bounded_day(value: date) -> date:
    if not _EARLIEST.date() <= value <= (datetime.now(UTC) + timedelta(days=366)).date():
        raise ValueError("date must be between 1970 and one year from now")
    return value


class AgentTransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    amount: Decimal = Field(
        max_digits=19,
        decimal_places=4,
        description=(
            "Signed, in the account's own currency: negative is money out (an "
            "expense), positive is money in. Send it as a string."
        ),
    )
    transacted_at: AwareDatetime = Field(
        description="When it happened, with a UTC offset (e.g. 2026-10-01T12:00:00Z)."
    )
    posted_at: AwareDatetime | None = None
    category_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    is_pending: bool = False
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    note: str | None = Field(
        default=None,
        max_length=500,
        description=(
            f"Optional. Stored as '{AGENT_NOTE_PREFIX}: <note>' and shown to the "
            "household. Descriptions and merchants cannot be set by an agent."
        ),
    )

    @field_validator("amount")
    @classmethod
    def _nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("amount cannot be zero")
        return value

    @field_validator("transacted_at", "posted_at")
    @classmethod
    def _in_window(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _bounded_day(value.astimezone(UTC).date())
        return value

    def stored_note(self) -> str:
        note = (self.note or "").strip()
        return f"{AGENT_NOTE_PREFIX}: {note}" if note else AGENT_NOTE_PREFIX


class AgentHoldingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    security_id: uuid.UUID = Field(description="An existing security's id; agents cannot add one.")
    quantity: Decimal = Field(
        max_digits=19, decimal_places=8, description="Units held; negative for a short position."
    )
    cost_basis: Decimal | None = Field(
        default=None,
        max_digits=19,
        decimal_places=4,
        description="Total cost of the whole position, in the security's quote currency.",
    )
    as_of: date | None = Field(default=None, description="The day the quantity was confirmed.")
    market_value: Decimal | None = Field(
        default=None,
        max_digits=19,
        decimal_places=4,
        description=(
            "Optional total value of the whole position in the security's quote "
            "currency (not the account's). Same sign as quantity."
        ),
    )

    @field_validator("as_of")
    @classmethod
    def _in_window(cls, value: date | None) -> date | None:
        return value if value is None else _bounded_day(value)

    @model_validator(mode="after")
    def _signs(self) -> AgentHoldingCreate:
        if self.quantity == 0:
            raise ValueError("quantity cannot be zero; a closed position has no row")
        if self.market_value and (self.market_value < 0) != (self.quantity < 0):
            raise ValueError("market_value must have the same sign as quantity")
        return self


#: The subtypes an agent may give: the ones the anonymized mirror shows as they
#: are. Anything else would be free text by another name.
AgentAccountSubtype = Literal[
    "checking", "savings", "money_market", "cd", "credit_card", "line_of_credit",
    "brokerage", "ira", "roth", "roth_ira", "401k", "403b", "457", "529", "hsa",
    "pension", "mortgage", "auto", "student", "personal", "heloc", "cash", "crypto",
    "other",
]


class AgentAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["depository", "credit", "investment", "loan", "other"]
    currency: str = Field(pattern="^[A-Za-z]{3}$", description="ISO 4217 code, e.g. USD.")
    subtype: AgentAccountSubtype | None = None
    current_balance: Decimal | None = Field(
        default=None,
        max_digits=19,
        decimal_places=4,
        description=(
            "Optional opening balance, signed, in the account's currency: what is "
            "owed on a card or a loan is negative. Omit it for an investment account, "
            "whose value is the sum of its holdings."
        ),
    )
    balance_date: date | None = Field(
        default=None, description="The day the balance was true. Defaults to today."
    )
    owner_id: uuid.UUID | None = Field(
        default=None, description="An existing owner's id. Omitted means the Shared owner."
    )
    label: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description=(
            f"Optional. The account is named '{AGENT_NOTE_PREFIX}: <label>' (or just "
            f"'{AGENT_NOTE_PREFIX}') until a person renames it. An agent cannot set the "
            "name itself or the institution."
        ),
    )

    @field_validator("balance_date")
    @classmethod
    def _in_window(cls, value: date | None) -> date | None:
        return value if value is None else _bounded_day(value)

    @model_validator(mode="after")
    def _balance(self) -> AgentAccountCreate:
        if self.balance_date is not None and self.current_balance is None:
            raise ValueError("balance_date needs current_balance")
        if self.type == "investment" and self.current_balance is not None:
            raise ValueError("an investment account's value comes from its holdings")
        return self

    def stored_name(self) -> str:
        label = " ".join((self.label or "").split())
        return f"{AGENT_NOTE_PREFIX}: {label}" if label else AGENT_NOTE_PREFIX


class AgentDocumentOut(BaseModel):
    """One account file, as it is: this route is not anonymized (ADR-0062)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    #: The path that returns the file's bytes.
    content: str = ""


# ---- Discovery ---------------------------------------------------------------


class ParamOut(BaseModel):
    name: str
    location: Literal["path", "query"]
    required: bool
    type: str
    description: str | None = None
    enum: list[str] | None = None


class RouteOut(BaseModel):
    """One route an agent can call, described well enough to call it."""

    name: str
    method: Literal["GET"]
    path: str
    #: A URL an agent can call as is: ``path`` when it needs no input, with today
    #: filled in for a required date; ``None`` when it needs an id from elsewhere.
    href: str | None
    description: str
    params: list[ParamOut]
    #: The JSON-schema name of the response body, as in ``/openapi.json``.
    returns: str | None = None


class CatalogOut(BaseModel):
    name: str
    version: str
    description: str
    anonymization: list[str]
    auth: str
    routes: list[RouteOut]
    links: dict[str, str]


# ---- Debug: explain a transaction --------------------------------------------


class RuleTraceOut(BaseModel):
    """One rule, evaluated against the transaction by the engine's own evaluator."""

    rule_id: uuid.UUID
    name: str
    priority: int
    enabled: bool
    order: int
    #: Every set condition and whether it holds. The rule matches iff all do.
    conditions: dict[str, bool]
    matched: bool
    #: The rule's conditions and actions, anonymized like the rule list.
    rule_conditions: RuleConditions
    rule_actions: RuleActions
    #: Provenance fields this rule would write, and which of them a human's edit
    #: (``field_sources[f] == "user"``) blocks.
    writes: list[str]
    blocked_by_user: list[str]


class OwnerResolutionOut(BaseModel):
    effective_owner_id: uuid.UUID
    #: Which link of split → transaction → account → Shared decided it (ADR-0026).
    decided_by: Literal["transaction", "account"]
    transaction_owner_id: uuid.UUID | None
    account_owner_id: uuid.UUID


class TransactionExplainOut(BaseModel):
    transaction: TransactionOut
    account: AccountOut
    category: CategoryOut | None
    owner: OwnerResolutionOut
    #: ``field_sources`` — who set each field: provider, rule or user (ADR-0007).
    provenance: dict[str, Any]
    rules: list[RuleTraceOut]
    #: Every enabled rule that matches, in engine order. They all apply, in this
    #: order, so for a field two of them write the later one wins (ADR-0007).
    matched_rule_ids: list[uuid.UUID]
    transfer: TransferDetailOut | None
    notes: list[str]


# ---- Debug: explain an account's balance -------------------------------------


class SnapshotOut(BaseModel):
    balance_date: date
    balance: Decimal
    currency: str


class BalanceExplainOut(BaseModel):
    account: AccountOut
    balance_source: str | None
    connection_id: uuid.UUID | None
    connection_status: str | None
    first_snapshot_date: date | None
    last_snapshot_date: date | None
    snapshot_count: int
    #: The most recent snapshots, newest first (at most 90).
    snapshots: list[SnapshotOut]
    transaction_count: int
    earliest_transaction_date: date | None
    latest_transaction_date: date | None
    #: Σ amounts of transactions dated after the last snapshot — what the balance
    #: should have moved by since (ADR-0043: a balance moves by its transactions).
    moved_since_last_snapshot: Decimal
    #: ``current_balance`` equals the last snapshot's balance.
    current_matches_last_snapshot: bool | None
    holdings: list[HoldingOut]
    #: Data checks (``/checks``) whose findings name this account.
    flagged_by_checks: list[str]
    notes: list[str]


# ---- Debug: the system -------------------------------------------------------


class CurrencyCoverageOut(BaseModel):
    currency: str
    accounts: int
    latest_rate_date: date | None


class ConnectionHealthOut(BaseModel):
    connection_id: uuid.UUID
    status: str
    is_enabled: bool
    last_synced_at: datetime | None
    last_run_status: str | None
    last_run_started_at: datetime | None
    last_error: str | None


class SystemOut(BaseModel):
    app_version: str
    schema_version: str | None
    env: str
    server_time: datetime
    household_id: uuid.UUID
    base_currency: str
    timezone: str
    settings: dict[str, Any]
    counts: dict[str, int]
    currencies: list[CurrencyCoverageOut]
    connections: list[ConnectionHealthOut]
    active_jobs: int
    last_job_heartbeat_at: datetime | None
    checks: dict[str, str]


# ---- Debug: a page as the user sees it ---------------------------------------


class PageCallOut(BaseModel):
    """One request the page makes, and its anonymized response."""

    path: str
    status: int
    #: The anonymized body — the same shape the page receives.
    body: Any


class PageOut(BaseModel):
    page: str
    description: str
    params: dict[str, str]
    calls: list[PageCallOut]
