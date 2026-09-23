"""Background worker entrypoint: the sync queue's consumer.

Three things run here on three different clocks, and the clocks are chosen for
what each one is waiting on:

* **``enqueue_due``** — every 15 min. Turns "this connection's ``next_sync_at`` has
  passed" into a queued job. It is a tick rather than a per-connection timer
  because the cadence lives in the database (ADR-0028: per-connection, PATCH-able
  at runtime), and a timer would have to be torn down and rebuilt every time the
  dashboard's interval control moved.
* **``reap``** — every 5 min. Takes back jobs whose worker stopped heartbeating.
* **the consumer** — a plain ``asyncio.Task``, not a scheduler job. ``AsyncIOScheduler``
  awaits its coroutine jobs inline, so a consumer that blocks waiting for work
  would block the heartbeat and every cron alongside it.

**RLS is the load-bearing constraint here, not a detail.** The worker is subject to
the same row-level policies as the API (ARCHITECTURE §5), which means an unscoped
``SELECT`` on ``sync_jobs`` returns *nothing* — there is no way to ask "what work
exists" globally, by construction. So the worker enumerates households and asks
per household, inside ``scoped_session``. Its one unscoped query is the household
list itself — the same query ``deps.get_context`` already runs against a table
ARCHITECTURE §5 documents as non-RLS and app-readable, so the worker adds no
exposure that any principal able to run SQL as the app role did not already have.
Every ``sync_jobs``/``sync_runs``/``sync_run_events`` read and write is scoped.

``assert_app_role`` is what keeps that true. Bypassing RLS is a grant, not a code
path: a worker connected as a superuser would read every household's jobs and the
code above would look identical. It refuses to start rather than quietly losing
the isolation the whole design rests on. ``wait_for_database`` reaches it: it
waits out a database that has not been migrated yet — the ordinary state of a
fresh ``docker compose up`` — and passes a role *verdict* through untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import traceback
import uuid
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.db import scoped_session, unscoped_session
from app.logging import configure_logging, get_logger
from app.security.redact import sanitize
from app.services import checks, fx_fetch, jobs, sync
from app.settings import get_settings

log = get_logger("worker")

#: How long the consumer sleeps between passes when the queue is empty. Five
#: seconds is a *latency* choice, not a load one: a "Sync now" the user pressed
#: should feel immediate, and an empty pass is one indexed ``SELECT`` per
#: household. It is not a busy-wait — a job that is claimed is run to completion
#: before the next pass, so this only bounds how long an empty queue is idle.
POLL_SECONDS = 5

#: The tick that looks for connections whose ``next_sync_at`` has passed. Short
#: enough that a per-connection cadence is honoured to the quarter hour, long
#: enough that the insert-scan is nothing.
SYNC_TICK_MINUTES = 15

#: The reaper's cadence. Independent of ``POLL_SECONDS`` and much slower: reaping
#: only matters when something has already gone wrong, and a job is not eligible
#: until it has been silent for ``jobs.REAP_AFTER_SECONDS``.
REAP_TICK_MINUTES = 5
#: ECB publishes once a working day; asking more often only re-reads the same day.
FX_REFRESH_HOURS = 24

HEARTBEAT_MINUTES = 5

#: How long the worker waits for the app role to become reachable before giving
#: up. A fresh volume is not a misconfiguration — `docker compose up` starts this
#: container before anything has run `alembic upgrade head`, and the migration is
#: what creates the role — so the first connection being refused is the
#: *documented* path. Long enough to cover a slow first migration, short enough
#: to stay a startup step rather than a hang.
DB_WAIT_SECONDS = 120.0

#: Retry delay, doubling to the cap. The first is short because the common case
#: is a database a second or two behind; the cap is short because the uncommon
#: case (something is actually wrong) still deserves a log line the operator can
#: read rather than a scroll of identical failures.
DB_WAIT_FIRST_DELAY = 1.0
DB_WAIT_MAX_DELAY = 10.0

_ROLE = text(
    """
    SELECT current_user AS role,
           r.rolsuper AS is_superuser,
           r.rolbypassrls AS bypasses_rls
      FROM pg_roles r
     WHERE r.rolname = current_user
    """
)


def role_problem(row, expected_role: str) -> str | None:
    """Whether this connection's role violates tenant isolation, and how.

    A named function rather than a few lines inside ``main`` so the rule can be
    tested without a misconfigured database to point at — the failure it guards
    against is one nobody can reproduce locally, which is exactly the kind of
    check that rots into a comment unless a test can reach it.

    ``current_user`` is compared as well as the flags: a superuser or
    ``BYPASSRLS`` role is the loud failure, but a worker that quietly connected as
    the *owner* role (which owns the tables, and owns them regardless of RLS
    settings) is the quiet one.
    """
    if row is None:
        return f"could not read the role row for current_user (expected {expected_role!r})"
    if row.role != expected_role:
        return f"connected as {row.role!r}, expected {expected_role!r}"
    if row.is_superuser:
        return f"{row.role!r} is a superuser and would bypass row-level security"
    if row.bypasses_rls:
        return f"{row.role!r} has BYPASSRLS and would ignore tenant isolation"
    return None


async def assert_app_role() -> None:
    """Refuse to start unless the connection is subject to RLS.

    ``SystemExit`` rather than an exception: this runs before anything is
    scheduled, there is no caller to catch it, and a worker that continues is a
    worker that syncs every household into whichever one it happens to be scoped
    to. Exiting non-zero makes the container restart-loop instead, which surfaces
    the misconfiguration instead of hiding it behind working sync.
    """
    expected = get_settings().app_db_user
    async with unscoped_session() as session:
        row = (await session.execute(_ROLE)).first()
    problem = role_problem(row, expected)
    if problem is not None:
        log.error("worker.role_check_failed", problem=problem, expected=expected)
        raise SystemExit(1)
    log.info("worker.role_check_ok", role=expected)


async def wait_for_database(stop: asyncio.Event) -> None:
    """Wait until the app role can be reached, then assert it — or give up loudly.

    Two questions that look like one, and only the first is worth waiting on.

    *Can the app role be reached?* On a fresh volume it cannot, and that is
    boot ordering rather than a fault: the compose file starts this container
    before anything runs ``alembic upgrade head``, and the migration is what
    creates the role. ``password authentication failed for user
    "metalmark_app"`` is what that looks like — and it used to end the process,
    leaving a stack with no worker in it. Nothing complained: the api served,
    the panel drew, and every "Sync now" queued a job that nobody ran. A cost
    of one silent worker is the stale ledger this workstream exists to prevent,
    so the worker waits.

    *Is the role subject to RLS?* That is a verdict, not a wait.
    ``assert_app_role`` delivers it by raising ``SystemExit`` — a
    ``BaseException``, so it passes through the ``except Exception`` below
    untouched. Retrying a superuser would only delay a refusal that is already
    correct.

    Covers the *role*, not the schema. A role without tables is a broken install
    rather than a boot ordering, and the consumer reports it every five seconds
    (``worker.consume.failed``) instead of passing silently.
    """
    deadline = time.monotonic() + DB_WAIT_SECONDS
    delay = DB_WAIT_FIRST_DELAY
    while True:
        try:
            await assert_app_role()
            return
        except Exception as exc:  # noqa: BLE001 — deliberately total; see above
            left = deadline - time.monotonic()
            if left <= 0:
                log.error(
                    "worker.database_unreachable",
                    error=sanitize(str(exc)),
                    waited_seconds=DB_WAIT_SECONDS,
                    hint=(
                        "the app role never became reachable — has `alembic upgrade head` "
                        "run against this database? (`docker compose run --rm api alembic "
                        "upgrade head`)"
                    ),
                )
                raise SystemExit(1) from exc
            log.warning("worker.database_not_ready", error=sanitize(str(exc)), retry_in_s=delay)

        # Interruptible, so a worker waiting on a database that may never arrive
        # still exits on SIGTERM instead of sitting out the deadline while
        # `docker compose stop` waits on it.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=min(delay, left))
        if stop.is_set():
            log.info("worker.shutdown")
            raise SystemExit(0)
        delay = min(delay * 2, DB_WAIT_MAX_DELAY)


async def household_ids() -> list[uuid.UUID]:
    """Every household, for the per-household claim loop.

    The worker's only unscoped read, and deliberately the same one the API's
    request context makes: ``households`` carries no policy and is readable by the
    app role. Nothing about a household's *contents* is reachable from this
    session, which is what keeps "enumerate the tenants" from becoming "read
    across them".
    """
    async with unscoped_session() as session:
        rows = (await session.execute(text("SELECT id FROM households"))).all()
    return [row.id for row in rows]


async def enqueue_due() -> None:
    """Queue a job for every connection in every household that is due."""
    total = 0
    for household_id in await household_ids():
        async with scoped_session(household_id) as session:
            total += await jobs.enqueue_scheduled_syncs(session)
    if total:
        log.info("worker.enqueued", count=total)


async def reap() -> None:
    """Take back jobs whose worker went silent, and close the runs they left open.

    Unscoped-by-household for the same reason as everything else: the reaper is a
    queue-wide question, and RLS turns it into one question per household. The
    cost is a handful of indexed scans every five minutes.
    """
    for household_id in await household_ids():
        async with scoped_session(household_id) as session:
            reaped = await jobs.reap_stale_jobs(session)
        for job_id, status in reaped:
            log.warning("worker.reaped", job_id=str(job_id), job_status=status)


async def claim_one() -> jobs.ClaimedJob | None:
    """Claim one job from anywhere, or ``None`` if there is nothing to do.

    Across households rather than within one: the consumer is sequential, so the
    first household with work wins and the rest are not asked. That is the
    difference between one claim query per pass and one per household per pass,
    and it does not starve anybody — the next pass starts at the top again, and a
    household is only skipped when an earlier one is *doing* something.
    """
    for household_id in await household_ids():
        async with scoped_session(household_id) as session:
            claimed = await jobs.claim_next(session, household_id)
        if claimed is not None:
            return claimed
    return None


async def run_one(job: jobs.ClaimedJob) -> str:
    """Run one claimed job and close it out. Returns the job's final status.

    The outcome comes from ``sync.run_connection_sync``, but the *job* is closed
    here, in its own transaction: the run finishing and the queue moving on are
    separate facts, and the one that must survive a crash between them is the
    queue's. A run that ingested everything and a job left ``running`` would hold
    the connection against every future attempt until the reaper noticed.
    """
    try:
        outcome = await sync.run_connection_sync(
            job.household_id,
            job.connection_id,
            trigger=job.trigger,
            job_id=job.job_id,
            fence=jobs.fence_for(job),
        )
    except Exception as exc:  # noqa: BLE001 — see below
        # Deliberately total. A sync touching a bank, a database and a rule
        # engine can fail in ways neither this module nor `sync.py` enumerated,
        # and none of them justify killing the consumer: one bad connection would
        # otherwise stop every household's sync until a human restarted the
        # container. `sanitize` runs here as well as inside `finish_job` because
        # this message is logged from *this* line, not only stored.
        # Stored whole on the run (the household's own row); logged as shape only.
        detail = sanitize(f"{type(exc).__name__}: {exc}")
        log.error("worker.job.crashed", job_id=str(job.job_id), **failure(exc))
        run_status, error = "error", detail
    else:
        run_status, error = outcome.status, outcome.error

    async with scoped_session(job.household_id) as session:
        final = await jobs.finish_job(session, job, run_status=run_status, error=error)

    log.info(
        "worker.job.finished",
        job_id=str(job.job_id),
        connection_id=str(job.connection_id),
        run_status=run_status,
        job_status=final,
        attempts=job.attempts,
    )
    return final


async def consume(stop: asyncio.Event) -> None:
    """The consumer loop: claim, run, sleep, repeat — until told to stop."""
    while not stop.is_set():
        try:
            job = await claim_one()
            if job is not None:
                await run_one(job)
                # Immediately, rather than after the sleep: a queue that has
                # work should drain without a poll interval between each item.
                continue
        except Exception as exc:  # noqa: BLE001 — the loop must outlive any single pass
            # Reaching here means the failure was *outside* a job — the claim, a
            # household list, the session setup — since `run_one` handles its own.
            # The type and the line, not the message: see `failure`.
            log.error("worker.consume.failed", **failure(exc))

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)


def failure(exc: BaseException) -> dict[str, str]:
    """What the log may say about an exception: its type and where it was raised.

    Never the message or the traceback's locals. A database error's message
    carries the failed statement's parameters — account names, payees, amounts —
    and this process's stdout goes wherever the host sends container logs
    (ADR-0047). The place in the code is enough to find the bug; the full message,
    where one is worth keeping, goes to the household's own rows (a sync run's
    ``error``), behind RLS.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename.rsplit('/app/', 1)[-1]}:{frames[-1].lineno}" if frames else ""
    return {"error_type": type(exc).__name__, "at": where}


async def heartbeat() -> None:
    log.info("worker.heartbeat")


async def startup_checks() -> None:
    """Once per start — which is once per upgrade, since an upgrade is a pull and a
    restart (ADR-0047): bring rates and derived caches up to date, then verify the
    data.

    In that order, because a check that read a stale rate table or stale caches
    would report the upgrade's own lag as a finding: the FX fetch first (when it
    is on — ADR-0046), then every household's cached base amounts (the cache
    follows the rate table *and* the rule that reads it), then the checks.

    **The log carries shape only** — schema revision, check ids, statuses, counts
    and, when something crashes, the exception's type and line (`failure`). Never
    its message: a database error's message carries the statement's parameters,
    which are account names and amounts. Those stay in the database, where
    `GET /checks` serves them to the household's own session (Admin → Data
    checks). Never raises into the scheduler, and a failed check changes nothing
    about how the worker runs: it is a finding for a person, not a reason to stop
    syncing.
    """
    settings = get_settings()
    if settings.fx_fetch_enabled:
        await refresh_fx()
    try:
        await fx_fetch.recompute_all()
    except Exception as exc:  # noqa: BLE001
        log.error("worker.fx_recompute.failed", **failure(exc))
    try:
        async with unscoped_session() as session:
            version = await checks.schema_version(session)
            rows = (await session.execute(text("SELECT id FROM households"))).all()
    except Exception as exc:  # noqa: BLE001
        log.error("checks.crashed", **failure(exc))
        return
    log.info("checks.schema", version=version, households=len(rows))
    for (household_id,) in rows:
        try:
            async with scoped_session(household_id) as session:
                results = await checks.run(session, household_id)
        except Exception as exc:  # noqa: BLE001
            log.error("checks.crashed", household_id=str(household_id), **failure(exc))
            continue
        shape = {c.id: c.log_shape() for c in results}
        worst = next(
            (s for s in ("fail", "warn") if any(c.status == s for c in results)), "ok"
        )
        log_at = log.warning if worst != "ok" else log.info
        log_at("checks.completed", household_id=str(household_id), result=worst, checks=shape)


async def refresh_fx() -> None:
    """The daily FX fetch (ADR-0046). Never raises into the scheduler: a source
    that is down today is asked again tomorrow."""
    try:
        await fx_fetch.refresh_all(url=get_settings().fx_url)
    except Exception as exc:  # noqa: BLE001 — one bad day must not take the job with it
        log.error("worker.fx_refresh.failed", **failure(exc))


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)
    log.info("worker.startup", env=settings.env)

    stop = asyncio.Event()

    def _handle_signal() -> None:
        log.info("worker.shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    # Registered *before* the database wait, not after: the wait is the one place
    # this process can legitimately spend two minutes doing nothing, and a
    # `docker compose stop worker` during it should land immediately.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await wait_for_database(stop)

    scheduler = AsyncIOScheduler()
    # `next_run_time=now` on both: a container that has just started is a
    # container that may have been down, so the first thing it does is look for
    # work rather than wait out an interval. Without it, a crash loop would run
    # neither the tick nor the reaper, which is the state that most needs them.
    now = datetime.now(UTC)
    scheduler.add_job(
        heartbeat, "interval", minutes=HEARTBEAT_MINUTES, id="heartbeat",
        next_run_time=now,
    )
    scheduler.add_job(
        enqueue_due, "interval", minutes=SYNC_TICK_MINUTES, id="enqueue_due",
        next_run_time=now,
    )
    scheduler.add_job(
        reap, "interval", minutes=REAP_TICK_MINUTES, id="reap", next_run_time=now,
    )
    scheduler.add_job(startup_checks, "date", run_date=now, id="startup_checks")
    if settings.fx_fetch_enabled:
        # The first fetch is `startup_checks`'s, so the checks read today's rates;
        # this one is the daily repeat.
        scheduler.add_job(refresh_fx, "interval", hours=FX_REFRESH_HOURS, id="fx_refresh")
    log.info("worker.fx_refresh", enabled=settings.fx_fetch_enabled)
    scheduler.start()

    consumer = asyncio.create_task(consume(stop), name="sync-consumer")

    def _consumer_died(task: asyncio.Task) -> None:
        if task.cancelled() or stop.is_set():
            return
        # Not suppressed into a log line. If the consumer is gone, `enqueue_due`
        # and `reap` keep running and keep *appearing* healthy while nothing ever
        # executes — a queue that grows forever with no error anywhere. Exiting
        # turns that into a restart, which is at least visible.
        log.error(
            "worker.consumer.died",
            error=sanitize(str(task.exception()) if task.exception() else "cancelled"),
        )
        stop.set()

    consumer.add_done_callback(_consumer_died)

    await stop.wait()

    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer
    scheduler.shutdown(wait=False)
    log.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
