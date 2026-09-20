"""The sync queue: claim a job, keep it alive, finish it, and reap what died.

``services/sync.py`` runs one connection and knows nothing about who asked or
what happens to a job afterwards. This module is the other half — the queue's
primitives, and the three mechanisms that make a job safe to lose:

* **A heartbeat, not a timeout.** The reaper's predicate is
  ``heartbeat_at < now() - reap_after``, so a run that is merely slow is never
  reaped. Anchoring the timeout to ``started_at`` would kill exactly the runs that
  are working — a bank that took 20 minutes to answer is not a dead worker.
* **A fencing token.** Every write here is ``WHERE id = :id AND claim_token =
  :token``. A worker whose job was reaped and re-claimed touches zero rows and
  discards its results instead of resurrecting a terminal state — and the same
  token is what makes a *cancel* work, since there is no way to interrupt an
  in-flight HTTP call.
* **A savepoint around the claim.** The partial unique index
  ``uq_sync_jobs_connection_running`` is the database's own statement of ADR-0004's
  one-running-job-per-connection, and two workers can race into it. The claim
  excludes connections with a live run to make that rare, and catches
  ``unique_violation`` to make it *harmless* — without the savepoint, a lost race
  aborts the surrounding transaction and the worker's claim dies with a 23505.

Everything in here runs **inside a household's scope**. The worker enumerates
``households`` and calls these per household, which is what keeps the worker
subject to RLS exactly as the API is (ARCHITECTURE §5) — there is no privileged
cross-tenant read anywhere in this module, and ``sync_jobs`` is never queried
without a household in scope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountConnection
from app.security.redact import sanitize
from app.services.sync import backoff_seconds

#: How long a job may go without a heartbeat before the reaper takes it back.
#: Comfortably longer than a slow bank fetch (the plan's own figure: SimpleFIN
#: answers in seconds, and a 60 s timeout was generous) and short enough that a
#: crashed worker's connection is not stuck for the rest of the day.
REAP_AFTER_SECONDS = 900

#: Attempts before a job is given up on. ``attempts`` counts claims, so this is
#: "five tries", and the backoff between them is ``sync.backoff_seconds`` — one
#: definition, shared with the failure path, so a requeued job and a retried one
#: cannot drift apart.
MAX_ATTEMPTS = 5

#: Jobs claimed per household per tick. One, deliberately: the consumer runs jobs
#: sequentially (ADR-0004's single worker), so a second claimed job would sit in
#: ``running`` with a heartbeat that has stopped advancing — which is precisely
#: what the reaper is looking for.
CLAIM_BATCH = 1

#: The reason string a reaped job carries. Written to the job *and* to any run
#: row it left open, so the dashboard's two views agree about what happened.
REAP_REASON = "the worker stopped heartbeating; the job was taken back"

_CLAIM = text(
    """
    WITH claimable AS (
        SELECT j.id
          FROM sync_jobs j
         WHERE j.status = 'queued'
           AND (j.not_before IS NULL OR j.not_before <= :now)
           AND NOT EXISTS (
                 SELECT 1 FROM sync_jobs r
                  WHERE r.connection_id = j.connection_id
                    AND r.status = 'running'
               )
         ORDER BY j.not_before NULLS FIRST, j.created_at
         LIMIT :limit
           FOR UPDATE SKIP LOCKED
    )
    UPDATE sync_jobs j
       SET status = 'running',
           claimed_at = :now,
           started_at = :now,
           heartbeat_at = :now,
           claim_token = :token,
           attempts = j.attempts + 1
      FROM claimable
     WHERE j.id = claimable.id
    RETURNING j.id, j.connection_id, j.household_id, j.trigger, j.attempts
    """
)

#: Fenced, and it is both the liveness signal and the ownership check: a worker
#: calling this is asking "is this still mine?" and answering "I am still here" in
#: the same statement. Doing it as one UPDATE rather than a SELECT plus an UPDATE
#: is what makes the answer atomic — a cancel landing between the two would
#: otherwise be read as "still ours".
_TOUCH = text(
    """
    UPDATE sync_jobs
       SET heartbeat_at = :now
     WHERE id = :id
       AND claim_token = :token
       AND status = 'running'
    RETURNING id
    """
)

_FINISH = text(
    """
    UPDATE sync_jobs
       SET status = :status, finished_at = :now, heartbeat_at = :now, error = :error
     WHERE id = :id AND claim_token = :token
    RETURNING id
    """
)

_REQUEUE = text(
    """
    UPDATE sync_jobs
       SET status = 'queued',
           not_before = :not_before,
           error = :error,
           claimed_at = NULL,
           started_at = NULL,
           heartbeat_at = NULL,
           claim_token = NULL
     WHERE id = :id AND claim_token = :token
    RETURNING id
    """
)

_CANCEL = text(
    """
    UPDATE sync_jobs
       SET status = 'cancelled', finished_at = :now, heartbeat_at = :now, error = :error
     WHERE id = :id AND claim_token = :token
    RETURNING id
    """
)

#: Due connections that have nothing queued or running. The ``NOT EXISTS`` is
#: what stops a wedged worker from accumulating a queue the length of the outage:
#: a connection gets one job at a time, however long its ``next_sync_at`` has been
#: in the past.
_ENQUEUE_DUE = text(
    """
    INSERT INTO sync_jobs (id, household_id, connection_id, trigger, status, attempts,
                           not_before, created_at, updated_at)
    SELECT gen_random_uuid(), c.household_id, c.id, 'cron', 'queued', 0,
           :now, :now, :now
      FROM account_connections c
     WHERE c.is_enabled
       AND (c.next_sync_at IS NULL OR c.next_sync_at <= :now)
       AND NOT EXISTS (
             SELECT 1 FROM sync_jobs j
              WHERE j.connection_id = c.id
                AND j.status IN ('queued', 'running')
           )
    RETURNING id
    """
)

_STALE = text(
    """
    SELECT id, connection_id, attempts
      FROM sync_jobs
     WHERE status = 'running'
       AND heartbeat_at IS NOT NULL
       AND heartbeat_at < :cutoff
     ORDER BY heartbeat_at
     LIMIT :limit
       FOR UPDATE SKIP LOCKED
    """
)

#: The reaper's two writes. **Unfenced by design**, and the absence is the point:
#: the fence is the token the *dead* worker still holds, and the reaper is taking
#: the job back precisely because that worker is gone. Its exclusivity comes from
#: the ``FOR UPDATE SKIP LOCKED`` in ``_STALE`` instead — the row lock, not the
#: token, is what makes the reaper and a live worker mutually exclusive.
_EXPIRE = text(
    """
    UPDATE sync_jobs
       SET status = 'expired', finished_at = :now, claimed_at = NULL,
           heartbeat_at = NULL, claim_token = NULL, error = :error
     WHERE id = :id
    """
)

_REAP_REQUEUE = text(
    """
    UPDATE sync_jobs
       SET status = 'queued',
           not_before = :not_before,
           error = :error,
           claimed_at = NULL,
           started_at = NULL,
           heartbeat_at = NULL,
           claim_token = NULL
     WHERE id = :id
    """
)

_ORPHAN_RUNS = text(
    """
    UPDATE sync_runs
       SET status = 'error', finished_at = :now, error = :error
     WHERE job_id = :job_id AND status = 'running'
    """
)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job this worker now owns, and the token it owns it by.

    The token is carried rather than re-read: every later write needs it, and a
    worker that kept the row instead would be holding a stale ORM object across
    the fetch — the one window in which another process may legally take the job
    away.
    """

    job_id: uuid.UUID
    household_id: uuid.UUID
    connection_id: uuid.UUID
    trigger: str
    attempts: int
    token: uuid.UUID


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Whether a failed write lost the ``uq_sync_jobs_connection_running`` race.

    Read off the driver's SQLSTATE rather than parsed out of the message: the
    message is Postgres's wording and changes between versions and locales, while
    ``23505`` is the standard.
    """
    return getattr(exc.orig, "sqlstate", None) == "23505" or getattr(
        exc.orig, "pgcode", None
    ) == "23505"


async def claim_next(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> ClaimedJob | None:
    """Claim one due job for this household, or return ``None``.

    ``None`` is the ordinary answer — no work is the common case — and it is also
    what a lost race returns. Both callers treat them identically: sleep, and ask
    again.

    A job whose connection has been **paused** is cancelled here rather than run.
    The queue cannot un-queue it at pause time (the job may not exist yet, and a
    job that is already running must be allowed to finish), so the moment before
    it would execute is the last one at which refusing is free. Silently syncing a
    connection the user switched off is the one behaviour that would make the
    pause button untrustworthy.
    """
    moment = now or datetime.now(UTC)
    token = uuid.uuid4()
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    _CLAIM,
                    {
                        "now": moment,
                        "limit": CLAIM_BATCH,
                        "token": token,
                    },
                )
            ).first()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            # Another worker claimed this connection's job a moment ago. The
            # savepoint rolled back, so this transaction is still usable — with
            # no savepoint the whole claim would abort on a 23505.
            return None
        raise

    if row is None:
        return None

    connection = await session.get(AccountConnection, row.connection_id)
    if connection is None or not connection.is_enabled:
        await session.execute(
            _CANCEL,
            {
                "id": row.id,
                "token": token,
                "now": moment,
                "error": (
                    "the connection was removed"
                    if connection is None
                    else "the connection was paused before the job ran"
                ),
            },
        )
        return None

    return ClaimedJob(
        job_id=row.id,
        household_id=row.household_id,
        connection_id=row.connection_id,
        trigger=row.trigger,
        attempts=row.attempts,
        token=token,
    )


def fence_for(job: ClaimedJob):
    """The ``Fence`` this job's run is executed under.

    Passed into ``sync.run_connection_sync``, which calls it inside the ingest
    transaction just after the fetch — the first moment it can ask, and the last
    at which the answer is still cheap to act on. It doubles as the post-fetch
    heartbeat, and that is deliberate rather than economical: the fetch is the
    long part of a run, so the statement that says "I am alive" belongs at the end
    of it, and there is no window in which a worker could heartbeat a job it has
    already lost.
    """

    async def fence(session: AsyncSession) -> bool:
        touched = (
            await session.execute(
                _TOUCH,
                {"id": job.job_id, "token": job.token, "now": datetime.now(UTC)},
            )
        ).first()
        return touched is not None

    return fence


async def finish_job(
    session: AsyncSession,
    job: ClaimedJob,
    *,
    run_status: str,
    error: str | None = None,
    now: datetime | None = None,
) -> str:
    """Close out a job from the run it produced. Returns the job's new status.

    A failed run is **retried, not failed**, until the attempts are spent: a bank
    that timed out once is not a connection the user should have to notice.
    ``sync.backoff_seconds`` decides when the retry becomes claimable, so the
    reaper and the failure path space their retries identically.

    A run that finished — ``ok``, ``partial``, even with an error message — is a
    ``done`` job. The run's status is the *bank's* story and lives on the run row;
    the job's status is whether the queue still has work to do. ``partial``
    especially: the bridge answered, most accounts ingested, and attempting it
    again would not make the stale account fresher.
    """
    moment = now or datetime.now(UTC)

    if run_status == "cancelled":
        statement, params, outcome = _FINISH, {"status": "cancelled"}, "cancelled"
    elif run_status == "error" and job.attempts < MAX_ATTEMPTS:
        statement = _REQUEUE
        params = {"not_before": moment + timedelta(seconds=backoff_seconds(job.attempts))}
        outcome = "queued"
    elif run_status == "error":
        statement, params, outcome = _FINISH, {"status": "expired"}, "expired"
    else:
        statement, params, outcome = _FINISH, {"status": "done"}, "done"

    touched = (
        await session.execute(
            statement,
            {
                **params,
                "id": job.job_id,
                "token": job.token,
                "now": moment,
                "error": sanitize(error) if error else None,
            },
        )
    ).first()

    if touched is None:
        # The fence, doing its job: the job was cancelled or reaped while this
        # run was in flight, so its outcome is no longer anyone's to record.
        #
        # Nothing is closed here, and that restraint is deliberate. "Lost" can
        # mean the job was reaped *and re-claimed by another worker*, which is
        # now running it under a fresh token and may have a live run row for the
        # same ``job_id``. Closing orphans on this path would kill a run that is
        # very much alive — so the cleanup below is gated on the write above.
        return "lost"

    # We still hold the job, so any run row for it that is *still* ``running`` is
    # one this worker abandoned — an exception in TX2 outside the two cases sync
    # handles, which rolls back without closing the run it opened in TX1. Every
    # other path closes its own run, so this normally updates nothing. It costs
    # one statement to make "no job that has stopped leaves a run saying it is
    # still going" an invariant of the queue rather than a property of each
    # caller's error handling.
    await session.execute(
        _ORPHAN_RUNS,
        {
            "job_id": job.job_id,
            "now": moment,
            "error": sanitize(error) if error else f"the run was abandoned; the job is {outcome}",
        },
    )
    return outcome


async def enqueue_scheduled_syncs(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Queue a run for every due connection in this household. Returns how many.

    Due is ``next_sync_at <= now``, which the *run* keeps moving — a run sets it
    to ``now + interval`` on success and to ``now + backoff`` on failure. So the
    cadence is per connection (ADR-0028's interval, default 6 h) and a connection
    that has been failing is retried on the same backoff curve as a queued job,
    rather than being asked every tick.

    Paused connections are skipped here as well as at claim time: there is no
    point queueing work that will be cancelled a second later, and the queue is a
    thing the operator reads.
    """
    moment = now or datetime.now(UTC)
    rows = (
        await session.execute(_ENQUEUE_DUE, {"now": moment})
    ).all()
    return len(rows)


async def reap_stale_jobs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    reap_after: int = REAP_AFTER_SECONDS,
    limit: int = 50,
) -> list[tuple[uuid.UUID, str]]:
    """Take back jobs whose worker stopped heartbeating. Returns ``(id, status)``.

    The rows are locked ``FOR UPDATE SKIP LOCKED`` *before* being touched, which is
    what stops the reaper from flipping a job a worker is finalizing at this exact
    moment: whichever of the two gets the row first, the other sees it as taken and
    moves on.

    Reaping also closes the run the dead worker left open. A run row is written
    and committed *before* the fetch, so a worker that dies mid-flight leaves one
    sitting at ``running`` forever — and a dashboard that shows a run in progress
    from three weeks ago is worse than one that shows nothing, because it hides
    every run after it behind the same lie.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=reap_after)
    stale = (
        await session.execute(_STALE, {"cutoff": cutoff, "limit": limit})
    ).all()

    reaped: list[tuple[uuid.UUID, str]] = []
    for row in stale:
        if row.attempts >= MAX_ATTEMPTS:
            status = "expired"
            await session.execute(_EXPIRE, {"id": row.id, "now": moment, "error": REAP_REASON})
        else:
            status = "queued"
            await session.execute(
                _REAP_REQUEUE,
                {
                    "id": row.id,
                    "not_before": moment + timedelta(seconds=backoff_seconds(row.attempts)),
                    "error": REAP_REASON,
                },
            )
        await session.execute(
            _ORPHAN_RUNS, {"job_id": row.id, "now": moment, "error": REAP_REASON}
        )
        reaped.append((row.id, status))
    return reaped
