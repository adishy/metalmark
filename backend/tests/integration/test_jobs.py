"""The sync queue: three defences, and the isolation they run under.

A queue that only works when nothing goes wrong is a queue that loses money. The
three properties here are the ones that make failure survivable, and each is
pinned against the *specific* mistake it prevents:

* **The claim is safe to lose.** Two workers can race into
  ``uq_sync_jobs_connection_running``; the guard makes that rare and the savepoint
  makes it harmless. Tested by removing the guard, because the race it leaves
  behind is the one thing a deterministic test cannot otherwise produce.
* **A slow job is not a dead job.** The reaper's clock is ``heartbeat_at``, never
  ``started_at``, so the test gives a job an ancient ``started_at`` and a fresh
  heartbeat and requires it to survive.
* **A fenced finalize is a no-op.** A worker that was reaped must not be able to
  write a terminal state over the job that replaced it.

Plus tenancy, which is not a feature of this module but a constraint on all of
it: ``sync_jobs`` is under RLS like every other household table, so these
functions are only ever called inside a scope — and a claim in one household must
never return another's work.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.db import scoped_session, unscoped_session
from app.models import Account, AccountConnection, SyncJob, SyncRun, Transaction
from app.models.sync import JOB_STATUSES
from app.security.crypto import SecretBox
from app.services import jobs, sync
from app.services.fake_simplefin import FAKE_ACCESS_URL
from app.settings import get_settings

pytestmark = pytest.mark.integration

#: A fixed clock, because half of these tests are about *when* something is due.
#: Anchored to the fixture capture's era rather than the wall clock, for the same
#: reason ``test_sync.py`` is: a test whose meaning depends on today's date is a
#: test that expires.
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

#: Comfortably longer than ``REAP_AFTER_SECONDS``, so "stale" is stale by any
#: reading and the test is not balanced on the constant's exact value.
ANCIENT = NOW - timedelta(hours=4)

#: The statuses a job never leaves. Derived from the model rather than written
#: out: a seventh status added to ``JOB_STATUSES`` and forgotten here would make
#: the poll below wait forever and fail as a bare timeout, with nothing saying why.
_TERMINAL = frozenset(JOB_STATUSES) - {"queued", "running"}


# ---- fixtures --------------------------------------------------------------


async def _make_connection(
    household_id: uuid.UUID,
    *,
    name: str = "SimpleFIN Bridge",
    is_enabled: bool = True,
    next_sync_at: datetime | None = None,
) -> uuid.UUID:
    """A connection whose credential is encrypted exactly as the API stores it.

    ``provider="fake"`` so that a test which lets the worker run one of these
    reaches the committed capture rather than the real bridge.
    """
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id,
            provider="fake",
            access_url_encrypted=SecretBox(get_settings().secret_key).encrypt(FAKE_ACCESS_URL),
            org_name=name,
            is_enabled=is_enabled,
            next_sync_at=next_sync_at,
        )
        session.add(connection)
        await session.flush()
        return connection.id


async def _make_job(
    household_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    status: str = "queued",
    attempts: int = 0,
    trigger: str = "cron",
    not_before: datetime | None = None,
    started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    claim_token: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    async with scoped_session(household_id) as session:
        job = SyncJob(
            household_id=household_id,
            connection_id=connection_id,
            trigger=trigger,
            status=status,
            attempts=attempts,
            not_before=not_before,
            started_at=started_at,
            heartbeat_at=heartbeat_at,
            claim_token=claim_token,
        )
        if created_at is not None:
            job.created_at = created_at
        session.add(job)
        await session.flush()
        return job.id


async def _job_row(household_id, job_id) -> SyncJob:
    async with scoped_session(household_id) as session:
        return (await session.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()


@pytest.fixture
async def household(household_factory):
    return await household_factory("Queue")


async def _one(household_id: uuid.UUID) -> list[uuid.UUID]:
    """Stands in for ``worker.household_ids`` when a test needs just one."""
    return [household_id]


# ---- the claim -------------------------------------------------------------


async def test_claiming_nothing_is_none_and_not_an_error(household):
    async with scoped_session(household) as session:
        assert await jobs.claim_next(session, household, now=NOW) is None


async def test_the_claim_takes_the_job_and_stamps_it(household):
    connection_id = await _make_connection(household)
    job_id = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)

    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.connection_id == connection_id
    assert claimed.household_id == household
    assert claimed.attempts == 1  # counted at claim, so "5 attempts" means five

    row = await _job_row(household, job_id)
    assert row.status == "running"
    assert row.claim_token == claimed.token
    assert row.heartbeat_at == NOW
    assert row.started_at == NOW


async def test_a_job_that_is_not_due_yet_is_not_claimed(household):
    """``not_before`` is the backoff gate, and the tick runs far more often than
    the backoff is long — so honouring it here is the whole of the retry policy."""
    connection_id = await _make_connection(household)
    await _make_job(household, connection_id, not_before=NOW + timedelta(minutes=30))

    async with scoped_session(household) as session:
        assert await jobs.claim_next(session, household, now=NOW) is None


async def test_the_oldest_due_job_is_claimed_first(household):
    connection_id = await _make_connection(household)
    older = await _make_job(household, connection_id, created_at=NOW - timedelta(minutes=30))
    await _make_job(household, connection_id, created_at=NOW - timedelta(minutes=5))

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)

    # Ordering is what makes the queue a queue instead of an arbitrary pick. The
    # second job is not claimable yet — only one may run per connection — so the
    # order is only observable through the first claim.
    assert claimed is not None
    assert claimed.job_id == older


async def test_a_second_job_for_a_running_connection_is_not_claimed(household):
    """The guard half of ADR-0004's one-running-job-per-connection.

    It exists to make the ``23505`` rare, and the savepoint below exists because
    "rare" is not "never".
    """
    connection_id = await _make_connection(household)
    await _make_job(
        household, connection_id, status="running", started_at=ANCIENT,
        heartbeat_at=NOW, claim_token=uuid.uuid4(),
    )
    queued = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        assert await jobs.claim_next(session, household, now=NOW) is None

    assert (await _job_row(household, queued)).status == "queued"


#: The ``NOT EXISTS`` block, lifted out of the real statement so the test below
#: can delete exactly that and nothing else. Asserted to be present first: a
#: rewrite of ``_CLAIM`` that reworded the guard would otherwise make this test
#: silently stop testing anything.
_THE_GUARD = """AND NOT EXISTS (
                 SELECT 1 FROM sync_jobs r
                  WHERE r.connection_id = j.connection_id
                    AND r.status = 'running'
               )"""


async def test_a_lost_claim_race_is_a_none_not_a_crash(household, monkeypatch):
    """The savepoint half, and the reason it is needed.

    Without the guard, two workers claiming the same connection produce a
    ``unique_violation`` on ``uq_sync_jobs_connection_running`` — and without the
    savepoint around the claim, that error aborts the enclosing transaction, so
    the *worker* dies rather than the claim. A crash loop under contention is a
    strictly worse failure than a skipped tick.

    Reproducing the real race means two live transactions and a lock wait, which
    cannot be scripted deterministically. So the guard is removed instead, which
    is precisely the state the guard exists to make unlikely — the race is then
    certain, and what is under test (the savepoint, the ``IntegrityError``
    handling, the transaction surviving) is untouched.
    """
    assert _THE_GUARD in jobs._CLAIM.text, "the guard moved; this test is now vacuous"
    monkeypatch.setattr(jobs, "_CLAIM", text(jobs._CLAIM.text.replace(_THE_GUARD, "")))

    # And a spy, because "returned None" is also what a claim returns when it
    # finds nothing at all. Without this the whole test could pass while never
    # reaching the code it exists for — which is the failure mode of every test
    # that asserts a negative.
    collisions: list[bool] = []
    real = jobs._is_unique_violation
    monkeypatch.setattr(
        jobs, "_is_unique_violation", lambda exc: collisions.append(True) or real(exc)
    )

    connection_id = await _make_connection(household)
    first = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=2))
    second = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)
        assert claimed is not None and claimed.job_id == first

        # This one must violate the partial unique index, and must not raise.
        assert await jobs.claim_next(session, household, now=NOW) is None

        # The statement that pins the savepoint. With one, the aborted claim is
        # rolled back to the savepoint and this session is still usable; without
        # one, the transaction is poisoned and this raises PendingRollbackError.
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    assert collisions == [True], "the collision never happened; this test proved nothing"
    assert (await _job_row(household, second)).status == "queued"
    assert (await _job_row(household, first)).status == "running"


async def test_a_queued_job_for_a_paused_connection_is_cancelled_at_claim_time(household):
    """Pause has to be enforced somewhere, and this is the last free moment.

    The queue cannot un-queue at pause time — the job may not exist yet, and one
    already running must be allowed to finish — so the claim is where "the user
    switched this off" becomes true.
    """
    connection_id = await _make_connection(household, is_enabled=False)
    job_id = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        assert await jobs.claim_next(session, household, now=NOW) is None

    row = await _job_row(household, job_id)
    assert row.status == "cancelled"
    assert row.finished_at == NOW
    assert "paused" in row.error

    # Cancel is terminal, and resuming does not resurrect it — but the connection
    # is free again (the partial unique index is on `running`, and nothing is
    # running), so the tick queues a fresh one. That pair is what makes "pause"
    # mean "not now" rather than "never again".
    async with scoped_session(household) as session:
        connection = await session.get(AccountConnection, connection_id)
        connection.is_enabled = True
    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 1

    assert (await _job_row(household, job_id)).status == "cancelled"


# ---- the heartbeat and the reaper ------------------------------------------


async def test_the_fence_stamps_the_heartbeat_it_checks(household):
    """One statement, two jobs: "is this still mine?" and "I am still here".

    Doing it in one UPDATE is what makes the answer atomic — a cancel landing
    between a separate SELECT and UPDATE would be read as "still ours".

    Anchored to the real clock rather than to ``NOW``: the fence deliberately has
    no injectable time, because the thing it must report is *actually* still
    alive, and a heartbeat a test could pin would prove nothing about that.
    """
    claimed_at = datetime.now(UTC) - timedelta(hours=4)
    connection_id = await _make_connection(household)
    job_id = await _make_job(
        household, connection_id, not_before=claimed_at - timedelta(minutes=1)
    )

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=claimed_at)

    assert claimed is not None
    assert (await _job_row(household, job_id)).heartbeat_at == claimed_at

    before = datetime.now(UTC)
    async with scoped_session(household) as session:
        assert await jobs.fence_for(claimed)(session) is True

    # Moved to the wall clock, which is what a stalled fetch's job would not do.
    assert (await _job_row(household, job_id)).heartbeat_at >= before


async def test_a_slow_job_is_not_reaped_and_a_silent_one_is(household):
    """**The** property of the reaper, and the reason it reads ``heartbeat_at``.

    The two jobs below are equally old in the only sense ``started_at`` could
    measure — four hours. One is still talking to us and must survive: a bank that
    takes twenty minutes to answer is not a dead worker, and reaping it would kill
    exactly the runs that are working.

    Two connections, not one, because a connection may only have one running job
    (``uq_sync_jobs_connection_running``) and the comparison needs both at once.
    """
    now = datetime.now(UTC)
    ancient = now - timedelta(hours=4)
    alive = await _make_connection(household, name="Alive Bank")
    dead = await _make_connection(household, name="Dead Bank")
    slow = await _make_job(
        household, alive, status="running", trigger="manual",
        started_at=ancient, heartbeat_at=now - timedelta(seconds=5), claim_token=uuid.uuid4(),
    )
    silent = await _make_job(
        household, dead, status="running",
        started_at=ancient, heartbeat_at=ancient, claim_token=uuid.uuid4(),
    )

    async with scoped_session(household) as session:
        reaped = await jobs.reap_stale_jobs(session, now=now)

    assert [job_id for job_id, _ in reaped] == [silent]
    assert (await _job_row(household, slow)).status == "running"
    assert (await _job_row(household, silent)).status == "queued"


async def test_reaping_backs_the_job_off_rather_than_retrying_it_at_once(household):
    """A job taken back from a dead worker rejoins the queue on the same curve a
    failed attempt uses — one definition of backoff, so the two cannot drift."""
    connection_id = await _make_connection(household)
    job_id = await _make_job(
        household, connection_id, status="running", attempts=2,
        started_at=ANCIENT, heartbeat_at=ANCIENT, claim_token=uuid.uuid4(),
    )

    async with scoped_session(household) as session:
        await jobs.reap_stale_jobs(session, now=NOW)

    row = await _job_row(household, job_id)
    assert row.status == "queued"
    assert row.claim_token is None
    assert row.heartbeat_at is None
    assert row.error == jobs.REAP_REASON
    assert row.not_before == NOW + timedelta(seconds=sync.backoff_seconds(2))


async def test_reaping_expires_a_job_that_has_run_out_of_attempts(household):
    connection_id = await _make_connection(household)
    job_id = await _make_job(
        household, connection_id, status="running", attempts=jobs.MAX_ATTEMPTS,
        started_at=ANCIENT, heartbeat_at=ANCIENT, claim_token=uuid.uuid4(),
    )

    async with scoped_session(household) as session:
        await jobs.reap_stale_jobs(session, now=NOW)

    row = await _job_row(household, job_id)
    assert row.status == "expired"
    assert row.finished_at == NOW


async def test_reaping_closes_the_run_the_dead_worker_left_open(household):
    """The run row is committed *before* the fetch, so a worker killed mid-flight
    leaves one sitting at ``running`` forever. A dashboard showing a run in
    progress from three weeks ago is worse than one showing nothing: it hides
    every run after it behind the same lie."""
    connection_id = await _make_connection(household)
    job_id = await _make_job(
        household, connection_id, status="running",
        started_at=ANCIENT, heartbeat_at=ANCIENT, claim_token=uuid.uuid4(),
    )
    async with scoped_session(household) as session:
        run = SyncRun(
            household_id=household, connection_id=connection_id, job_id=job_id,
            trigger="cron", status="running", started_at=ANCIENT,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    async with scoped_session(household) as session:
        await jobs.reap_stale_jobs(session, now=NOW)

    async with scoped_session(household) as session:
        row = (await session.execute(select(SyncRun).where(SyncRun.id == run_id))).scalar_one()
    assert row.status == "error"
    assert row.finished_at == NOW
    assert row.error == jobs.REAP_REASON


async def test_a_fenced_finalize_after_a_reap_is_a_no_op(household):
    """The whole reason the token exists.

    The worker that lost its job is still running, still has results, and will
    reach its finalize. The token is what makes that write land on nothing —
    otherwise it would overwrite the terminal state of the job that took its
    place, and the queue would report a job as ``done`` that had been retried.
    """
    connection_id = await _make_connection(household)
    job_id = await _make_job(
        household, connection_id, not_before=ANCIENT - timedelta(minutes=1)
    )

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=ANCIENT)
    assert claimed is not None

    async with scoped_session(household) as session:
        await jobs.reap_stale_jobs(session, now=NOW)

    async with scoped_session(household) as session:
        assert await jobs.finish_job(session, claimed, run_status="ok", now=NOW) == "lost"

    row = await _job_row(household, job_id)
    assert row.status == "queued"  # what the reaper left, untouched
    assert row.finished_at is None


async def test_a_cancelled_job_cannot_be_finalized_by_the_worker_holding_it(household):
    """Cancel is a token bump, not a signal, so this is the same fence as above
    applied to the other way a job is taken away."""
    connection_id = await _make_connection(household)
    job_id = await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)

    # What the API does when the operator presses Cancel (services.connections).
    async with scoped_session(household) as session:
        row = (await session.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()
        row.status = "cancelled"
        row.claim_token = uuid.uuid4()

    async with scoped_session(household) as session:
        assert await jobs.finish_job(session, claimed, run_status="ok", now=NOW) == "lost"

    assert (await _job_row(household, job_id)).status == "cancelled"


# ---- finishing -------------------------------------------------------------


async def _claim(household, connection_id, *, attempts: int = 1):
    """Claim a job and return ``(claimed, job_id)``, for the finish tests."""
    job_id = await _make_job(
        household, connection_id, not_before=NOW - timedelta(minutes=1), attempts=attempts - 1
    )
    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)
    assert claimed is not None
    return claimed, job_id


@pytest.mark.parametrize("run_status", ["ok", "partial"])
async def test_a_run_that_finished_is_a_done_job(household, run_status):
    """``partial`` included, deliberately: the bridge answered and most accounts
    ingested. The run's status is the bank's story and lives on the run row; the
    job's status is whether the queue still has work to do."""
    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id)

    async with scoped_session(household) as session:
        assert await jobs.finish_job(session, claimed, run_status=run_status, now=NOW) == "done"

    row = await _job_row(household, job_id)
    assert row.status == "done"
    assert row.finished_at == NOW


async def test_a_failed_run_requeues_with_backoff_until_the_attempts_run_out(household):
    """A bank that timed out once is not something the user should have to notice."""
    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id, attempts=2)

    async with scoped_session(household) as session:
        final = await jobs.finish_job(
            session, claimed, run_status="error", error="the bridge timed out", now=NOW
        )

    assert final == "queued"
    row = await _job_row(household, job_id)
    assert row.status == "queued"
    assert row.error == "the bridge timed out"
    assert row.not_before == NOW + timedelta(seconds=sync.backoff_seconds(2))
    # Cleared so the next claim cannot be mistaken for the same attempt, and so
    # nothing is holding the connection against a future run.
    assert row.claim_token is None
    assert row.started_at is None
    assert row.finished_at is None
    assert row.attempts == 2  # not reset: the retry is the same job, not a new one


async def test_the_last_attempt_expires_instead_of_requeuing(household):
    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id, attempts=jobs.MAX_ATTEMPTS)

    async with scoped_session(household) as session:
        final = await jobs.finish_job(session, claimed, run_status="error", now=NOW)

    assert final == "expired"
    assert (await _job_row(household, job_id)).status == "expired"


async def test_a_cancelled_run_closes_its_job_as_cancelled(household):
    """The path a cancel during the fetch takes: the fence fails, sync closes the
    run as ``cancelled``, and the job's own close follows the run."""
    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id)

    async with scoped_session(household) as session:
        final = await jobs.finish_job(session, claimed, run_status="cancelled", now=NOW)
    assert final == "cancelled"

    assert (await _job_row(household, job_id)).status == "cancelled"


async def test_finishing_closes_a_run_the_worker_abandoned(household):
    """The invariant this makes structural rather than per-caller.

    An exception outside the two cases ``sync`` handles rolls back TX2 without
    closing the run TX1 opened. Rather than ask every caller's error handling to
    remember, closing out a *live* job closes any run still claiming to be going.
    """
    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id)
    async with scoped_session(household) as session:
        run = SyncRun(
            household_id=household, connection_id=connection_id, job_id=job_id,
            trigger="cron", status="running", started_at=NOW,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    async with scoped_session(household) as session:
        await jobs.finish_job(session, claimed, run_status="error", error="boom", now=NOW)

    async with scoped_session(household) as session:
        row = (await session.execute(select(SyncRun).where(SyncRun.id == run_id))).scalar_one()
    assert row.status == "error"
    assert row.error == "boom"


# ---- the cron tick ---------------------------------------------------------


async def test_a_due_connection_gets_one_job_and_only_one(household):
    """The tick runs every fifteen minutes and a cadence is a day. Without the
    ``NOT EXISTS``, a worker that was down for a week would come back to a queue
    the length of the outage."""
    connection_id = await _make_connection(household, next_sync_at=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 1
    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 0

    async with scoped_session(household) as session:
        queued = (await session.execute(select(SyncJob))).scalars().all()
    assert len(queued) == 1
    assert queued[0].connection_id == connection_id
    assert queued[0].trigger == "cron"
    assert queued[0].status == "queued"
    assert queued[0].not_before == NOW


async def test_the_tick_leaves_alone_what_is_not_due_or_not_wanted(household):
    """Three ways a connection says "not now", and all three must mean it."""
    await _make_connection(household, next_sync_at=NOW + timedelta(hours=1))  # not yet
    await _make_connection(household, next_sync_at=NOW - timedelta(minutes=1), is_enabled=False)
    never_synced = await _make_connection(household, next_sync_at=None)  # never run: due now

    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 1

    async with scoped_session(household) as session:
        queued = (await session.execute(select(SyncJob))).scalars().all()
    assert [j.connection_id for j in queued] == [never_synced]


async def test_the_tick_does_not_stack_a_second_job_on_a_running_one(household):
    connection_id = await _make_connection(household, next_sync_at=ANCIENT)
    await _make_job(
        household, connection_id, status="running",
        started_at=ANCIENT, heartbeat_at=NOW, claim_token=uuid.uuid4(),
    )

    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 0


# ---- tenancy ---------------------------------------------------------------


async def test_an_unscoped_session_cannot_see_the_queue_at_all(household):
    """Fail-closed, and asserted over every table this module touches.

    ``jobs.py`` never queries without a scope; this is what makes that a
    requirement rather than a style. It is also why the worker enumerates
    households: there is no query here that could ask "what work exists" across
    tenants, because such a query returns nothing by construction.
    """
    connection_id = await _make_connection(household)
    await _make_job(household, connection_id, not_before=NOW - timedelta(minutes=1))
    async with scoped_session(household) as session:
        run = SyncRun(
            household_id=household, connection_id=connection_id,
            trigger="cron", status="running", started_at=NOW,
        )
        session.add(run)
        await session.flush()
        assert await jobs.claim_next(session, household, now=NOW) is not None

    async with unscoped_session() as session:
        for table in ("sync_jobs", "sync_runs", "sync_run_events"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"{table} leaked across the tenant boundary"


async def test_a_claim_never_returns_another_households_job(household, household_factory):
    """The worker walks households in a loop, so this is the mistake the loop
    makes possible: scoping the *session* to one tenant while claiming work
    belonging to another. It cannot happen — the claim is a scoped query — and
    that is worth being a test rather than an argument."""
    other = await household_factory("Other")
    other_connection = await _make_connection(other, name="Other Bank")
    other_job = await _make_job(other, other_connection, not_before=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        assert await jobs.claim_next(session, household, now=NOW) is None

    # Neither claimed nor touched: a claim that returned it would have set these.
    row = await _job_row(other, other_job)
    assert row.status == "queued"
    assert row.claim_token is None

    # And the same for the tick, which inserts on behalf of a household.
    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 0


async def test_reaping_one_household_does_not_touch_another(household, household_factory):
    other = await household_factory("Other")
    other_connection = await _make_connection(other, name="Other Bank")
    stale = await _make_job(
        other, other_connection, status="running",
        started_at=ANCIENT, heartbeat_at=ANCIENT, claim_token=uuid.uuid4(),
    )

    async with scoped_session(household) as session:
        assert await jobs.reap_stale_jobs(session, now=NOW) == []

    assert (await _job_row(other, stale)).status == "running"


# ---- the worker's assembly -------------------------------------------------


async def test_the_worker_runs_a_claimed_job_and_links_the_run_to_it(household):
    """``worker.run_one`` end to end, against the committed capture.

    Two things are only provable here rather than in the queue's own tests: that
    a worker with no provider injected reaches the *connection's* provider rather
    than the default, and that ``SyncRun.job_id`` is set — the link the reaper
    needs to close an orphaned run, and which is invisible from either side
    alone.
    """
    from app import worker

    connection_id = await _make_connection(household, next_sync_at=NOW - timedelta(minutes=1))

    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 1
    async with scoped_session(household) as session:
        claimed = await jobs.claim_next(session, household, now=NOW)
    assert claimed is not None

    final = await worker.run_one(claimed)

    assert final == "done"
    async with scoped_session(household) as session:
        run = (await session.execute(select(SyncRun))).scalars().one()
        assert run.job_id == claimed.job_id
        assert run.status == "ok"
        assert run.connection_id == connection_id
        assert run.connection_label == "SimpleFIN Bridge"
        # The run did what a run does, so this is the engine and not just the
        # bookkeeping around it.
        assert run.txns_inserted > 0
        assert (await session.execute(select(Account))).scalars().all()
        assert (await session.execute(select(Transaction))).scalars().all()

    assert (await _job_row(household, claimed.job_id)).status == "done"


async def test_the_consumer_drains_the_queue_and_stops_when_asked(household, monkeypatch):
    """The loop itself: claim, run, finish, and come back for more.

    The pieces are each tested above; this is the wiring between them, which is
    the part that is invisible until a deployed worker sits idle next to a full
    queue.

    ``household_ids`` is narrowed to this household. Not to make the code under
    test easier — the loop is unchanged — but because the test database is shared
    across the session, so the real enumeration would sweep up jobs other tests
    left behind and this test's runtime would depend on file ordering. The
    enumeration has its own check against the running container.
    """
    from app import worker

    await _make_connection(household, next_sync_at=NOW - timedelta(minutes=1))
    async with scoped_session(household) as session:
        assert await jobs.enqueue_scheduled_syncs(session, now=NOW) == 1

    monkeypatch.setattr(worker, "household_ids", lambda: _one(household))

    async with scoped_session(household) as session:
        job_id = (await session.execute(select(SyncJob.id))).scalar_one()

    async def _settled() -> SyncJob:
        # A *terminal* status, not merely "not queued". The claim moves the job to
        # `running` well before the sync finishes, so polling for "not queued"
        # returns during the run — and passes or fails depending on whether the
        # sync happens to complete between two polls. On a fast laptop that is
        # usually; on CI it was not.
        while True:
            row = await _job_row(household, job_id)
            if row.status in _TERMINAL:
                return row
            await asyncio.sleep(0.05)

    stop = asyncio.Event()
    consumer = asyncio.create_task(worker.consume(stop))

    try:
        row = await asyncio.wait_for(_settled(), timeout=60)
    finally:
        stop.set()
        # The loop must notice: a consumer that ignores the stop event holds up
        # every shutdown until the container is killed.
        await asyncio.wait_for(consumer, timeout=15)

    assert row.status == "done"
    assert row.attempts == 1
    assert not consumer.cancelled() and consumer.exception() is None


async def test_the_worker_survives_a_job_whose_sync_raises(household, monkeypatch):
    """A consumer that dies on an unexpected exception stops every household's
    sync until a human restarts the container. ``run_one`` swallows it, records
    it, and the queue retries on the backoff curve."""
    from app import worker

    connection_id = await _make_connection(household)
    claimed, job_id = await _claim(household, connection_id)

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("https://fake-user:fake-password@fake-bridge.invalid/simplefin/")

    monkeypatch.setattr(sync, "run_connection_sync", _explode)

    assert await worker.run_one(claimed) == "queued"

    row = await _job_row(household, job_id)
    assert row.status == "queued"
    assert "RuntimeError" in row.error
    # Sanitized on the way out: this is the path a credential would reach a log
    # and a database column by, if the exception were stored as it stands.
    assert "fake-password" not in row.error
