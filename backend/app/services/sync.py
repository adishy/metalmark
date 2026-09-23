"""The sync engine: window, accounts, ingest, reconciliation, expiry (ADR-0004/0009/0016/0019/0028).

One run of one connection — compute the window, upsert accounts, ingest
transactions, reconcile pendings, expire phantoms, snapshot balances and
transfers, and write down what happened.

**The engine, not the queue.** ``run_connection_sync`` executes a connection once
and owns the three transactions that takes; ``worker.py`` owns *when* and
*which*, plus the job row's lifecycle. The split is what lets a test drive the
whole engine with no scheduler, no queue and no job fixture.

Three things the engine is careful about, each because the alternative loses
data or holds a lock:

* **The HTTP fetch is inside no transaction.** It is a 60-second call; holding a
  transaction open across it pins a pool connection and an MVCC snapshot for a
  minute, and the RLS GUC is transaction-*local*, so every DB touch needs its own
  transaction regardless.
* **A failed run writes its own error from a fresh transaction** (TX3). Without
  it, a failure mid-ingest rolls back the run row's error along with everything
  else, and the run sits ``running`` forever with nothing explaining why.
* **Provenance gates every write to an existing row.** A human's value is never
  overwritten and a rule's outranks the provider's (ADR-0007). The pending
  matcher cannot see a manual row *at all* — that is a predicate in the query,
  not a promise about the callers of it.

Nothing in here knows what SimpleFIN is. The provider arrives as an
``AggregatorProvider``; the wire format stops at ``services/simplefin.py``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scoped_session
from app.models import (
    Account,
    AccountConnection,
    SyncRun,
    SyncRunEvent,
    Transaction,
)

# The status vocabularies stay in their own module: they are the *table's* enums,
# not part of the model package's surface, and the same is true of JOB_STATUSES.
from app.models.sync import EVENT_LEVELS
from app.schemas.ledger import is_asset_for
from app.security.crypto import SecretBox
from app.security.redact import sanitize
from app.services import notifications, rules
from app.services import transactions as txn_service
from app.services.aggregator import (
    AccountSet,
    AggregatorProvider,
    ProviderAccount,
    ProviderError,
    ProviderTransaction,
    external_key_for,
    get_provider,
    infer_account_type,
)
from app.services.errors import LedgerError
from app.services.ledger import base_currency, record_balance
from app.services.owners import ensure_shared_owner
from app.settings import get_settings

#: ``Transaction.source`` for a synced row. The column documents
#: ``simplefin|csv|ofx|manual``; the fake provider stands in for SimpleFIN rather
#: than being a fifth origin, so a demo connection's rows are labelled the same
#: as the real thing's — which is the point of a demo.
SOURCE = "simplefin"

#: The weakest provenance origin (ADR-0007's ``provider``). Spelled here rather
#: than imported from ``aggregator`` because it names a *ledger* fact, and the
#: aggregator seam deliberately knows nothing about the ledger.
PROVIDER = "provider"

#: How far back a sync reaches when nothing is unsettled. Not zero: the bridge's
#: ``start-date`` filter is inclusive and its clock is not ours, so "since the
#: last run" could straddle a transaction posted a second either side of the
#: boundary. Five days costs little — the payload is bounded by the same window —
#: and it makes a missed or failed run self-healing.
OVERLAP_DAYS = 5

#: The first-ever window. The bridge caps a pull at 90 days and *warns* from 45
#: (fixture README), so asking for 90 would put a ``gen.api`` warning — and a
#: ``partial`` badge — on every first sync of every connection, to fetch history
#: the account never had.
#:
#: **44, not 45, and that is the entire point of the constant.** Measured against
#: the live bridge rather than read off the message, which says 45: the warning is
#: triggered by the *calendar date* of ``start-date``, and a start date 45 days
#: back warns while 44 does not. So 45 is the boundary rather than safely under
#: it — the value whose whole purpose is to keep a first sync clean is the one
#: that makes it ``partial``, and only against a real connection, where the
#: fixtures cannot say. ``tests/unit/test_sync.py`` pins the relationship with
#: ``RECOMMENDED_WINDOW_DAYS``, because 44 next to a message that says 45 reads
#: like a typo until you read this.
FIRST_SYNC_WINDOW_DAYS = 44

#: A ceiling on how far back an unsettled row can drag the window. Without it, one
#: pending row a human marked by hand and never cleared would widen the window on
#: every run until the bridge capped it.
#:
#: **89, not a year, and that is what makes the sentence above true.** The cap is
#: measured at exactly 90 days, and the bridge *truncates* rather than refuses: any
#: request past it returns 90 days of data under a ``gen.api`` cap warning. So a
#: round-number ceiling of 365 guaranteed the truncation this constant exists to
#: avoid — the run would be narrower than its own log claimed, and ``partial`` for
#: asking for a range it was never going to get. Clamping here changes no data:
#: anything past 90 days is unreachable either way, so a stale pending row expires
#: through the TTL path, which is the honest account of what happened to it.
MAX_LOOKBACK_DAYS = 89

#: A pending row that neither posts nor reappears is expired after this long. Long
#: enough to survive a weekend and a bank holiday; short enough that a phantom
#: charge does not sit in the ledger for a month.
PENDING_TTL_DAYS = 7

#: Deliberately **wider** than ``imports.NEAR_DUPLICATE_DAYS`` (2). That constant
#: decides "warn a human about a possible duplicate"; this one decides "this is
#: the same row, merge it". A wrong merge loses a row and needs someone to notice
#: it was ever there; a duplicate warning just asks. Same shape, different risk,
#: so deliberately not the same number.
SYNC_PENDING_MATCH_DAYS = 3

#: How many same-amount pending rows are still worth reasoning about. Above this
#: the run stops guessing: "which of these seven $4.50 coffees is this?" has no
#: answer a program should pick, and inserting a visible duplicate is recoverable
#: where merging the wrong pair is not.
PENDING_CANDIDATE_LIMIT = 5

#: Backoff for a failing connection. Jitter only ever *subtracts* (see
#: ``backoff_seconds``), so the cap stays a cap.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
BACKOFF_JITTER = 0.25

#: The columns a provider owns → the ``field_sources`` key each is keyed by. The
#: keys, not the columns: provenance is keyed by *field*, which is the vocabulary
#: ``services/transactions.py`` already uses (it marks ``"owner"`` while assigning
#: ``owner_id``).
_FIELD_ATTR: dict[str, str] = {
    "amount": "amount",
    "transacted_at": "transacted_at",
    "posted_at": "posted_at",
    "description": "description",
    "merchant": "merchant",
    "is_pending": "is_pending",
}

#: Fields the provider may only *set*, never clear. A payload that omits a
#: description is not the provider telling us the description is empty; reading it
#: that way would erase a good value on every fetch that happened to be thinner.
#: ``amount``/``transacted_at``/``posted_at`` are not here: each has a real
#: meaning when absent (``posted_at`` *is* the pending signal).
_SET_ONLY = frozenset({"description", "merchant"})

#: Fence: called inside the ingest transaction, immediately after the fetch.
#: Returns ``False`` when this run no longer owns its job — reaped and re-claimed,
#: or cancelled — in which case the run closes ``cancelled`` and nothing is
#: ingested. The worker supplies it; the engine's only promise is that it asks at
#: the one moment the answer is still cheap to act on.
Fence = Callable[[AsyncSession], Awaitable[bool]]

_WORDS = re.compile(r"[^a-z0-9]+")


# ---- window ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """The ``start-date`` to ask for, and enough context to explain it.

    ``pending_anchors`` is the number of accounts whose extension came from an
    unsettled row. It is carried so the run log can say *why* a window is unusually
    long — otherwise a 40-day window looks like a bug rather than like a charge
    from last month that has not posted yet.
    """

    start: datetime
    first_sync: bool = False
    pending_anchors: int = 0


async def compute_window(
    session: AsyncSession, connection_id: uuid.UUID, *, now: datetime
) -> Window:
    """How far back to ask for this connection's transactions.

    A fixed ``now - 3d`` would be simpler and wrong: a pending charge that posts a
    week later falls outside it, and sync would insert a *second* row for a
    transaction it already has. So the window is anchored to the oldest row still
    unsettled (ARCHITECTURE §3's low-water mark), which is the only thing that
    knows how far back the outstanding work goes.

    The overlap is a **floor**, not an alternative: a pending anchor may only move
    the start earlier, never later. That is both simpler than a per-account
    minimum and strictly more conservative — the extra days cost nothing, because
    the same window caps the payload.
    """
    account_ids = (
        await session.execute(select(Account.id).where(Account.connection_id == connection_id))
    ).scalars().all()
    if not account_ids:
        # Nothing has ever been synced here, so there is no low-water mark to
        # compute and no history to be careful about.
        return Window(
            start=now - timedelta(days=FIRST_SYNC_WINDOW_DAYS), first_sync=True
        )

    anchors = (
        await session.execute(
            select(func.min(Transaction.transacted_at))
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.is_pending.is_(True),
            )
            .group_by(Transaction.account_id)
        )
    ).scalars().all()

    start = min([now - timedelta(days=OVERLAP_DAYS), *anchors])
    # Floor then ceiling, in that order: the ceiling is what stops a window from
    # opening in the future, and applying it first would let the floor undo it.
    start = max(start, now - timedelta(days=MAX_LOOKBACK_DAYS))
    start = min(start, now - timedelta(days=1))
    return Window(start=start, pending_anchors=len(anchors))


def backoff_seconds(attempts: int, *, rand: float = 0.0) -> int:
    """Delay before retrying a failed sync: exponential, capped, jittered.

    One function because both the retry path and the reaper need the number, and
    two derivations of "how long until this is worth trying again" would drift
    apart silently. ``rand`` is the jitter source, a value in ``[0, 1)`` — the
    default of ``0`` means no jitter, which is what makes the function testable.

    Jitter only ever *subtracts*, so the cap stays a hard cap; spreading both ways
    around it would let a caller that computed "one hour" actually wait longer
    than the ceiling it was reasoning about.
    """
    exponent = max(attempts - 1, 0)
    raw = min(BACKOFF_BASE_SECONDS * (2 ** min(exponent, 20)), BACKOFF_CAP_SECONDS)
    return int(raw * (1 - BACKOFF_JITTER * max(0.0, min(rand, 1.0))))


# ---- run log --------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Make a value safe to store in the JSONB ``detail``, sanitizing as it goes.

    Recursive because ``detail`` is a tree and a sanitizer that only looked at the
    top level would miss the one nested string that mattered. ``Decimal`` becomes a
    string rather than a float: the event log is not money, but the habit of
    converting money to float is the habit that eventually costs a cent.
    """
    if isinstance(value, str):
        return sanitize(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return sanitize(str(value))


class RunLog:
    """One run's ordered, sanitized event trail (ADR-0016).

    Sanitized at **write** time, never at read time. Write time is the only place
    that can also decline to store a bank payload fragment in the first place; a
    reader-side sanitizer would still have left the data sitting in a column that
    gets backed up and replicated.

    ``seq`` is assigned here because ``ts`` cannot order these rows: Postgres
    ``now()`` is the *transaction* timestamp, so every event written in one ingest
    shares it to the microsecond. ``services/rules.py`` documents the same trap.

    And the sequence is **resumed, not restarted**, because a run writes its events
    across up to three transactions and ``UNIQUE (sync_run_id, seq)`` is what makes
    the ordering a fact. A ``RunLog`` built for the second transaction therefore
    asks the table where the first one stopped. Constructing one with ``seq = 0``
    hard-coded would collide with the window event TX1 already committed, and every
    run would fail on a uniqueness violation for a reason that has nothing to do
    with the bank.
    """

    def __init__(
        self, session: AsyncSession, household_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        self._session = session
        self._household_id = household_id
        self._run_id = run_id
        #: Unknown until the first ``emit`` — see the class docstring.
        self._seq: int | None = None

    async def _next_seq(self) -> int:
        if self._seq is None:
            highest = (
                await self._session.execute(
                    select(func.max(SyncRunEvent.seq)).where(
                        SyncRunEvent.sync_run_id == self._run_id
                    )
                )
            ).scalar()
            self._seq = 0 if highest is None else highest + 1
        seq = self._seq
        self._seq += 1
        return seq

    async def emit(self, level: str, event: str, **detail: Any) -> None:
        if level not in EVENT_LEVELS:
            raise ValueError(f"unknown event level: {level!r}")
        self._session.add(
            SyncRunEvent(
                household_id=self._household_id,
                sync_run_id=self._run_id,
                seq=await self._next_seq(),
                level=level,
                event=event,
                detail=_jsonable(detail),
            )
        )


# ---- accounts -------------------------------------------------------------


@dataclass(slots=True)
class AccountUpsert:
    """What happened to one provider account, for the run's counters and log."""

    account: Account
    created: bool = False
    #: An existing account was re-pointed at this connection or at a new provider
    #: id — the reconnect remap (ADR-0009), and the counter the M2 bar is about.
    remapped: bool = False
    #: The key belongs to a *different live* connection, so two connections are
    #: reporting the same account and this one has just taken it over. Not an
    #: error — the ledger row is shared by design — but worth a run-log warning,
    #: because the account now follows whichever connection synced last.
    contested: bool = False


async def upsert_account(
    session: AsyncSession,
    household_id: uuid.UUID,
    connection: AccountConnection,
    pa: ProviderAccount,
    *,
    key: str | None = None,
) -> AccountUpsert:
    """Find this provider account's ledger row, or make one (ADR-0009).

    Two lookups, in this order, and the order is the design:

    1. **``external_key``** — institution + normalised name. This is the key that
       survives a reconnect, because a re-claim changes the provider's account id
       and the connection id but not what the account *is*.
    2. **``(connection_id, external_id)``** — the provider renamed the account, so
       the key no longer matches but the id still does.
    3. **Insert.**

    A key match is always **adopted**, whoever holds it, because the key *is* the
    account's identity within the household: ``uq_accounts_household_id`` allows
    exactly one row per ``(household_id, external_key)``. So "another connection
    already has this key" cannot mean "make a second one" — the database refuses
    that, and the run dies of a constraint violation. It means the two connections
    are reporting the same account, and the ledger's one row serves both. That is
    reported (``contested``) rather than hidden, because an account whose
    connection flips to whichever one synced last is worth knowing about.

    ``owner_id`` is never touched on an existing row. Attribution is a human's
    decision (ADR-0026) and a reconnect is not a reason to revise it.

    ``key`` overrides the computed key — ``_account_keys`` passes one when two
    accounts in the same payload would otherwise share it.
    """
    key = key or external_key_for(pa)
    # ``limit(1)``, not ``limit(2)``: the unique constraint makes more than one row
    # impossible, and a "which of these is it" branch that can never be taken is a
    # claim about the schema that stops being true the moment the schema changes.
    existing = (
        await session.execute(
            select(Account).where(Account.external_key == key).limit(1)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = (
            await session.execute(
                select(Account).where(
                    Account.connection_id == connection.id,
                    Account.external_id == pa.external_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            account = await _insert_account(session, household_id, connection, pa, key)
            return AccountUpsert(account=account, created=True)

    # Only contested when it belongs to a *different live* connection. NULL is the
    # reconnect case — the old connection was deleted — and this connection is
    # simply picking its own account back up.
    contested = existing.connection_id not in (None, connection.id)
    remapped = (
        existing.external_id != pa.external_id or existing.connection_id != connection.id
    )
    existing.external_id = pa.external_id
    existing.connection_id = connection.id
    return AccountUpsert(account=existing, remapped=remapped, contested=contested)


async def _insert_account(
    session: AsyncSession,
    household_id: uuid.UUID,
    connection: AccountConnection,
    pa: ProviderAccount,
    key: str,
) -> Account:
    """A new ledger account for one the provider just told us about.

    ``owner_id`` is the household's Shared owner and ``is_manual`` is False, both
    as PLAN.md's SYNC row requires: a synced account has an attribution from the
    moment it exists, so the chain terminates instead of needing a null case.
    """
    account_type = infer_account_type(pa)
    owner = await ensure_shared_owner(session, household_id)
    account = Account(
        household_id=household_id,
        connection_id=connection.id,
        external_id=pa.external_id,
        external_key=key,
        name=pa.name,
        type=account_type,
        institution=pa.org_name or connection.org_name,
        currency=pa.currency or await base_currency(session, household_id),
        current_balance=pa.balance,
        available_balance=pa.available_balance,
        balance_date=pa.balance_date,
        is_asset=is_asset_for(account_type),
        # *Stated*, unlike the manual path: ADR-0021's derived default assumes
        # holdings to derive from, and sync writes none — so a derived synced
        # account was valued at nothing on the chart while the Accounts page showed
        # the provider's balance. The provider's number is the only statement of
        # this account's value there is, so it is snapshotted like any other.
        balance_source="stated" if account_type == "investment" else None,
        owner_id=owner.id,
        is_manual=False,
    )
    session.add(account)
    await session.flush()
    return account


async def _rekey_remapped_account(
    session: AsyncSession, account: Account, pa: ProviderAccount
) -> int:
    """Re-point an account's rows at the provider ids this payload just used.

    ADR-0009's reconnect key exists because a re-claim changes the ids the bank
    hands out — the *account* id for certain, and a bridge that re-mints ids on
    reconnect changes the transactions' too. When that happens, ``(account_id,
    external_id)`` stops identifying anything, and every row in the payload looks
    new: a reconnected account's whole history would be inserted a second time.
    That is the failure the M2 bar ("loses zero transactions, creates zero dupes")
    is written against, and content is the only identity left to match on.

    So the match is deliberately **tighter than the pending matcher's**:

    * exact ``amount`` and exact ``transacted_at`` — a reconnect does not re-date
      or re-price history; only a *posting* shifts a date, and that is the other
      matcher's problem. A tolerance here would be a tolerance on identity;
    * the same loose description rule, because a bridge is free to reword a payee
      without meaning a different charge;
    * the row must not already carry an id this payload uses, so a row that is
      correctly keyed cannot be stolen from the transaction that owns it;
    * the target id must not already exist on this account;
    * and **exactly one candidate**, or the incoming transaction is left to be
      inserted. Two identical charges on one day is a real thing a ledger holds,
      and pairing them off is not a decision a program can make — the duplicate it
      leaves behind is visible and deletable, the wrong merge is neither.

    This runs only on the run where the account was remapped, and it is naturally
    a no-op when the ids did not change: every row then carries an id the payload
    uses, so every row is excluded and nothing is renamed.
    """
    incoming_ids = {t.external_id for t in pa.transactions}
    rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                # ADR-0019's boundary again: a row with no provider id is a
                # human's, and a reconnect is not a reason to hand it one.
                Transaction.external_id.is_not(None),
            )
        )
    ).scalars().all()
    existing_ids = {row.external_id for row in rows}

    # Bucketed by what cannot be argued about — the money and the day — so the
    # only judgement left is the description, and only among rows that already
    # agree on everything else.
    buckets: dict[tuple[Decimal, datetime], list[Transaction]] = {}
    for row in rows:
        if row.external_id in incoming_ids:
            continue
        buckets.setdefault((row.amount, row.transacted_at), []).append(row)

    renamed = 0
    for ptxn in pa.transactions:
        if ptxn.external_id in existing_ids:
            continue  # already keyed; the id lookup will find it on its own
        bucket = buckets.get((ptxn.amount, ptxn.transacted_at))
        if not bucket:
            continue
        matches = [r for r in bucket if _descriptions_match(r.description, ptxn.description)]
        if len(matches) != 1:
            continue
        row = matches[0]
        bucket.remove(row)  # claimed: a second lookalike must not take it too
        row.external_id = ptxn.external_id
        renamed += 1

    if renamed:
        await session.flush()
    return renamed


async def _apply_balance(
    session: AsyncSession, account: Account, pa: ProviderAccount, *, log: RunLog,
    now: datetime,
) -> None:
    """Write the provider's balance onto the account, and snapshot it.

    The ADR-0021 guard is here and is the whole of it: a *derived* account's
    balance series belongs to its holdings, so a synced stated balance must not
    write a point into it. Sync now creates investment accounts ``stated``, so
    this fires only for one created ``derived`` before migration 0006 that has
    holdings entered by hand (0006 left exactly those derived). The balance
    column still moves, but the history does not, because a history with two
    disagreeing sources in it is worse than no history.

    Where it lands is ``ledger.record_balance``'s rule, shared with every other
    writer: at the provider's ``balance_date``, or the run's day when it sends
    none — never the account's previous date, which is what used to overwrite the
    last point with today's number. Same date, same row, updated in place, so a
    re-sync is idempotent.
    """
    if not account.is_asset and pa.balance > 0:
        # ADR-0043 stores debt as a negative balance, which is what SimpleFIN
        # bridges send. A positive one is either a card in credit or a bridge using
        # the other sign — and the second would count every card for the
        # household. Not guessed at: said, where Admin shows it.
        await log.emit(
            "warning",
            "balance.liability_positive",
            account_id=str(account.id),
            name=account.name,
            reason="a liability reported a positive balance; debt is stored as "
            "negative, so check this account's sign (ADR-0043)",
        )
    derived = account.balance_source == "derived"
    current = await record_balance(
        session,
        account,
        balance=pa.balance,
        on=pa.balance_date or now.date(),
        snapshot=not derived,
    )
    if current:
        account.available_balance = pa.available_balance
        await session.flush()

    if derived:
        await log.emit(
            "info",
            "balance.derived_skipped",
            account_id=str(account.id),
            reason="this account's balance history comes from its holdings (ADR-0021)",
        )
        return
    await log.emit("debug", "balance.snapshotted", account_id=str(account.id))


# ---- transactions ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """What one provider transaction became.

    ``action`` is deliberately four-valued rather than a changed/not-changed
    flag, because the run's counters are: a re-sync that matches every row by id
    and changes nothing must report ``inserted == 0`` **and** ``updated == 0``,
    and only the caller can tell "matched and identical" from "matched and
    corrected" if this collapses them.
    """

    action: str  # inserted | updated | unchanged | reconciled
    txn: Transaction
    rules_applied: int = 0


async def ingest_transaction(
    session: AsyncSession,
    household_id: uuid.UUID,
    account: Account,
    ptxn: ProviderTransaction,
    *,
    loaded: rules.LoadedRules | None,
    now: datetime,
) -> IngestOutcome:
    """Land one provider transaction on the ledger, once (ADR-0007/0019).

    A separate function from ``transactions.create_transaction`` on purpose. That
    one's docstring states its contract — "everything below is still a ``user``
    decision" — which is right for a CSV import and exactly backwards for a
    provider. Threading both meanings through one function would give it two
    contradictory contracts, the ambiguity ADR-0007/0019 exist to remove. Manual
    parity is a statement about *capabilities*, and it holds: this writes only
    columns a human can write through ``PATCH /transactions/{id}``, and calls the
    same rules entry point.

    Three paths, in this order:

    1. **The provider id is already here** → update the row in place, honouring
       provenance field by field.
    2. **The id is new, but an unsettled row on this account is plausibly the same
       transaction** → adopt the new id onto that row. A bridge that re-mints ids
       when a charge posts is the case this exists for.
    3. **Otherwise insert.**

    Path 2 is why nothing is lost when a human has already worked on a pending
    row: the row is not copied, re-created or "carried forward" — it *is* the same
    row, so its category, splits, tags and notes were never at risk. There is
    deliberately no copy step to get wrong.
    """
    existing = await _by_external_id(session, account.id, ptxn.external_id)
    if existing is not None:
        changed = await _apply_provider_fields(existing, ptxn, now=now)
        applied = await _run_rules(session, household_id, existing, loaded) if changed else 0
        return IngestOutcome(
            action="updated" if changed else "unchanged",
            txn=existing,
            rules_applied=applied,
        )

    pending = await _match_pending(session, account, ptxn)
    if pending is not None:
        pending.external_id = ptxn.external_id
        await _apply_provider_fields(pending, ptxn, now=now)
        await session.flush()  # the new id must be visible before anything matches again
        applied = await _run_rules(session, household_id, pending, loaded)
        return IngestOutcome(action="reconciled", txn=pending, rules_applied=applied)

    txn = await _insert_transaction(session, household_id, account, ptxn, now=now)
    session.add(txn)
    await session.flush()
    applied = await _run_rules(session, household_id, txn, loaded)
    return IngestOutcome(action="inserted", txn=txn, rules_applied=applied)


async def _by_external_id(
    session: AsyncSession, account_id: uuid.UUID, external_id: str
) -> Transaction | None:
    """Scope the lookup to the **account**, always.

    The capture's trap (fixture README, correction 5): SimpleFIN transaction ids
    are unique within an account and repeat across accounts — every one of the
    demo's 170 ids is shared between Savings and Checking. Looking up by
    ``external_id`` alone would find another account's transaction and overwrite
    it. The ``(account_id, external_id)`` unique index enforces the same thing.
    """
    return (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.external_id == external_id,
            ).limit(1)
        )
    ).scalar_one_or_none()


async def _run_rules(
    session: AsyncSession,
    household_id: uuid.UUID,
    txn: Transaction,
    loaded: rules.LoadedRules | None,
) -> int:
    """Apply the household's rules; 1 if they changed anything, else 0.

    Runs on insert, on reconcile, and on an update that actually changed a value —
    the three cases where the row's content is new. A row matched by id and left
    identical skips it, so a re-sync is not a hook for rules created since the last
    one to rewrite history.
    """
    changed = await rules.apply_to_transaction(session, household_id, txn, loaded=loaded)
    return 1 if changed else 0


def _norm_description(value: str | None) -> str:
    """Lowercase, and drop everything that is not a letter or a digit.

    Apostrophes are removed rather than turned into spaces: the same merchant comes
    from the wire as both ``"John's Fishin Shack"`` (``payee``) and
    ``"JOHNS FISHIN SHACK BAIT"`` (``memo``), and a normaliser that split the first
    into ``john s`` would stop it matching the second.
    """
    return _WORDS.sub(" ", (value or "").replace("'", "").casefold()).strip()


def _descriptions_match(a: str | None, b: str | None) -> bool:
    """Loose on purpose — a posting often rewords the charge slightly.

    An absent description on either side is **not** evidence of a match; it is
    absence of evidence. The amount and the date still have to agree, and the
    candidate still has to be the only one. Treating it as a refusal instead would
    mean a provider that stops sending descriptions can never reconcile, and
    duplicates forever — which is the failure this whole path exists to prevent.
    """
    na, nb = _norm_description(a), _norm_description(b)
    if not na or not nb:
        return True
    return na == nb or na in nb or nb in na


async def _match_pending(
    session: AsyncSession, account: Account, ptxn: ProviderTransaction
) -> Transaction | None:
    """The one unsettled row this incoming transaction could be the posting of.

    **Exactly one candidate, or none.** Two plausible rows is the two-identical-
    coffees case ADR-0019 was written for, and there is no answer a program should
    pick: merging the wrong pair loses a real charge and rewrites another. So
    ambiguity inserts a visible duplicate instead, which a human can delete —
    the recoverable failure over the silent one.

    The amount comparison is exact. Money is ``NUMERIC(19,4)``, so equality here
    is exact decimal equality; a tolerance would merge two genuinely different
    charges of similar size, which is the same wrong-merge failure in slower
    motion.

    An incoming transaction that is **itself unsettled** never matches. Adoption
    means "the thing that was pending has now posted", and the incoming row has to
    be the posting for that sentence to be true. Two identical coffees bought on
    the same day are the case: both arrive pending, both look like the one already
    on file, and matching the second against the first would silently turn two
    purchases into one — the merge ADR-0019 exists to refuse. A still-pending row
    with an unfamiliar id is a new charge, so it is inserted and waits for its own
    posting.
    """
    if ptxn.is_pending:
        return None
    window = timedelta(days=SYNC_PENDING_MATCH_DAYS)
    rows = (
        await session.execute(
            select(Transaction)
            .where(
                Transaction.account_id == account.id,
                Transaction.is_pending.is_(True),
                # ADR-0019's manual-origin boundary, made structural: a manual or
                # CSV row has no external_id, so it cannot appear in this result at
                # all — which is what makes "sync never touches a row a human
                # typed" a fact about the query rather than a promise about its
                # callers.
                Transaction.external_id.is_not(None),
                Transaction.amount == ptxn.amount,
                Transaction.transacted_at >= ptxn.transacted_at - window,
                Transaction.transacted_at <= ptxn.transacted_at + window,
            )
            .limit(PENDING_CANDIDATE_LIMIT)
        )
    ).scalars().all()
    if len(rows) >= PENDING_CANDIDATE_LIMIT:
        # Too many same-amount unsettled rows to reason about. Stop here rather
        # than filter down to a "unique" survivor that is unique only because the
        # query was capped.
        return None
    matching = [row for row in rows if _descriptions_match(row.description, ptxn.description)]
    return matching[0] if len(matching) == 1 else None


def _new_transaction(
    household_id: uuid.UUID, account: Account, ptxn: ProviderTransaction, *, now: datetime
) -> Transaction:
    """A brand-new provider row, with its provenance already declared.

    Only fields the provider actually supplied are marked ``provider``: a source
    entry for a value that does not exist would claim an origin for nothing.
    ``merchant`` comes from ``payee`` rather than being left for a rule — the
    provider is handing us a structured merchant name and discarding it would be
    worse than useless (fixture README, correction 2). Marking it ``provider``
    still lets a rule improve it, because the engine's gate is "not user".
    """
    sources: dict[str, str] = {
        "amount": PROVIDER,
        "transacted_at": PROVIDER,
        "is_pending": PROVIDER,
    }
    if ptxn.posted_at is not None:
        sources["posted_at"] = PROVIDER
    if ptxn.description is not None:
        sources["description"] = PROVIDER
    if ptxn.payee is not None:
        sources["merchant"] = PROVIDER

    return Transaction(
        household_id=household_id,
        account_id=account.id,
        external_id=ptxn.external_id,
        amount=ptxn.amount,
        currency=account.currency,
        transacted_at=ptxn.transacted_at,
        posted_at=ptxn.posted_at,
        description=ptxn.description,
        merchant=ptxn.payee,
        is_pending=ptxn.is_pending,
        # Set once, and never refreshed while pending — otherwise the TTL slides
        # forward forever and a phantom never expires (Transaction.pending_since).
        pending_since=now if ptxn.is_pending else None,
        review_status="needs_review",
        source=SOURCE,
        field_sources=sources,
    )


async def _insert_transaction(
    session: AsyncSession,
    household_id: uuid.UUID,
    account: Account,
    ptxn: ProviderTransaction,
    *,
    now: datetime,
) -> Transaction:
    txn = _new_transaction(household_id, account, ptxn, now=now)
    txn.base_amount, txn.fx_rate_date = await txn_service.compute_base_amount(
        session, household_id,
        amount=ptxn.amount, currency=account.currency, on=ptxn.transacted_at.date(),
    )
    return txn


async def _apply_provider_fields(
    txn: Transaction, ptxn: ProviderTransaction, *, now: datetime
) -> set[str]:
    """Write the provider's values onto an existing row, honouring provenance.

    Returns the ``field_sources`` keys that actually changed — an empty set is
    "matched and identical", which is what makes a re-sync report zero updates.

    The gate is ADR-0007's precedence, ``user > rule > provider``: a field a human
    set is skipped, and so is one a rule set, because a rule's value is a decision
    someone made *about* this row and the provider's is merely the latest report.
    """
    sources = dict(txn.field_sources or {})

    def writable(name: str) -> bool:
        return sources.get(name) not in (rules.USER, rules.RULE)

    # `is_pending` is not independent state — it is `posted_at is None` in another
    # shape, which the aggregator's docstring argues for. So it inherits that
    # field's gate: whoever owns the posted time owns what it implies, and the two
    # cannot be left disagreeing.
    posted_writable = writable("posted_at")

    values: dict[str, Any] = {
        "amount": ptxn.amount,
        "transacted_at": ptxn.transacted_at,
        "posted_at": ptxn.posted_at,
        "is_pending": ptxn.is_pending,
        "description": ptxn.description,
        "merchant": ptxn.payee,
    }

    changed: set[str] = set()
    for name, value in values.items():
        if name == "is_pending" and not posted_writable:
            continue
        if not writable(name):
            continue
        if value is None and name in _SET_ONLY:
            continue
        attr = _FIELD_ATTR[name]
        if getattr(txn, attr) == value:
            continue
        setattr(txn, attr, value)
        sources[name] = PROVIDER
        changed.add(name)

    txn.field_sources = sources
    if posted_writable:
        if ptxn.is_pending:
            if txn.pending_since is None:
                txn.pending_since = now
        else:
            txn.pending_since = None
    return changed


# ---- expiry ---------------------------------------------------------------


async def expire_pendings(
    session: AsyncSession,
    account_ids: Sequence[uuid.UUID],
    *,
    seen: set[tuple[uuid.UUID, str]],
    now: datetime,
    log: RunLog,
) -> int:
    """Resolve unsettled rows this payload never mentioned and never settled.

    A pending charge that neither posts nor reappears is a phantom — the bank
    dropped it, or re-minted its id and it was matched elsewhere — and leaving it
    unsettled inflates the window and the "pending" badge forever.

    What to do about it depends on whether a human has been here. A row nobody has
    touched is deleted: it is not real. A row a human has categorized, split or
    annotated is **kept**, un-pended and pushed to the review queue, because
    deleting work someone did is the data loss ADR-0007/0019 exist to prevent and
    the cost of keeping it is one review row. ARCHITECTURE.md:283 says "so no
    phantom row lingers" without distinguishing the two; this distinguishes them.

    ``seen`` is keyed by ``(account_id, external_id)`` and not by id alone, for the
    same reason ``_by_external_id`` is: the provider reuses ids across accounts.
    """
    if not account_ids:
        return 0
    cutoff = now - timedelta(days=PENDING_TTL_DAYS)
    rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id.in_(account_ids),
                Transaction.is_pending.is_(True),
                Transaction.pending_since.is_not(None),
                Transaction.pending_since < cutoff,
                Transaction.external_id.is_not(None),
            )
        )
    ).scalars().all()

    resolved = 0
    for row in rows:
        if (row.account_id, row.external_id) in seen:
            continue  # still unsettled and still being reported; not a phantom
        if rules.USER in (row.field_sources or {}).values():
            row.is_pending = False
            row.pending_since = None
            row.review_status = "needs_review"
            await log.emit(
                "warning",
                "pending.expired_kept",
                transaction_id=str(row.id),
                account_id=str(row.account_id),
                reason="a human had edited this row; kept and sent to review",
            )
        else:
            await session.delete(row)
            await log.emit(
                "warning",
                "pending.expired",
                account_id=str(row.account_id),
                reason="never posted and no longer reported",
            )
        resolved += 1
    if resolved:
        await session.flush()
    return resolved


# ---- the run --------------------------------------------------------------


@dataclass(slots=True)
class RunCounts:
    """``sync_runs``' counters, carried together so a caller cannot fill half."""

    accounts_seen: int = 0
    accounts_created: int = 0
    accounts_remapped: int = 0
    #: Rows re-pointed at the ids a remapped account's payload uses. Its own
    #: counter rather than folded into ``txns_reconciled``: a re-key moves no
    #: value, so a run that reports it as a change would be claiming work it did
    #: not do — and on a reconnect it is the number that says the history came
    #: through, which is exactly what the M2 bar asks about.
    txns_rekeyed: int = 0
    txns_inserted: int = 0
    txns_updated: int = 0
    txns_reconciled: int = 0
    pendings_expired: int = 0
    transfers_matched: int = 0
    rules_applied: int = 0


@dataclass(slots=True)
class SyncOutcome:
    """How one run ended, for the worker and for the caller that asked for it."""

    status: str  # ok | partial | error | cancelled
    run_id: uuid.UUID | None = None
    error: str | None = None
    counts: RunCounts = field(default_factory=RunCounts)


def classify_errlist(errlist: Sequence[str]) -> tuple[str, str | None]:
    """``errlist`` → (run status, connection health or ``None``).

    The bridge reports per-account and per-request trouble on a **200**, so this is
    an ordinary path, not an error path. Two rules, and both matter:

    * ``con.*`` is about a *connection* — real, persistent, and worth showing as
      broken. ``con.auth`` in particular means the credential is dead, and
      ``auth_error`` is what puts a working "Reconnect" button in front of the user.
    * Everything else (``gen.*`` request warnings, ``act.*`` account warnings,
      ``app.parse`` entries from our own parser) is about *this payload*, and maps
      to ``partial``: the run produced usable data and something was off about it.
      Mapping these to a connection failure would mark a healthy bank broken
      because our date range was long.

    The code prefix decides, never the message text: the wording of every
    ``con.*`` code is unverified, because the demo bridge will not produce one.
    """
    if not errlist:
        return "ok", None
    run_status = "partial"
    connection_status: str | None = None
    for entry in errlist:
        code = entry.split(":", 1)[0].strip()
        if not code.startswith("con."):
            continue
        run_status = "error"
        # Ordered so the more specific diagnosis wins: a payload carrying both an
        # auth failure and some other connection complaint is an auth failure, and
        # that is the one that offers the user a fix.
        if code == "con.auth":
            connection_status = "auth_error"
        elif connection_status is None:
            connection_status = "error"
    return run_status, connection_status


async def _account_keys(
    session: AsyncSession, accounts: Sequence[ProviderAccount], *, log: RunLog
) -> dict[str, str]:
    """The ``external_key`` each provider account in one payload lands under.

    ADR-0009's key is institution + name, so two cards a bank names alike ("Blue
    Cash") compute the same key, and sync used to land both on **one** ledger row:
    their transactions merged and the row's balance alternated between the two
    cards depending on payload order. Within one payload the provider's ids tell
    them apart, so a colliding account gets ``key:<provider id>`` — its own row,
    at the cost of reconnect-by-name for that account (a re-claim re-mints ids).

    **An existing row is not split.** The account the row already belongs to —
    its provider id, or any of its transactions already on the row (the merged
    case) — keeps the plain key; splitting a merged row would start the second
    card's history partway through while the row kept both. That is reported
    (``account.key_collision``) for a person to separate, not guessed apart.

    **Nor is a row nobody in the payload owns.** That is a reconnect whose ids
    were re-minted: the row is the household's history of one of these cards, and
    giving both new keys would insert two accounts beside it while it carried its
    last balance forward — a double count. They keep the plain key, as before this
    existed, and it is reported the same way.
    """
    keys = {pa.external_id: external_key_for(pa) for pa in accounts}
    groups: dict[str, list[ProviderAccount]] = {}
    for pa in accounts:
        groups.setdefault(keys[pa.external_id], []).append(pa)
    for key, group in groups.items():
        if len(group) < 2:
            continue
        row = (
            await session.execute(select(Account).where(Account.external_key == key).limit(1))
        ).scalar_one_or_none()
        on_row: set[str] = set()
        if row is not None:
            on_row = set(
                (
                    await session.execute(
                        select(Transaction.external_id).where(
                            Transaction.account_id == row.id,
                            Transaction.external_id.is_not(None),
                        )
                    )
                ).scalars()
            )
        keeps = [
            pa
            for pa in group
            if row is not None
            and (
                row.external_id == pa.external_id
                or any(t.external_id in on_row for t in pa.transactions)
            )
        ]
        if row is not None and not keeps:
            keeps = list(group)
        for pa in group:
            if pa not in keeps:
                keys[pa.external_id] = f"{key}:{pa.external_id}"
        await log.emit(
            "warning",
            "account.key_collision",
            key=key,
            names=[pa.name for pa in group],
            merged=len(keeps) > 1,
            reason=(
                "these accounts share one ledger row from before; separate them by hand"
                if len(keeps) > 1
                else "same institution and name; each is kept as its own account"
            ),
        )
    return keys


async def _report_unreported_accounts(
    session: AsyncSession,
    connection: AccountConnection,
    reported: list[uuid.UUID],
    *,
    log: RunLog,
) -> None:
    """Say which of this connection's accounts the fetch did not include.

    A bank stops reporting a closed account, or the bridge drops one. Its last
    balance is then carried forward on the chart indefinitely, which is right
    while it is the last thing known and wrong once the account is gone — and only
    a person can say which. So it is said, every run: the Accounts page marks the
    account stale (``ledger.stale_since``), and hiding or closing it stays theirs.
    """
    rows = (
        await session.execute(
            select(Account.id, Account.name).where(
                Account.connection_id == connection.id,
                Account.id.not_in(reported) if reported else true(),
            )
        )
    ).all()
    for account_id, name in rows:
        await log.emit(
            "warning",
            "account.not_reported",
            account_id=str(account_id),
            name=name,
            reason="this fetch did not include the account; its last balance is "
            "carried forward until it is reported again, hidden or closed",
        )


async def ingest_account_set(
    session: AsyncSession,
    household_id: uuid.UUID,
    connection: AccountConnection,
    account_set: AccountSet,
    *,
    now: datetime,
    log: RunLog,
) -> RunCounts:
    """Land one fetch's worth of accounts and transactions.

    Order matters in exactly one place: the transfer auto-match runs **after** the
    whole payload has been ingested, over the rows this run touched. Inside the
    loop it would be useless — two legs of one transfer usually arrive in the same
    payload, and the first would be examined before the second existed.
    """
    counts = RunCounts()
    loaded = await rules.load_rules(session)
    touched: list[uuid.UUID] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    account_ids: list[uuid.UUID] = []
    keys = await _account_keys(session, account_set.accounts, log=log)

    for pa in account_set.accounts:
        counts.accounts_seen += 1
        upsert = await upsert_account(
            session, household_id, connection, pa, key=keys[pa.external_id]
        )
        account = upsert.account
        account_ids.append(account.id)
        if upsert.created:
            counts.accounts_created += 1
            await log.emit(
                "info",
                "account.created",
                account_id=str(account.id),
                name=pa.name,
                account_type=account.type,
            )
        if upsert.remapped:
            counts.accounts_remapped += 1
            await log.emit(
                "info",
                "account.remapped",
                account_id=str(account.id),
                provider_account_id=pa.external_id,
            )
            # Before anything is ingested, so the loop below still finds every row
            # by id — the reconciliation is a migration between two id schemes,
            # not a second way of landing a transaction.
            renamed = await _rekey_remapped_account(session, account, pa)
            if renamed:
                counts.txns_rekeyed += renamed
                await log.emit(
                    "info",
                    "account.rekeyed",
                    account_id=str(account.id),
                    count=renamed,
                )
        if upsert.contested:
            await log.emit(
                "warning",
                "account.contested",
                account_id=str(account.id),
                name=pa.name,
                reason="another live connection reports this same account; "
                "the ledger row follows whichever connection synced last",
            )

        for ptxn in pa.transactions:
            seen.add((account.id, ptxn.external_id))
            outcome = await ingest_transaction(
                session, household_id, account, ptxn, loaded=loaded, now=now
            )
            touched.append(outcome.txn.id)
            counts.rules_applied += outcome.rules_applied
            if outcome.action == "inserted":
                counts.txns_inserted += 1
            elif outcome.action == "updated":
                counts.txns_updated += 1
            elif outcome.action == "reconciled":
                counts.txns_reconciled += 1
                await log.emit(
                    "info",
                    "transaction.reconciled",
                    transaction_id=str(outcome.txn.id),
                    account_id=str(account.id),
                )

        await _apply_balance(session, account, pa, log=log, now=now)

    await _report_unreported_accounts(session, connection, account_ids, log=log)

    counts.pendings_expired = await expire_pendings(
        session, account_ids, seen=seen, now=now, log=log
    )
    counts.transfers_matched = await txn_service.auto_match_transfers(
        session, household_id, touched
    )
    if counts.transfers_matched:
        await log.emit("info", "transfers.matched", count=counts.transfers_matched)
    return counts


async def _get_connection(
    session: AsyncSession, connection_id: uuid.UUID
) -> AccountConnection:
    connection = (
        await session.execute(
            select(AccountConnection).where(AccountConnection.id == connection_id)
        )
    ).scalar_one_or_none()
    if connection is None:
        raise LedgerError("Connection not found", 404)
    return connection


def _access_url(connection: AccountConnection) -> str:
    """Decrypt the stored credential, or explain why there is none.

    The plaintext exists for as long as the fetch call does and is never bound to
    a log context, a structlog contextvar, or an exception (ADR-0016).
    """
    if not connection.access_url_encrypted:
        raise LedgerError(
            "This connection has no stored credential; reconnect it", 409
        )
    try:
        return SecretBox(get_settings().secret_key).decrypt(connection.access_url_encrypted)
    except ValueError:
        # Wrong key or corrupt ciphertext. Both are fixed the same way, and the
        # message must not quote the ciphertext — it is the credential's envelope.
        raise LedgerError(
            "The stored credential could not be decrypted; reconnect this connection",
            409,
        ) from None


async def _finish_failed(
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    connection_id: uuid.UUID,
    message: str,
    *,
    moment: datetime,
    kind: str = "transient",
    http_status: int | None = None,
) -> SyncOutcome:
    """Close a failed run in a **fresh** transaction (TX3).

    Fresh, and that is the point: the failure may have happened mid-ingest, where
    the transaction holding the run row is about to roll back — and the run row's
    own error message would roll back with it. The run would then sit ``running``
    forever with nothing saying why, which is precisely the state the dashboard
    exists to make impossible.

    Connection health follows the error's *kind*, not its existence. ``auth`` is
    the user's to fix and becomes ``auth_error`` so the UI offers Reconnect;
    ``payment`` is real and persistent and becomes ``error``; anything transient
    or our own fault leaves the health alone and only records ``last_error`` —
    a 5xx from the bridge is our problem and must not mark a working connection
    broken.
    """
    safe = sanitize(message)
    trouble: notifications.Trouble | None = None
    async with scoped_session(household_id) as session:
        log = RunLog(session, household_id, run_id)
        await log.emit("error", "run.failed", error=safe, kind=kind, http_status=http_status)
        await _close_run(
            session, run_id, status="error", now=moment, error=safe, http_status=http_status
        )
        connection = await _get_connection(session, connection_id)
        connection.last_error = safe
        broke = False
        if kind == "auth":
            connection.status = "auth_error"
            broke = True
        elif kind == "payment":
            connection.status = "error"
            broke = True
        # The cadence clock moves even on failure — otherwise the tick re-enqueues
        # this connection every minute against a bank that is down (see
        # AccountConnection.next_sync_at). last_synced_at deliberately does not:
        # it means "last successful sync", and a failure is not one.
        connection.next_sync_at = moment + timedelta(seconds=backoff_seconds(1))
        if broke:
            # A connection that has just broken is the one thing worth telling
            # somebody about out of band. A *transient* failure is not: it is our
            # problem, it retries on its own, and the status column was left alone
            # precisely because the connection is not what is wrong.
            #
            # Decided here, delivered below — reading the connection's history
            # belongs to this transaction; the webhook call does not, because a
            # webhook that hangs must not hold this run's row locks.
            trouble = await notifications.trouble_for(
                session, connection=connection, run_id=run_id,
                status=connection.status, message=safe, now=moment,
            )
            if trouble is not None:
                # Recorded where it was decided, in this run's own transaction
                # (ADR-0037). The record *is* the in-app notice — the browser
                # cannot be pushed to, so it polls for these rows — and it has to
                # commit with the run it belongs to: a notice that was only in
                # this process's memory when it died was never sent, and the run
                # it would have been filed under is the run that is now gone.
                await log.emit("info", notifications.NOTIFIED_EVENT, **trouble.notice())
    if trouble is not None:
        await notifications.deliver(trouble)
    return SyncOutcome(status="error", run_id=run_id, error=safe)


async def _close_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: str,
    now: datetime,
    counts: RunCounts | None = None,
    error: str | None = None,
    account_set: AccountSet | None = None,
    connection_label: str | None = None,
    http_status: int | None = None,
) -> None:
    run = (
        await session.execute(select(SyncRun).where(SyncRun.id == run_id))
    ).scalar_one()
    run.status = status
    run.finished_at = now
    if run.started_at is not None:
        run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1000))
    run.error = error
    if counts is not None:
        run.accounts_seen = counts.accounts_seen
        run.accounts_created = counts.accounts_created
        run.accounts_remapped = counts.accounts_remapped
        run.txns_rekeyed = counts.txns_rekeyed
        run.txns_inserted = counts.txns_inserted
        run.txns_updated = counts.txns_updated
        run.txns_reconciled = counts.txns_reconciled
        run.pendings_expired = counts.pendings_expired
        run.transfers_matched = counts.transfers_matched
        run.rules_applied = counts.rules_applied
    if connection_label is not None:
        # Overwrites the value TX1 wrote, which was whatever the connection knew
        # when the run opened — possibly nothing at all, on a connection's first
        # sync. This one comes from the payload the run actually fetched, and the
        # payload is the authority on what the institution is called; keeping the
        # older value would label the run that *discovered* a rename with the name
        # it just disproved, and the name would only catch up on the following run.
        #
        # The TX1 value is not redundant: it is what a run that *fails* keeps,
        # because a failure has no payload to take a name from.
        run.connection_label = connection_label
    if account_set is not None and account_set.stats is not None:
        run.http_ms = account_set.stats.http_ms
        run.bytes_fetched = account_set.stats.bytes_fetched
        # The whole of ``FetchStats``, not two thirds of it. Leaving the status out
        # is invisible in a passing suite — the panel renders the chip only when the
        # column is set (`Admin.tsx`), so a missing 200 reads as "nothing to say"
        # rather than as a gap — and it is the one number that distinguishes a
        # healthy fetch from a fetch that never happened.
        run.http_status = account_set.stats.http_status
    if http_status is not None:
        # The explicit argument wins: it is the *failure* status, passed by a caller
        # that has no ``AccountSet`` to read one from.
        run.http_status = http_status


async def run_connection_sync(
    household_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    trigger: str = "cron",
    job_id: uuid.UUID | None = None,
    provider: AggregatorProvider | None = None,
    fence: Fence | None = None,
    now: datetime | None = None,
) -> SyncOutcome:
    """Sync one connection, once. The engine's whole public surface.

    Three transactions around one HTTP call:

    * **TX1** — write the run row, resolve the provider, decrypt the credential,
      compute the window. Commits before the fetch, so the run is already on
      record while it is in flight.
    * ***(no transaction)* — the fetch.**
    * **TX2** — fence, ingest, close the run and the connection's health together.
    * **TX3** — on failure, a fresh transaction to write the error. See
      ``_finish_failed``.

    ``fence`` is the worker's hook (see ``Fence``); a test or a direct caller
    passes none, and the run is then unfenced, which is correct — there is no job
    to be fenced against.

    ``job_id`` links the run to the queue row that asked for it, and it is written
    in **TX1** for the same reason the run row itself is: the reaper needs to find
    this run if the worker dies holding it. A run whose row only acquired its
    ``job_id`` at the end would be invisible to ``jobs.reap_stale_jobs`` for
    exactly as long as it was stuck, which is the whole of the case it exists for.
    A direct caller (a test, a script) passes none and gets an unlinked run, which
    is what the API's manual trigger already produces.
    """
    moment = now or datetime.now(UTC)
    window_start: datetime | None = None
    access_url = ""
    resolved = provider

    # --- TX1: the run row, the provider, the credential, the window ---------
    async with scoped_session(household_id) as session:
        try:
            connection = await _get_connection(session, connection_id)
        except LedgerError as exc:
            # No run row exists and none can be written — there is no connection to
            # hang one off. The caller reports this; the worker's own log sees it.
            return SyncOutcome(status="error", error=exc.message)
        run = SyncRun(
            household_id=household_id,
            connection_id=connection.id,
            job_id=job_id,
            connection_label=connection.org_name,
            trigger=trigger,
            status="running",
            # Explicit rather than left to the column's server default: this value
            # is read back for duration_ms, and a default that only the database
            # knows would need a refresh to see.
            started_at=moment,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

        setup_error: str | None = None
        setup_kind = "transient"
        try:
            resolved = resolved or get_provider(connection.provider)
            access_url = _access_url(connection)
        except LedgerError as exc:
            setup_error, setup_kind = exc.message, "auth"
        except (ProviderError, RuntimeError, ValueError) as exc:
            # A provider that cannot be resolved (unknown name, or the fake refused
            # outside test/dev) is a configuration fault, not a bank's. It is not
            # the connection's fault either, so the health column is left alone.
            setup_error = str(exc)
        else:
            window = await compute_window(session, connection.id, now=moment)
            window_start = window.start
            await RunLog(session, household_id, run_id).emit(
                "info",
                "window.computed",
                start=window.start,
                days=(moment - window.start).days,
                first_sync=window.first_sync,
                pending_anchors=window.pending_anchors,
            )

    if setup_error is not None:
        return await _finish_failed(
            household_id, run_id, connection_id, setup_error,
            moment=moment, kind=setup_kind,
        )

    # --- no transaction: the fetch ------------------------------------------
    # Nothing logs this call's exception object. `httpx.HTTPStatusError`
    # stringifies the request it failed on, credentials included, so the only
    # safe thing to do with one is let `SimpleFinProvider` catch it and hand back
    # a `ProviderError`, whose message was sanitized in its constructor.
    try:
        account_set = await resolved.fetch_accounts(access_url, start=window_start)
    except ProviderError as exc:
        return await _finish_failed(
            household_id, run_id, connection_id, exc.message,
            moment=moment, kind=exc.kind, http_status=exc.status,
        )

    # --- TX2: fence, ingest, close ------------------------------------------
    # The `try` wraps the `async with` rather than sitting inside it, so a failure
    # leaves the block by *exception* and the transaction rolls back. Catching it
    # inside would exit the block normally, committing a half-finished ingest —
    # and would open `_finish_failed`'s fresh session while this one still held the
    # run row locked.
    cancelled = False
    counts = RunCounts()
    status = "ok"
    # Built inside the transaction (it reads this connection's run history) and
    # sent after it commits, so the webhook is never in the transaction's way.
    trouble: notifications.Trouble | None = None
    try:
        async with scoped_session(household_id) as session:
            if fence is not None and not await fence(session):
                await _close_run(session, run_id, status="cancelled", now=moment)
                await RunLog(session, household_id, run_id).emit(
                    "warning", "run.cancelled", reason="cancelled or reaped during the fetch"
                )
                cancelled = True
            else:
                connection = await _get_connection(session, connection_id)
                log = RunLog(session, household_id, run_id)
                for warning in account_set.errlist:
                    await log.emit("warning", "provider.errmessage", message=warning)

                status, connection_status = classify_errlist(account_set.errlist)
                counts = await ingest_account_set(
                    session, household_id, connection, account_set, now=moment, log=log
                )
                await log.emit("info", "run.finished", status=status, **asdict(counts))

                # The institution's name is only knowable from a payload — the
                # claim endpoint is handed a token and a URL, and neither names a
                # bank — so this is where the connection learns what it is, and it
                # keeps up to date if the bridge ever calls the institution
                # something else.
                #
                # Learned *before* the run closes, deliberately. This run was
                # opened in an earlier transaction, when nothing yet knew the name,
                # so its ``connection_label`` is NULL at that point; and since a
                # run's ``connection_id`` is SET NULL on disconnect, that label is
                # the only thing that still says where those transactions came
                # from. Leaving it until after the close would strand the one run a
                # person is looking at right after pasting a token. The label is
                # written once and never revised (``_close_run``), so a rename here
                # changes the *next* run's label and never relabels history.
                if account_set.org_name:
                    connection.org_name = account_set.org_name

                await _close_run(
                    session, run_id, status=status, now=moment, counts=counts,
                    account_set=account_set, connection_label=connection.org_name,
                )

                connection.last_synced_at = moment
                connection.next_sync_at = (
                    moment + timedelta(minutes=connection.sync_interval_minutes)
                )
                if connection_status is not None:
                    connection.status = connection_status
                    connection.last_error = sanitize("; ".join(account_set.errlist))
                    # The errlist said something about the *connection*, on a 200.
                    # Same rule as a failed fetch: news is worth a notification,
                    # a repeat is not.
                    trouble = await notifications.trouble_for(
                        session, connection=connection, run_id=run_id,
                        status=connection_status, message=connection.last_error, now=moment,
                    )
                    # Recorded here for the same reason as in `_finish_failed`:
                    # this is the transaction that decides, and the row must
                    # commit with it (ADR-0037).
                    if trouble is not None:
                        await log.emit("info", notifications.NOTIFIED_EVENT, **trouble.notice())
                else:
                    # A run that reached the bank without a connection-level
                    # complaint clears the previous one: the health column
                    # describes the connection *now*, not its worst day.
                    connection.status = "ok"
                    connection.last_error = None
    except ProviderError as exc:
        return await _finish_failed(
            household_id, run_id, connection_id, exc.message,
            moment=moment, kind=exc.kind, http_status=exc.status,
        )

    if trouble is not None:
        await notifications.deliver(trouble)

    if cancelled:
        return SyncOutcome(status="cancelled", run_id=run_id)
    return SyncOutcome(status=status, run_id=run_id, counts=counts)


__all__ = [
    "FIRST_SYNC_WINDOW_DAYS",
    "MAX_LOOKBACK_DAYS",
    "OVERLAP_DAYS",
    "PENDING_TTL_DAYS",
    "SYNC_PENDING_MATCH_DAYS",
    "AccountUpsert",
    "Fence",
    "IngestOutcome",
    "RunCounts",
    "RunLog",
    "SyncOutcome",
    "Window",
    "backoff_seconds",
    "classify_errlist",
    "compute_window",
    "expire_pendings",
    "ingest_account_set",
    "ingest_transaction",
    "run_connection_sync",
    "upsert_account",
]
