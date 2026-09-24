"""Which policy every agent-visible field gets (ADR-0048).

One entry per ``(schema, field)``. The walker drops any field without an entry, and
``tests/unit/test_agent_policies.py`` fails on it — so a new field is invisible to
agents until someone decides here what it is. The policies are in
``anonymize.py``; the short version:

* ``Keep`` — ids, amounts, dates, counts, flags. Never a string (enforced).
* ``Pseudonym(kind)`` — names and every piece of free text a person or a bank
  wrote: the whole value becomes ``Kind abc123``.
* ``Label(kind)`` — a display label: generic words kept, anything else a pseudonym.
* ``Code(...)`` — a value the app writes from a fixed vocabulary.
* ``Text`` / ``ExternalText`` — sentences the app (or a bank) composed, with known
  names substituted and number shapes masked.
* ``Drop`` — never shown.

When a field is sensitive in a way this list does not name, choose the stricter
policy. The cost of a pseudonym where a name would have been harmless is an agent
that has to ask; the cost of the reverse is a leak.
"""

from __future__ import annotations

import re

from app.agent.anonymize import (
    Code,
    Color,
    Currency,
    Drop,
    ExternalText,
    Icon,
    JsonTree,
    Keep,
    Key,
    Label,
    Nested,
    Pattern,
    Policy,
    Pseudonym,
    Text,
    Ticker,
    Timezone,
)
from app.schemas import agent as ag
from app.schemas import auth as au
from app.schemas import checks as ch
from app.schemas import connections as co
from app.schemas import household as hh
from app.schemas import institutions as ins
from app.schemas import investments as inv
from app.schemas import ledger as le
from app.schemas import owners as ow
from app.schemas import reports as rp
from app.schemas import rules as ru
from app.schemas import transactions as tx
from app.schemas import transfers as tr

K = Keep()
N = Nested()
D = Drop()
CUR = Currency()
TEXT = Text()
EXT = ExternalText()

ACCOUNT_TYPES = Code("depository", "credit", "investment", "loan", "other")
#: Subtypes come from a person or a file (OFX ``ACCTTYPE``) as well as the app, so
#: only the common vocabulary survives; anything else is a pseudonym.
ACCOUNT_SUBTYPES = Code(
    "checking",
    "savings",
    "money_market",
    "cd",
    "credit_card",
    "line_of_credit",
    "brokerage",
    "ira",
    "roth",
    "roth_ira",
    "401k",
    "403b",
    "457",
    "529",
    "hsa",
    "pension",
    "mortgage",
    "auto",
    "student",
    "personal",
    "heloc",
    "cash",
    "crypto",
    "other",
    "CHECKING",
    "SAVINGS",
    "MONEYMRKT",
    "CREDITLINE",
    "CD",
)
GRANULARITY = Code("day", "week", "month", "quarter", "year")
FIELD_SOURCES = JsonTree(values=("provider", "rule", "user", "import", "manual", "simplefin"))
#: A sync run event's ``detail`` (ARCHITECTURE §2 ``sync_run_events``). Already
#: sanitized at write time (ADR-0016) — but it names accounts (``name``) and
#: carries the provider's account key (``key``), so it is walked like any tree.
EVENT_DETAIL = JsonTree(
    codes=("kind", "status", "level", "account_type", "trigger", "event", "reason_code"),
    text=("reason",),
    external=("error", "message", "title", "body", "warning"),
)
CURSOR = Pattern(re.compile(r"[A-Za-z0-9_\-=]{1,512}"), "Cursor")
#: An alembic revision (``0008``) or a version string (``0.1.0``).
VERSION = Pattern(re.compile(r"[0-9A-Za-z][0-9A-Za-z_.+\-]{0,31}"), "Version")

REGISTRY: dict[tuple[type, str], Policy] = {}


def register(model: type, **fields: Policy) -> None:
    for name, policy in fields.items():
        if name not in model.model_fields:
            raise KeyError(f"{model.__name__} has no field {name!r}")
        REGISTRY[(model, name)] = policy


# ---- ledger -------------------------------------------------------------------

register(
    le.AccountOut,
    id=K,
    name=Pseudonym("Account"),
    type=ACCOUNT_TYPES,
    currency=CUR,
    subtype=ACCOUNT_SUBTYPES,
    institution=Pseudonym("Institution"),
    current_balance=K,
    balance_date=K,
    is_asset=K,
    owner_id=K,
    is_manual=K,
    is_hidden=K,
    stale_since=K,
)
register(
    le.NetWorthOut,
    base_currency=CUR,
    assets=K,
    liabilities=K,
    net_worth=K,
    unconverted_currencies=CUR,
    attribution=Code(),
)
register(
    le.CategoryGroupOut,
    id=K,
    name=Label("Group"),
    type=Code("income", "expense", "transfer"),
    sort=K,
)
register(
    le.CategoryOut,
    id=K,
    group_id=K,
    name=Label("Category"),
    icon=Icon(),
    color=Color(),
    sort=K,
)
register(
    ins.InstitutionOut,
    name=Pseudonym("Institution"),
    key=Pseudonym("Institution"),
    fetchable=K,
    has_logo=K,
    logo_source=Code("fetched", "uploaded"),
    logo_updated_at=K,
)
register(le.BalanceOut, balance_date=K, balance=K, currency=Currency())
register(le.TagOut, id=K, name=Label("Tag"), color=Color())
register(
    le.FxRateOut,
    id=K,
    base_currency=CUR,
    quote_currency=CUR,
    rate_date=K,
    rate=K,
    source=Code("auto", "manual"),
)

# ---- owners, household, identity ------------------------------------------------

register(ow.OwnerOut, id=K, name=Label("Owner"), kind=Code("person", "shared"), sort=K)
register(
    hh.HouseholdOut,
    id=K,
    name=Pseudonym("Household"),
    base_currency=CUR,
    timezone=Timezone(),
    role=Code("owner", "member"),
)
register(
    hh.MemberOut,
    user_id=K,
    display_name=Pseudonym("Person"),
    email=D,
    role=Code("owner", "member"),
)
# /auth/me is not exposed to agents (it is the session's own identity, and carries
# the CSRF token) — registered anyway, so a future exposure is anonymized rather
# than dropped silently, and the CSRF token can never ride along.
register(
    au.MeResponse,
    user=N,
    household_id=K,
    household_name=Pseudonym("Household"),
    base_currency=CUR,
    role=Code("owner", "member"),
    csrf_token=D,
)
register(au.UserResponse, id=K, email=D, display_name=Pseudonym("Person"), is_admin=K)

# ---- transactions -------------------------------------------------------------

register(
    tx.TransactionOut,
    id=K,
    account_id=K,
    amount=K,
    currency=CUR,
    base_amount=K,
    fx_rate_date=K,
    transacted_at=K,
    posted_at=K,
    # Option (a) of the design: both free-text fields become pseudonyms. Stable,
    # so rows sharing a merchant share a pseudonym; rule matching is answered
    # server-side by /anon_debug/transactions/{id}/explain instead.
    description=Pseudonym("Description"),
    merchant=Pseudonym("Merchant"),
    category_id=K,
    owner_id=K,
    effective_owner_id=K,
    is_pending=K,
    review_status=Code("needs_review", "reviewed", "ignored"),
    is_hidden=K,
    is_split_parent=K,
    transfer_group_id=K,
    field_sources=FIELD_SOURCES,
    notes=Pseudonym("Note"),
    source=Code("simplefin", "csv", "ofx", "manual", "import"),
    tag_ids=K,
    splits=N,
)
register(
    tx.SplitOut,
    id=K,
    amount=K,
    base_amount=K,
    category_id=K,
    owner_id=K,
    effective_owner_id=K,
    notes=Pseudonym("Note"),
)
register(tx.TransactionPage, items=N, next_cursor=CURSOR)
register(
    tr.TransferCandidatesOut,
    items=N,
)
register(
    tr.TransferCandidateOut,
    transaction=N,
    days_apart=K,
    fx_cost_base=K,
    within_tolerance=K,
)
register(
    tr.TransferDetailOut,
    transfer_group_id=K,
    matched_by=Code("auto", "manual"),
    fx_cost_base=K,
    legs=N,
)

# ---- rules ----------------------------------------------------------------------

register(
    ru.RuleOut,
    id=K,
    name=Pseudonym("Rule"),
    priority=K,
    enabled=K,
    conditions=N,
    actions=N,
    created_at=K,
)
register(
    ru.RuleConditions,
    # The matched text is the merchant or description it targets, so it gets the
    # same kind of pseudonym — but a *substring* or a regex does not hash to the
    # value it matches, so this does not tell an agent which rows match. The
    # explain route does, by running the engine.
    merchant_contains=Pseudonym("Pattern"),
    description_regex=Pseudonym("Pattern"),
    amount_min=K,
    amount_max=K,
    direction=Code("in", "out"),
    account_ids=K,
    category_id=K,
    is_pending=K,
)
register(
    ru.RuleActions,
    set_category_id=K,
    add_tag_ids=K,
    set_owner_id=K,
    rename_merchant=Pseudonym("Merchant"),
    set_hidden=K,
    mark_reviewed=K,
    split=N,
)
register(
    ru.RuleSplitLeg,
    amount=K,
    percent=K,
    remainder=K,
    category_id=K,
    owner_id=K,
    notes=Pseudonym("Note"),
)

# ---- reports --------------------------------------------------------------------

register(
    rp.NetWorthSeries,
    base_currency=CUR,
    start=K,
    end=K,
    granularity=GRANULARITY,
    points=N,
    delta_net_worth=K,
    net_cash_flow=K,
    currency_revaluation=K,
    market_appreciation=K,
    unexplained=K,
    unexplained_by_account=N,
    warnings=TEXT,
    attribution=Code(),
)
register(rp.NetWorthPoint, date=K, net_worth=K, missing=N)
register(
    rp.MissingAccount,
    account_id=K,
    name=Pseudonym("Account"),
    reason=Code("not_started", "no_balance", "no_rate", "no_price"),
)
register(rp.UnexplainedAccount, account_id=K, name=Pseudonym("Account"), amount=K)
register(
    rp.CashFlowSeries,
    base_currency=CUR,
    start=K,
    end=K,
    granularity=GRANULARITY,
    points=N,
    attribution=Code(),
)
register(rp.CashFlowPoint, date=K, income=K, expense=K, net=K)
register(
    rp.CashFlowSankey,
    base_currency=CUR,
    start=K,
    end=K,
    income=N,
    expense=N,
    total_income=K,
    total_expense=K,
    net=K,
    attribution=Code(),
    warnings=TEXT,
)
register(rp.CashFlowSankeyRow, key=Key(), label=Label("Category"), category_id=K, total=K)
register(
    rp.SpendingReport,
    base_currency=CUR,
    start=K,
    end=K,
    rows=N,
    total=K,
    attribution=Code(),
    warnings=TEXT,
)
register(rp.CategorySpendRow, key=Key(), category_id=K, category_name=Label("Category"), total=K)

# ---- investments ----------------------------------------------------------------

SECURITY_TYPES = Code("stock", "etf", "mutual_fund", "bond", "option", "crypto", "cash", "other")
register(
    inv.SecurityOut,
    id=K,
    name=Pseudonym("Security"),
    ticker=Ticker(),
    security_type=SECURITY_TYPES,
    currency=CUR,
    is_manual=K,
)
register(
    inv.PriceOut,
    id=K,
    security_id=K,
    price_date=K,
    price=K,
    currency=CUR,
    source=Code("auto", "manual"),
)
register(
    inv.HoldingOut,
    id=K,
    account_id=K,
    security_id=K,
    security=N,
    quantity=K,
    cost_basis=K,
    quantity_source=Code(),
    basis_source=Code(),
    manual_quantity=K,
    manual_cost_basis=K,
    as_of=K,
)
register(
    inv.InvestmentTransactionOut,
    id=K,
    account_id=K,
    security_id=K,
    type=Code("buy", "sell", "dividend", "interest", "fee", "split", "transfer"),
    trade_date=K,
    quantity=K,
    price=K,
    amount=K,
    currency=CUR,
    description=Pseudonym("Description"),
    notes=Pseudonym("Note"),
    source=Code("simplefin", "csv", "ofx", "manual", "import"),
    transfer_group_id=K,
)
register(inv.PortfolioOut, as_of=K, base_currency=CUR, total_base=K, accounts=N)
register(
    inv.AccountValuationOut,
    account_id=K,
    name=Pseudonym("Account"),
    currency=CUR,
    balance_source=Code("stated", "derived"),
    balance_account=K,
    market_value_account=K,
    market_value_base=K,
    stated_balance_base=K,
    unaccounted_cash_base=K,
    holdings=N,
    unpriced=K,
    no_rate=K,
    oldest_price_date=K,
    max_stale_days=K,
    is_fully_valued=K,
)
register(
    inv.HoldingValueOut,
    holding_id=K,
    account_id=K,
    security_id=K,
    name=Pseudonym("Security"),
    ticker=Ticker(),
    security_type=SECURITY_TYPES,
    quantity=K,
    price=K,
    price_date=K,
    price_currency=CUR,
    value_native=K,
    value_account=K,
    value_base=K,
    stale_days=K,
    reason=Code(),
)
register(
    inv.AllocationOut,
    as_of=K,
    base_currency=CUR,
    group_by=Code("security", "type", "account", "currency"),
    total_base=K,
    rows=N,
    unpriced_positions=K,
    no_rate_positions=K,
    max_stale_days=K,
)
register(
    inv.AllocationRowOut,
    key=Key(),
    label=Label("Holding"),
    value_base=K,
    percent=K,
    holdings=K,
)

# ---- checks, connections, sync --------------------------------------------------

register(ch.ChecksOut, schema_version=VERSION, checks=N)
register(
    ch.CheckOut,
    id=Code(),
    status=Code("ok", "warn", "fail", "info"),
    count=K,
    summary=TEXT,
    items=N,
)
register(ch.CheckItemOut, account_id=K, name=Pseudonym("Account"))
register(
    co.ConnectionOut,
    id=K,
    provider=Code(),
    org_name=Pseudonym("Institution"),
    status=Code(),
    last_synced_at=K,
    last_error=EXT,
    is_enabled=K,
    sync_interval_minutes=K,
    next_sync_at=K,
    created_at=K,
    last_new_data_at=K,
    quiet_syncs=K,
)
register(
    co.ConnectionDefaults,
    sync_interval_minutes=K,
    sync_interval_min_minutes=K,
    sync_interval_max_minutes=K,
)
register(
    co.SyncJobOut,
    id=K,
    connection_id=K,
    trigger=Code(),
    status=Code(),
    attempts=K,
    created_at=K,
    not_before=K,
    claimed_at=K,
    heartbeat_at=K,
    cancel_requested_at=K,
    started_at=K,
    finished_at=K,
    error=EXT,
)
register(
    co.SyncRunOut,
    id=K,
    connection_id=K,
    connection_label=Pseudonym("Institution"),
    trigger=Code(),
    status=Code(),
    started_at=K,
    finished_at=K,
    duration_ms=K,
    http_ms=K,
    http_status=K,
    bytes_fetched=K,
    accounts_seen=K,
    accounts_created=K,
    accounts_remapped=K,
    txns_rekeyed=K,
    txns_inserted=K,
    txns_updated=K,
    txns_reconciled=K,
    pendings_expired=K,
    transfers_matched=K,
    rules_applied=K,
    error=EXT,
)
register(co.SyncRunDetail, run=N, events=N)
register(
    co.SyncRunEventOut,
    id=K,
    seq=K,
    ts=K,
    level=Code("debug", "info", "warning", "error"),
    event=Code(),
    detail=EVENT_DETAIL,
)
register(
    co.NotificationOut,
    id=K,
    run_id=K,
    ts=K,
    connection_id=K,
    title=EXT,
    body=EXT,
)

# ---- the agent routes' own schemas ---------------------------------------------

register(
    ag.RuleTraceOut,
    rule_id=K,
    name=Pseudonym("Rule"),
    priority=K,
    enabled=K,
    order=K,
    conditions=JsonTree(),
    matched=K,
    rule_conditions=N,
    rule_actions=N,
    writes=Code(),
    blocked_by_user=Code(),
)
register(
    ag.OwnerResolutionOut,
    effective_owner_id=K,
    decided_by=Code("transaction", "account"),
    transaction_owner_id=K,
    account_owner_id=K,
)
register(
    ag.TransactionExplainOut,
    transaction=N,
    account=N,
    category=N,
    owner=N,
    provenance=FIELD_SOURCES,
    rules=N,
    matched_rule_ids=K,
    transfer=N,
    notes=TEXT,
)
register(ag.SnapshotOut, balance_date=K, balance=K, currency=CUR)
register(
    ag.BalanceExplainOut,
    account=N,
    balance_source=Code("stated", "derived"),
    connection_id=K,
    connection_status=Code(),
    first_snapshot_date=K,
    last_snapshot_date=K,
    snapshot_count=K,
    snapshots=N,
    transaction_count=K,
    earliest_transaction_date=K,
    latest_transaction_date=K,
    moved_since_last_snapshot=K,
    current_matches_last_snapshot=K,
    holdings=N,
    flagged_by_checks=Code(),
    notes=TEXT,
)
register(ag.CurrencyCoverageOut, currency=CUR, accounts=K, latest_rate_date=K)
register(
    ag.ConnectionHealthOut,
    connection_id=K,
    status=Code(),
    is_enabled=K,
    last_synced_at=K,
    last_run_status=Code(),
    last_run_started_at=K,
    last_error=EXT,
)
register(
    ag.SystemOut,
    app_version=VERSION,
    schema_version=VERSION,
    env=Code(),
    server_time=K,
    household_id=K,
    base_currency=CUR,
    timezone=Timezone(),
    # Settings the system route chose to report: booleans, codes and currencies,
    # built by the route from the settings object — never the secret-bearing ones.
    settings=JsonTree(
        codes=("env", "log_level", "fx_fetch", "cookie_secure", "default_base_currency")
    ),
    counts=JsonTree(),
    currencies=N,
    connections=N,
    active_jobs=K,
    last_job_heartbeat_at=K,
    checks=JsonTree(values=("ok", "warn", "fail", "info")),
)
