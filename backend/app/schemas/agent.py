"""Schemas for agent access (ADR-0048): token administration, and the shapes the
``/agent`` and ``/anon_debug`` routes add of their own.

Every field of every schema here that an agent can receive has a policy in
``app/agent/policies.py`` — the token-admin schemas are only ever returned to a
logged-in administrator, never to an agent, and are not in the registry.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.investments import HoldingOut
from app.schemas.ledger import AccountOut, CategoryOut
from app.schemas.rules import RuleActions, RuleConditions
from app.schemas.transactions import TransactionOut
from app.schemas.transfers import TransferDetailOut

Scope = Literal["agent:read", "debug:read"]

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
