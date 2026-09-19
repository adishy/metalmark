"""Background worker entrypoint.

Phase 0: a running APScheduler with a heartbeat. Phase 2 adds the sync-job
consumer (``sync_jobs`` claimed with FOR UPDATE SKIP LOCKED), the cron full
sync, the rules engine on ingest, and daily balance snapshots. The worker sets
``SET LOCAL app.household_id`` per job so RLS applies to it exactly as to the
API (ARCHITECTURE §5) — it must NOT run BYPASSRLS.
"""

from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.logging import configure_logging, get_logger
from app.settings import get_settings

log = get_logger("worker")


async def heartbeat() -> None:
    log.info("worker.heartbeat")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)
    log.info("worker.startup", env=settings.env)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    # Phase 2: scheduler.add_job(enqueue_scheduled_syncs, "interval", hours=6)
    scheduler.start()

    stop = asyncio.Event()

    def _handle_signal() -> None:
        log.info("worker.shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await stop.wait()
    scheduler.shutdown(wait=False)
    log.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
