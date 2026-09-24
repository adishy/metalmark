"""Connection lifecycle: claim, pause, retune, disconnect, and what ran.

The *control* half of sync. ``services/sync.py`` executes one connection and
knows nothing about who asked; this module owns the row's lifecycle and the
queue that decides who asks — which is why the two never import each other's
internals.

Three things here are load-bearing beyond their obvious job:

* **The access URL is a secret in transit.** It arrives from the provider, goes
  straight into ``SecretBox``, and is never returned, logged, or bound to a log
  context. ``ConnectionOut`` has no field for it at all — not a null one, not an
  excluded one, which is the only shape that survives someone later adding
  ``model_dump()`` to a response by hand.
* **Disconnect keeps the history.** ``accounts.connection_id`` is ``ON DELETE SET
  NULL``, so the transactions survive the connection by default — but nothing
  would then fill the column back in, and the account would read as one nobody
  ever synced. So the rows are marked ``is_manual`` first, which is what
  ARCHITECTURE §2 describes and what makes the ledger's decoupling from
  connections real rather than nominal (ADR-0009).
* **Pausing is not the same as being broken.** ``is_enabled`` is the scheduling
  gate and ``status`` is the health enum; a paused connection keeps its last
  health, so the dashboard can distinguish "I turned this off" from "the bank
  revoked us", which is the difference between two different buttons.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, AccountConnection, SyncJob, SyncRun, SyncRunEvent
from app.models.ledger import (
    SYNC_INTERVAL_MAX_MINUTES,
    SYNC_INTERVAL_MIN_MINUTES,
)
from app.schemas.connections import ConnectionUpdate
from app.security.crypto import SecretBox
from app.services.aggregator import AggregatorProvider, ProviderError, get_provider
from app.services.errors import LedgerError
from app.settings import get_settings

#: How many runs the dashboard's history shows by default. A sync runs a handful
#: of times a day, so this is weeks of history — more than anyone scrolls, and
#: small enough that the count query stays off the dashboard's critical path.
DEFAULT_RUN_LIMIT = 50
MAX_RUN_LIMIT = 200


async def get_connection(
    session: AsyncSession, connection_id: uuid.UUID
) -> AccountConnection:
    """The connection, or a 404. RLS means "another household's" reads as absent."""
    connection = (
        await session.execute(
            select(AccountConnection).where(AccountConnection.id == connection_id).limit(1)
        )
    ).scalar_one_or_none()
    if connection is None:
        raise LedgerError("Connection not found", 404)
    return connection


async def list_connections(session: AsyncSession) -> list[AccountConnection]:
    return list(
        (
            await session.execute(
                select(AccountConnection).order_by(AccountConnection.created_at)
            )
        ).scalars().all()
    )


async def claim(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    setup_token: str,
    provider: AggregatorProvider | None = None,
    now: datetime | None = None,
) -> AccountConnection:
    """Trade a setup token for a connection row, credential encrypted at rest.

    ``provider`` defaults to whatever ``METALMARK_SIMPLEFIN_PROVIDER`` names —
    the only place in the backend that has to guess, because a connection that
    does not exist yet cannot say which provider it is. Everywhere else the
    choice is read off the row (``aggregator.get_provider``).

    A failed claim writes **nothing**. There is no half-connection to clean up
    and no row whose status has to be interpreted: the household either has a
    credential or it does not.

    ``org_name`` is left NULL and filled in by the first successful fetch, which
    is the first moment anything actually knows it. Guessing it from the token
    would be inventing data for a column the dashboard displays as the
    institution's name.
    """
    resolved = provider if provider is not None else get_provider(
        get_settings().simplefin_provider
    )
    try:
        result = await resolved.claim(setup_token)
    except ProviderError as exc:
        # ``exc.message`` was sanitized in the constructor — the setup token and
        # the claim URL are both registered as secrets there, so this string is
        # safe to hand to a client and safe to store in ``last_error``.
        raise LedgerError(exc.message, _claim_status(exc)) from exc

    moment = now or datetime.now(UTC)
    box = SecretBox(get_settings().secret_key)
    connection = AccountConnection(
        household_id=household_id,
        provider=resolved.name,
        access_url_encrypted=box.encrypt(result.access_url),
        org_name=None,
        status="ok",
        # Due immediately: the user who just pasted a token is watching a screen
        # that says nothing has synced yet, and waiting up to an interval to
        # change that is the wrong first impression.
        next_sync_at=moment,
    )
    session.add(connection)
    await session.flush()
    return connection


def _claim_status(exc: ProviderError) -> int:
    """A claim failure's HTTP status, chosen so the UI's advice is actionable.

    A used token is a **400**, not a 403: the request is well-formed and the
    caller is allowed to make it — the token itself is spent, and re-sending it
    will fail identically forever. Saying so plainly is what puts "generate a new
    one" in front of the user instead of "you are not allowed to do this".
    """
    if exc.status == 403:
        return 400
    if exc.status == 402:
        return 402
    # A 3xx is the same shape as a spent token, and belongs in the same bucket: the
    # token names an address the bridge no longer serves, so another one is the fix
    # and re-sending this one cannot ever work. Without this it falls to the 502
    # below — which reads as "the server is briefly broken", i.e. retry — and the
    # user retries a request that is guaranteed to fail identically.
    if exc.status is not None and 300 <= exc.status < 400:
        return 400
    return 502


async def update_connection(
    session: AsyncSession, connection_id: uuid.UUID, data: ConnectionUpdate
) -> AccountConnection:
    """Pause/resume, and retune the cadence. Nothing else is editable.

    ``next_sync_at`` is recomputed only when the interval actually changes and the
    connection is enabled: resuming from a long pause must not leave the next run
    scheduled for a time that has already passed, which is the one edit that would
    otherwise make the dashboard's "next run" column a lie.
    """
    connection = await get_connection(session, connection_id)
    now = datetime.now(UTC)

    if data.is_enabled is not None and data.is_enabled != connection.is_enabled:
        connection.is_enabled = data.is_enabled
        if data.is_enabled and (
            connection.next_sync_at is None or connection.next_sync_at < now
        ):
            connection.next_sync_at = now

    if data.sync_interval_minutes is not None:
        if not (
            SYNC_INTERVAL_MIN_MINUTES
            <= data.sync_interval_minutes
            <= SYNC_INTERVAL_MAX_MINUTES
        ):
            # The CHECK constraint would catch this too, as a 500 rather than a
            # 422. A range error is a client's mistake and should read like one.
            raise LedgerError(
                "The sync interval must be between "
                f"{SYNC_INTERVAL_MIN_MINUTES} and {SYNC_INTERVAL_MAX_MINUTES} minutes",
                422,
            )
        if data.sync_interval_minutes != connection.sync_interval_minutes:
            connection.sync_interval_minutes = data.sync_interval_minutes
            if connection.is_enabled:
                connection.next_sync_at = now + timedelta(
                    minutes=data.sync_interval_minutes
                )

    await session.flush()
    return connection


async def delete_connection(session: AsyncSession, connection_id: uuid.UUID) -> None:
    """Disconnect, keeping every account and transaction it brought in.

    The FK is ``ON DELETE SET NULL``, so the rows survive on their own; what it
    cannot do is decide that they are now *manual* — that is a fact about how
    they will be treated, not about a foreign key, and ARCHITECTURE §2 leaves it
    to this function. Without it, an account whose connection is gone keeps
    ``is_manual = False`` and reads as a synced account that no connection
    feeds, which is exactly the state the ledger's decoupling exists to avoid.

    The queue goes with it: ``sync_jobs.connection_id`` cascades, so a queued
    job for a deleted connection cannot outlive it. Runs do **not** cascade —
    their ``connection_id`` is SET NULL and ``connection_label`` carries the
    institution's name forward, so the history still says where it came from.
    """
    connection = await get_connection(session, connection_id)
    await session.execute(
        update(Account)
        .where(Account.connection_id == connection.id)
        .values(is_manual=True, connection_id=None)
    )
    await session.delete(connection)
    await session.flush()


# ---- the queue ------------------------------------------------------------


async def enqueue_sync(
    session: AsyncSession,
    household_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    trigger: str = "manual",
    requested_by: uuid.UUID | None = None,
    now: datetime | None = None,
) -> SyncJob:
    """Queue one run of one connection. The worker does the running.

    Refused while paused, with a 409 that says which control to use. The
    alternative — quietly syncing a connection the user turned off — is the one
    behaviour that would make the pause button untrustworthy, and "the button
    worked but the app did it anyway" is worse than an error.

    **Allowed on a broken connection.** ``auth_error`` means the credential the
    *last* run used was refused; the user may have just re-authorised at the
    bridge, and "Sync now" is precisely how they would check. Refusing there
    would make the one button that can confirm a fix refuse to run.
    """
    connection = await get_connection(session, connection_id)
    if not connection.is_enabled:
        raise LedgerError(
            "This connection is paused. Resume it to sync.", 409
        )

    moment = now or datetime.now(UTC)
    job = SyncJob(
        household_id=household_id,
        connection_id=connection.id,
        requested_by=requested_by,
        trigger=trigger,
        status="queued",
        not_before=moment,
    )
    session.add(job)
    await session.flush()
    return job


async def list_jobs(session: AsyncSession, *, active_only: bool = False) -> list[SyncJob]:
    """Queued and running jobs, newest first — the dashboard's live table."""
    query = select(SyncJob).order_by(SyncJob.created_at.desc())
    if active_only:
        query = query.where(SyncJob.status.in_(("queued", "running")))
    return list((await session.execute(query)).scalars().all())


async def cancel_job(
    session: AsyncSession, job_id: uuid.UUID, *, now: datetime | None = None
) -> SyncJob:
    """Cancel a job, whether it is waiting or already executing.

    A **database write, not a signal.** The worker is a different process and
    there is no channel to interrupt an in-flight HTTP call, so cancel does the
    only thing that is both immediate and safe: it marks the job terminal and
    **bumps the claim token**. Every write the worker makes is fenced on that
    token, so a worker that was mid-fetch when this landed finds its token gone
    and discards its results instead of resurrecting a job the user has already
    been told is over.

    Runs do not need the same treatment: the worker's fence check closes the run
    as ``cancelled`` on its way out, so the history stays honest about a run that
    really did happen.

    Cancelling something already finished is a 409 rather than a silent success —
    the dashboard must be able to say "already finished" instead of showing a
    cancel that did nothing.
    """
    job = (
        await session.execute(select(SyncJob).where(SyncJob.id == job_id).limit(1))
    ).scalar_one_or_none()
    if job is None:
        raise LedgerError("Job not found", 404)
    if job.status not in ("queued", "running"):
        raise LedgerError(f"This job already finished ({job.status})", 409)

    moment = now or datetime.now(UTC)
    job.status = "cancelled"
    job.finished_at = moment
    job.cancel_requested_at = moment
    # The fence. A fresh token can never match the one the worker holds, so every
    # fenced write it attempts from here touches zero rows.
    job.claim_token = uuid.uuid4()
    await session.flush()
    return job


# ---- what ran -------------------------------------------------------------


async def list_runs(
    session: AsyncSession,
    *,
    connection_id: uuid.UUID | None = None,
    limit: int = DEFAULT_RUN_LIMIT,
) -> list[SyncRun]:
    """Recent runs, newest first, optionally for one connection."""
    bounded = max(1, min(limit, MAX_RUN_LIMIT))
    query = select(SyncRun).order_by(SyncRun.started_at.desc()).limit(bounded)
    if connection_id is not None:
        query = query.where(SyncRun.connection_id == connection_id)
    return list((await session.execute(query)).scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> SyncRun:
    run = (
        await session.execute(select(SyncRun).where(SyncRun.id == run_id).limit(1))
    ).scalar_one_or_none()
    if run is None:
        raise LedgerError("Sync run not found", 404)
    return run


async def list_run_events(session: AsyncSession, run_id: uuid.UUID) -> list[SyncRunEvent]:
    """One run's log, in the order it was written.

    Ordered by ``seq``, never by ``ts``: Postgres ``now()`` is the *transaction*
    timestamp, so every event written in one ingest transaction shares a ``ts``
    exactly, and ordering by it would let the dashboard's feed shuffle. ``seq``
    exists for this (``models/sync.py``), and the run writes roll back from it.
    """
    return list(
        (
            await session.execute(
                select(SyncRunEvent)
                .where(SyncRunEvent.sync_run_id == run_id)
                .order_by(SyncRunEvent.seq)
            )
        ).scalars().all()
    )


async def data_freshness(session: AsyncSession) -> dict[uuid.UUID, dict]:
    """Per connection: when a sync last brought new data, and how many quiet
    successful syncs have run since.

    "New" is anything a run wrote to the ledger — inserted, updated or settled
    (reconciled) rows. Failed runs are not counted as quiet: they are already
    reported as failures, and this is about the ones that say "ok".
    """
    runs = (
        await session.execute(
            select(
                SyncRun.connection_id,
                SyncRun.started_at,
                SyncRun.status,
                SyncRun.txns_inserted,
                SyncRun.txns_updated,
                SyncRun.txns_reconciled,
            )
            .where(SyncRun.connection_id.is_not(None))
            .order_by(SyncRun.connection_id, SyncRun.started_at.desc())
        )
    ).all()
    out: dict[uuid.UUID, dict] = {}
    for conn_id, started_at, status, ins, upd, rec in runs:
        entry = out.setdefault(
            conn_id, {"last_new_data_at": None, "quiet_syncs": 0, "_done": False}
        )
        if entry["_done"]:
            continue
        if (ins or 0) + (upd or 0) + (rec or 0) > 0:
            entry["last_new_data_at"] = started_at
            entry["_done"] = True
        elif status == "ok":
            entry["quiet_syncs"] += 1
    for entry in out.values():
        entry.pop("_done")
    return out
