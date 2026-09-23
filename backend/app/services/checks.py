"""Data checks — the invariants the net-worth work depends on, verified on every start.

A deploy used to come with a script: run a preview query before upgrading, read
it, run validation queries after. A step a person has to remember is a step that
gets skipped, and its output landed wherever the person ran it. These checks run
by themselves — the worker runs them once per household every time it starts, so
every deploy is checked the moment it lands — and anyone in the household can see
the result in Admin (``GET /checks``), with the accounts behind each finding.

**What reaches the worker's log is shape, never content**: a check's id, its
status and a count. No account name, no balance, no institution — the log is
stdout, which goes wherever the host sends container logs, and a household's
finances do not belong there. The names are in the API response only, behind the
household's own session and RLS.

Each check is a fact the code relies on, stated so a failure says what to do:

* ``headline_matches_chart`` — the Accounts total equals the net-worth line's
  value today (ADR-0043/0044/0045). If this fails, the chart and the number beside
  it disagree, which is the audit's original symptom.
* ``synced_investments_valued`` — no synced investment account is valued from
  holdings it does not have (ADR-0044, migration 0006).
* ``liabilities_signed`` — synced cards/loans that have only ever been positive:
  a card in credit, or a bank using the other sign, which would count the debt in
  the household's favour (ADR-0043, migration 0007 left them alone).
* ``manual_liabilities_positive`` — hand-entered cards/loans in credit; normally a
  mistake since ADR-0043 (debt is negative).
* ``accounts_without_rate`` — accounts whose currency has no rate today, so they
  are missing from net worth.
* ``stale_accounts`` — synced accounts the bank stopped reporting; their last
  balance is still counted.
* ``migrations_applied`` — what migrations 0006/0007 changed in this household
  (their backups), so the deploy's effect is visible without a query.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, BalanceSnapshot, Holding, InvestmentTransaction
from app.services import fx, ledger, reports

Status = Literal["ok", "warn", "fail", "info"]


@dataclass
class CheckItem:
    account_id: uuid.UUID
    name: str


@dataclass
class Check:
    id: str
    status: Status
    count: int
    #: One sentence for Admin. It never goes in the log — ``log_shape`` does.
    summary: str
    items: list[CheckItem] = field(default_factory=list)

    def log_shape(self) -> dict:
        """What the worker may log about this check."""
        return {"status": self.status, "count": self.count}


def _items(rows) -> list[CheckItem]:
    return [CheckItem(account_id=r[0], name=r[1]) for r in rows]


async def _headline_matches_chart(session: AsyncSession, household_id: uuid.UUID) -> Check:
    base = await ledger.base_currency(session, household_id)
    headline = (await ledger.net_worth(session, household_id))["net_worth"]
    (point,) = await reports.net_worth_points(session, [ledger.today()], base)
    ok = headline == point
    return Check(
        id="headline_matches_chart",
        status="ok" if ok else "fail",
        count=0 if ok else 1,
        summary=(
            "The Accounts total equals today's point on the net-worth chart."
            if ok
            else "The Accounts total and today's point on the net-worth chart differ."
        ),
    )


async def _synced_investments_valued(session: AsyncSession) -> Check:
    rows = (
        await session.execute(
            select(Account.id, Account.name).where(
                Account.type == "investment",
                Account.balance_source == "derived",
                Account.external_key.is_not(None),
                ~exists().where(Holding.account_id == Account.id),
                ~exists().where(InvestmentTransaction.account_id == Account.id),
            )
        )
    ).all()
    return Check(
        id="synced_investments_valued",
        status="fail" if rows else "ok",
        count=len(rows),
        summary=(
            "Synced investment accounts valued from holdings they do not have."
            if rows
            else "Every synced investment account is valued from its balance or its holdings."
        ),
        items=_items(rows),
    )


async def _liabilities_signed(session: AsyncSession) -> Check:
    rows = (
        await session.execute(
            select(Account.id, Account.name).where(
                Account.type.in_(("credit", "loan")),
                Account.external_key.is_not(None),
                Account.current_balance > 0,
                ~exists().where(
                    BalanceSnapshot.account_id == Account.id, BalanceSnapshot.balance < 0
                ),
            )
        )
    ).all()
    return Check(
        id="liabilities_signed",
        status="warn" if rows else "ok",
        count=len(rows),
        summary=(
            "Synced cards or loans that have only ever reported a positive balance: "
            "in credit, or counted in the household's favour. Check each one."
            if rows
            else "Every synced card and loan holds its debt as a negative balance."
        ),
        items=_items(rows),
    )


async def _manual_liabilities_positive(session: AsyncSession) -> Check:
    rows = (
        await session.execute(
            select(Account.id, Account.name).where(
                Account.type.in_(("credit", "loan")),
                Account.external_key.is_(None),
                Account.current_balance > 0,
            )
        )
    ).all()
    return Check(
        id="manual_liabilities_positive",
        status="warn" if rows else "ok",
        count=len(rows),
        summary=(
            "Hand-entered cards or loans shown in credit. If they are owed instead, "
            "edit the amount owed."
            if rows
            else "No hand-entered card or loan is in credit."
        ),
        items=_items(rows),
    )


async def _accounts_without_rate(session: AsyncSession, household_id: uuid.UUID) -> Check:
    base = await ledger.base_currency(session, household_id)
    accounts = (
        await session.execute(
            select(Account.id, Account.name, Account.currency).where(
                Account.currency != base, Account.is_hidden.is_(False)
            )
        )
    ).all()
    today = ledger.today()
    missing = [
        (a.id, a.name)
        for a in accounts
        if await fx.get_multiplier(
            session, from_ccy=a.currency, to_ccy=base, on=today, base_ccy=base
        )
        is None
    ]
    return Check(
        id="accounts_without_rate",
        status="warn" if missing else "ok",
        count=len(missing),
        summary=(
            "Accounts in a currency with no exchange rate today, so missing from net worth."
            if missing
            else "Every account's currency has a rate today."
        ),
        items=_items(missing),
    )


async def _stale_accounts(session: AsyncSession) -> Check:
    stale = await ledger.stale_since(session)
    names = dict(
        (await session.execute(select(Account.id, Account.name).where(Account.id.in_(stale)))).all()
    ) if stale else {}
    return Check(
        id="stale_accounts",
        status="warn" if stale else "ok",
        count=len(stale),
        summary=(
            "Synced accounts the bank stopped reporting; their last balance is still counted."
            if stale
            else "Every synced account is still being reported."
        ),
        items=[CheckItem(account_id=i, name=names[i]) for i in stale],
    )


async def _migrations_applied(session: AsyncSession) -> Check:
    """The household's current signed state: synced investment accounts valued
    from their balance, and cards/loans holding a negative balance.

    Not a count of what 0006/0007 changed — their backups are out of the app
    role's reach on purpose (0006), and an account created negative after the
    upgrade counts here too. It is the state those migrations exist to produce,
    shown so a deploy's effect is visible without a query. Always info.
    """
    stated = (
        await session.execute(
            select(Account.id).where(
                Account.type == "investment",
                Account.balance_source == "stated",
                Account.external_key.is_not(None),
            )
        )
    ).all()
    signed = (
        await session.execute(
            select(Account.id).where(
                Account.type.in_(("credit", "loan")), Account.current_balance < 0
            )
        )
    ).all()
    return Check(
        id="migrations_applied",
        status="info",
        count=len(stated) + len(signed),
        summary=(
            f"{len(stated)} synced investment account(s) valued from their balance; "
            f"{len(signed)} card(s) or loan(s) holding debt as a negative balance."
        ),
    )


CheckFn = Callable[[AsyncSession], Awaitable[Check]]


def _checks(household_id: uuid.UUID) -> list[tuple[str, CheckFn]]:
    """Every check, in the order Admin shows them."""
    return [
        ("headline_matches_chart", lambda s: _headline_matches_chart(s, household_id)),
        ("synced_investments_valued", _synced_investments_valued),
        ("liabilities_signed", _liabilities_signed),
        ("manual_liabilities_positive", _manual_liabilities_positive),
        ("accounts_without_rate", lambda s: _accounts_without_rate(s, household_id)),
        ("stale_accounts", _stale_accounts),
        ("migrations_applied", _migrations_applied),
    ]


async def run(session: AsyncSession, household_id: uuid.UUID) -> list[Check]:
    """Every check, for one household.

    Each runs in its own savepoint, so one that crashes is reported as a failed
    check — by its id and its exception's *type*, never its message, which for a
    database error carries the statement's parameters — and the rest still run.
    """
    results: list[Check] = []
    for check_id, check in _checks(household_id):
        try:
            async with session.begin_nested():
                results.append(await check(session))
        except Exception as exc:  # noqa: BLE001 — a crashed check is a finding, not an outage
            results.append(Check(
                id=check_id, status="fail", count=0,
                summary=f"This check could not run ({type(exc).__name__}).",
            ))
    return results


def as_dicts(results: list[Check]) -> list[dict]:
    return [asdict(c) for c in results]


async def schema_version(session: AsyncSession) -> str | None:
    """The database's migration revision, or None when it cannot be read."""
    try:
        return (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:  # noqa: BLE001 — a permission or a missing table is an answer too
        return None
