"""The ``/anon_debug`` views: why a row or a balance is what it is, what a page
shows, and the state of the system (ADR-0048).

Each view is composed from app routes (``dispatch.fetch``) where one exists — so a
transaction here is the transaction the page shows — plus a few direct, read-only
queries for what no route returns. The composed schema is then anonymized whole
by the caller, with the same registry as everything else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import Request
from sqlalchemy import Date, func, or_, select

from app.agent import dispatch
from app.agent.tokens import AgentPrincipal
from app.models import (
    Account,
    AccountConnection,
    BalanceSnapshot,
    Category,
    FxRate,
    Holding,
    Household,
    Owner,
    Rule,
    Security,
    SyncJob,
    SyncRun,
    Tag,
    Transaction,
)
from app.schemas.agent import (
    BalanceExplainOut,
    ConnectionHealthOut,
    CurrencyCoverageOut,
    OwnerResolutionOut,
    RuleTraceOut,
    SnapshotOut,
    SystemOut,
    TransactionExplainOut,
)
from app.schemas.rules import RuleActions, RuleConditions
from app.services import rules as rules_service
from app.services.rules import USER
from app.settings import get_settings

SNAPSHOT_LIMIT = 90


class ViewError(Exception):
    """A view could not be built; carries the error ``dispatch.Result`` to return."""

    def __init__(self, result: dispatch.Result) -> None:
        self.result = result


async def _need(request: Request, principal: AgentPrincipal, path: str, query=()) -> object:
    result = await dispatch.fetch_model(request, principal, path, query)
    if result.status != 200:
        raise ViewError(result)
    return result.body


# ---- transaction -------------------------------------------------------------


def _writes(actions: RuleActions) -> list[str]:
    """The provenance fields a rule's actions write (``rules._apply_to_row``)."""
    fields = []
    if actions.set_category_id is not None:
        fields.append("category")
    if actions.set_owner_id is not None:
        fields.append("owner")
    if actions.rename_merchant is not None:
        fields.append("merchant")
    if actions.set_hidden is not None:
        fields.append("is_hidden")
    if actions.mark_reviewed is not None:
        fields.append("review_status")
    if actions.add_tag_ids is not None:
        fields.append("tags")
    if actions.split is not None:
        fields.append("splits")
    return fields


async def explain_transaction(
    request: Request, principal: AgentPrincipal, txn_id: uuid.UUID
) -> TransactionExplainOut:
    txn = await _need(request, principal, f"/transactions/{txn_id}")
    account = await _need(request, principal, f"/accounts/{txn.account_id}")
    categories = await _need(request, principal, "/categories")
    category = next((c for c in categories if c.id == txn.category_id), None)
    transfer = None
    if txn.transfer_group_id is not None:
        transfer = await _need(
            request, principal, f"/transactions/transfers/{txn.transfer_group_id}"
        )

    notes: list[str] = []
    sources = dict(txn.field_sources or {})
    traces: list[RuleTraceOut] = []
    matched_ids: list[uuid.UUID] = []
    async with dispatch.agent_session(principal) as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.id == txn_id))
        ).scalar_one()
        for order, rule in enumerate(await rules_service.list_rules(session)):
            conditions = RuleConditions.model_validate(rule.conditions or {})
            actions = RuleActions.model_validate(rule.actions or {})
            results = rules_service.condition_results(conditions, row)
            matched = all(results.values())
            writes = _writes(actions)
            if matched and rule.enabled:
                matched_ids.append(rule.id)
            traces.append(
                RuleTraceOut(
                    rule_id=rule.id,
                    name=rule.name,
                    priority=rule.priority,
                    enabled=rule.enabled,
                    order=order,
                    conditions=results,
                    matched=matched,
                    rule_conditions=conditions,
                    rule_actions=actions,
                    writes=writes,
                    blocked_by_user=[f for f in writes if sources.get(f) == USER],
                )
            )

    if not traces:
        notes.append("The household has no rules.")
    elif not matched_ids:
        notes.append("No enabled rule matches this transaction.")
    for f, origin in sorted(sources.items()):
        if origin == USER:
            notes.append(f"{f} was set by a person; no rule or sync can change it (ADR-0007).")
    if txn.transfer_group_id is not None:
        notes.append("This is a transfer leg: cash-flow and spending reports exclude it.")
    if txn.is_hidden:
        notes.append("Hidden: it is left out of reports and the default transaction list.")
    if txn.is_pending:
        notes.append("Pending: the provider may still change or drop it.")
    if txn.base_amount is None:
        notes.append(
            f"No FX rate converts {txn.currency} on this date, so reports cannot count it."
        )

    return TransactionExplainOut(
        transaction=txn,
        account=account,
        category=category,
        owner=OwnerResolutionOut(
            effective_owner_id=txn.effective_owner_id,
            decided_by="transaction" if txn.owner_id is not None else "account",
            transaction_owner_id=txn.owner_id,
            account_owner_id=account.owner_id,
        ),
        provenance=sources,
        rules=traces,
        matched_rule_ids=matched_ids,
        transfer=transfer,
        notes=notes,
    )


# ---- balance -----------------------------------------------------------------


async def explain_balance(
    request: Request, principal: AgentPrincipal, account_id: uuid.UUID
) -> BalanceExplainOut:
    account = await _need(request, principal, f"/accounts/{account_id}")
    holdings = []
    if account.type == "investment":
        holdings = await _need(
            request, principal, "/investments/holdings", [("account_id", str(account_id))]
        )
    checks = await _need(request, principal, "/checks")
    flagged = [c.id for c in checks.checks if any(i.account_id == account_id for i in c.items)]

    async with dispatch.agent_session(principal) as session:
        row = await session.get(Account, account_id)
        connection = (
            await session.get(AccountConnection, row.connection_id) if row.connection_id else None
        )
        snaps = (
            (
                await session.execute(
                    select(BalanceSnapshot)
                    .where(BalanceSnapshot.account_id == account_id)
                    .order_by(BalanceSnapshot.balance_date.desc())
                    .limit(SNAPSHOT_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        snap_count, first_snap = (
            await session.execute(
                select(func.count(), func.min(BalanceSnapshot.balance_date)).where(
                    BalanceSnapshot.account_id == account_id
                )
            )
        ).one()
        txn_day = Transaction.transacted_at.cast(Date)
        txn_count, earliest, latest = (
            await session.execute(
                select(func.count(), func.min(txn_day), func.max(txn_day)).where(
                    Transaction.account_id == account_id
                )
            )
        ).one()
        last_snap = snaps[0] if snaps else None
        moved_q = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == account_id
        )
        if last_snap is not None:
            moved_q = moved_q.where(txn_day > last_snap.balance_date)
        moved = (await session.execute(moved_q)).scalar_one()

    notes: list[str] = []
    if last_snap is None:
        if account.type == "investment":
            notes.append(
                "No snapshots yet: net worth reads this account from its holdings or not at all."
            )
        else:
            notes.append(
                "No snapshots: net worth derives this balance backwards from its "
                "transactions (ADR-0045)."
            )
    elif account.current_balance != last_snap.balance:
        notes.append(
            "current_balance differs from the last snapshot; a balance is filed at its own "
            "date and the current balance only moves forward (ADR-0044)."
        )
    if last_snap is not None and moved:
        notes.append(
            "Transactions dated after the last snapshot move the balance by "
            "moved_since_last_snapshot; the next balance a provider reports should agree."
        )
    if not account.is_asset and account.current_balance > 0:
        notes.append("A liability with a positive balance: debt is stored negative (ADR-0043).")
    if account.stale_since is not None:
        notes.append("The account is stale: its provider has not reported it recently.")

    return BalanceExplainOut(
        account=account,
        balance_source=row.balance_source,
        connection_id=row.connection_id,
        connection_status=connection.status if connection else None,
        first_snapshot_date=first_snap,
        last_snapshot_date=last_snap.balance_date if last_snap else None,
        snapshot_count=snap_count,
        snapshots=[
            SnapshotOut(balance_date=s.balance_date, balance=s.balance, currency=s.currency)
            for s in snaps
        ],
        transaction_count=txn_count,
        earliest_transaction_date=earliest,
        latest_transaction_date=latest,
        moved_since_last_snapshot=moved,
        current_matches_last_snapshot=(account.current_balance == last_snap.balance)
        if last_snap
        else None,
        holdings=holdings,
        flagged_by_checks=flagged,
        notes=notes,
    )


# ---- system ------------------------------------------------------------------


async def system(request: Request, principal: AgentPrincipal) -> SystemOut:
    settings = get_settings()
    checks = await _need(request, principal, "/checks")
    async with dispatch.agent_session(principal) as session:
        household = await session.get(Household, principal.household_id)

        async def count(model) -> int:
            return (await session.execute(select(func.count()).select_from(model))).scalar_one()

        counts = {
            name: await count(model)
            for name, model in (
                ("accounts", Account),
                ("transactions", Transaction),
                ("rules", Rule),
                ("owners", Owner),
                ("categories", Category),
                ("tags", Tag),
                ("securities", Security),
                ("holdings", Holding),
                ("connections", AccountConnection),
                ("balance_snapshots", BalanceSnapshot),
                ("sync_runs", SyncRun),
                ("sync_jobs", SyncJob),
            )
        }
        per_currency = (
            await session.execute(select(Account.currency, func.count()).group_by(Account.currency))
        ).all()
        currencies = []
        for ccy, n in sorted(per_currency):
            latest = None
            if ccy != household.base_currency:
                latest = (
                    await session.execute(
                        select(func.max(FxRate.rate_date)).where(
                            or_(
                                (FxRate.base_currency == household.base_currency)
                                & (FxRate.quote_currency == ccy),
                                (FxRate.base_currency == ccy)
                                & (FxRate.quote_currency == household.base_currency),
                            )
                        )
                    )
                ).scalar_one()
            currencies.append(
                CurrencyCoverageOut(currency=ccy, accounts=n, latest_rate_date=latest)
            )

        connections = []
        for c in (
            await session.execute(select(AccountConnection).order_by(AccountConnection.created_at))
        ).scalars():
            run = (
                await session.execute(
                    select(SyncRun)
                    .where(SyncRun.connection_id == c.id)
                    .order_by(SyncRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            connections.append(
                ConnectionHealthOut(
                    connection_id=c.id,
                    status=c.status,
                    is_enabled=c.is_enabled,
                    last_synced_at=c.last_synced_at,
                    last_run_status=run.status if run else None,
                    last_run_started_at=run.started_at if run else None,
                    last_error=c.last_error,
                )
            )
        active_jobs = (
            await session.execute(
                select(func.count())
                .select_from(SyncJob)
                .where(SyncJob.status.in_(("queued", "running")))
            )
        ).scalar_one()
        heartbeat = (await session.execute(select(func.max(SyncJob.heartbeat_at)))).scalar_one()

    return SystemOut(
        app_version=request.app.version,
        schema_version=checks.schema_version,
        env=settings.env,
        server_time=datetime.now(UTC),
        household_id=principal.household_id,
        base_currency=household.base_currency,
        timezone=household.timezone,
        settings={
            # Only these, by name — never a DSN, a password or the secret key.
            "env": settings.env,
            "log_level": settings.log_level,
            "open_signup": settings.open_signup,
            "fx_fetch": settings.fx_fetch_enabled,
            "cookie_secure": settings.cookie_is_secure,
            "default_base_currency": settings.default_base_currency,
            "session_idle_minutes": settings.session_idle_minutes,
            "session_absolute_hours": settings.session_absolute_hours,
            "notify_webhook_configured": bool(settings.notify_webhook_url),
        },
        counts=counts,
        currencies=currencies,
        connections=connections,
        active_jobs=active_jobs,
        last_job_heartbeat_at=heartbeat,
        checks={c.id: c.status for c in checks.checks},
    )


# ---- pages -------------------------------------------------------------------


@dataclass(frozen=True)
class PageWindow:
    start: date
    end: date
    owner_id: uuid.UUID | None


def _owner(w: PageWindow) -> list[tuple[str, str]]:
    return [("owner_id", str(w.owner_id))] if w.owner_id else []


def _window(w: PageWindow) -> list[tuple[str, str]]:
    return [("start", w.start.isoformat()), ("end", w.end.isoformat()), *_owner(w)]


#: What each page of the app loads (``frontend/src/pages`` and ``api/hooks.ts``),
#: as ``(description, [(path, query-builder)])``. The page's own defaults are
#: approximated by the window parameters; pass the page's actual ones to match it.
PAGES: dict[str, tuple[str, list[tuple[str, object]]]] = {
    "accounts": (
        "Accounts: every account with its balance, net worth, and the chart.",
        [
            ("/accounts", _owner),
            ("/accounts/net-worth", _owner),
            ("/owners", None),
            ("/household", None),
            ("/reports/net-worth", _window),
        ],
    ),
    "transactions": (
        "Transactions: the first page of the ledger and the pickers it uses.",
        [
            ("/transactions", lambda w: _owner(w)),
            ("/accounts", None),
            ("/categories", None),
            ("/category-groups", None),
            ("/tags", None),
            ("/owners", None),
        ],
    ),
    "review": (
        "Review: the queue of transactions that need review.",
        [
            ("/transactions", lambda w: [("review_status", "needs_review"), *_owner(w)]),
            ("/categories", None),
            ("/owners", None),
        ],
    ),
    "reports": (
        "Reports: net worth, cash flow, the sankey and spending over a window.",
        [
            ("/reports/net-worth", _window),
            ("/reports/cash-flow", _window),
            ("/reports/cash-flow/sankey", _window),
            ("/reports/spending", _window),
        ],
    ),
    "investments": (
        "Investments: portfolio, allocation, holdings and trades.",
        [
            ("/investments/portfolio", None),
            ("/investments/allocation", None),
            ("/investments/holdings", None),
            ("/investments/transactions", None),
            ("/investments/securities", None),
        ],
    ),
    "settings": (
        "Settings: household, members, owners, categories, tags, rules, FX rates.",
        [
            ("/household", None),
            ("/household/members", None),
            ("/owners", None),
            ("/categories", None),
            ("/category-groups", None),
            ("/tags", None),
            ("/rules", None),
            ("/fx-rates", None),
        ],
    ),
    "admin": (
        "Admin: connections, sync jobs and runs, notifications and data checks.",
        [
            ("/connections", None),
            ("/connections/jobs", None),
            ("/connections/runs", None),
            ("/connections/notifications", None),
            ("/checks", None),
        ],
    ),
}


def page_window(start: date | None, end: date | None, owner_id: uuid.UUID | None) -> PageWindow:
    end = end or date.today()
    start = start or (end - timedelta(days=365))
    return PageWindow(start=start, end=end, owner_id=owner_id)


def page_calls(page: str, window: PageWindow) -> list[tuple[str, list[tuple[str, str]]]]:
    _desc, calls = PAGES[page]
    return [(path, build(window) if build else []) for path, build in calls]
