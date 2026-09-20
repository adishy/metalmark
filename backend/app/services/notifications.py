"""Out-of-band notice when a bank connection breaks (ADR-0028).

Nothing here is load-bearing. A sync succeeds or fails on the bank's answer and
never on whether a webhook answered, so every failure in this module is swallowed,
the timeout is short, and the default is a **no-op**: an instance with no
``METALMARK_NOTIFY_WEBHOOK_URL`` sends nothing and says so at debug level.

**Transition-only, not every failure.** A connection whose credential was revoked
keeps failing: the cron retries it, and the next cron retries it again. Notifying
on each one turns a single, actionable fact ("your bank access is broken") into a
stream that is indistinguishable from noise, and the stream is what trains someone
to ignore the notification that matters. So a notification is sent when the
failure is *new* — the previous run did not fail — with a slow reminder for an
outage that outlasts a day, because "we already told you once" is not a reason to
go silent for a week.

The decision is a pure function (:func:`should_notify`) and the delivery is a
separate call, deliberately. The decision has to read the connection's history, and
that read belongs inside the transaction that is already open; the delivery is a
network call with a timeout, and that belongs **outside** every transaction — a
webhook that is slow must not hold the row locks of the run that triggered it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import AccountConnection, SyncRun
from app.security.redact import sanitize
from app.settings import get_settings

log = get_logger("notifications")

#: How long a repeat of an already-reported outage may go unreported. The
#: reminder exists so a week-long break is not a week of silence; it is
#: deliberately much longer than any cadence, so at the default 24 h interval a
#: broken connection notifies about once a day rather than once per attempt.
REPEAT_AFTER = timedelta(hours=24)

#: Short on purpose. This runs in the worker's process, and a webhook that hangs
#: must not become a connection that never retries.
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Trouble:
    """One thing worth telling somebody, built inside a transaction, sent after."""

    event: str  # connection.auth_error | connection.error
    household_id: uuid.UUID
    connection_id: uuid.UUID
    org_name: str | None
    message: str

    def payload(self) -> dict:
        """The JSON body. Sanitized here as well as at the call site, because
        this leaves the process — and "it was sanitized upstream" is the
        assumption that stops being true when a second caller appears."""
        return {
            "event": self.event,
            "household_id": str(self.household_id),
            "connection_id": str(self.connection_id),
            "org_name": self.org_name,
            "message": sanitize(self.message),
        }


def should_notify(previous: SyncRun | None, *, now: datetime) -> bool:
    """Whether this failure is news.

    ``previous`` is the run before this one for the same connection, or ``None``
    if there is none. Three ways to be news:

    * **No history.** The connection's first run failed. Nothing has ever been
      said about it, so there is nothing to be repeating.
    * **A transition.** The previous run did not fail. Whatever is wrong now is
      new — including a connection that was working this morning.
    * **A stale reminder.** It has been failing since before ``REPEAT_AFTER``,
      and a long outage is worth one more mention.

    Anything else is the same failure being re-reported, which is the case the
    function exists to suppress.
    """
    if previous is None:
        return True
    if previous.status != "error":
        return True
    return (now - previous.started_at) >= REPEAT_AFTER


async def previous_run(
    session: AsyncSession, connection_id: uuid.UUID, run_id: uuid.UUID
) -> SyncRun | None:
    """The run before this one, which is what ``should_notify`` reads.

    ``started_at`` rather than ``created_at``: it is stamped by the caller (see
    ``run_connection_sync``'s TX1) with the run's own clock, while a transaction
    timestamp would be identical for runs sharing one — and ordering by a
    timestamp that ties is an arbitrary pick, which for this function means an
    arbitrary notification.
    """
    return (
        await session.execute(
            select(SyncRun)
            .where(SyncRun.connection_id == connection_id, SyncRun.id != run_id)
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def trouble_for(
    session: AsyncSession,
    *,
    connection: AccountConnection,
    run_id: uuid.UUID,
    status: str,
    message: str,
    now: datetime | None = None,
) -> Trouble | None:
    """The ``Trouble`` to send, or ``None`` if this is not news.

    Called inside the transaction that is about to record the failure, because
    that is where the connection's history is readable. It performs no I/O
    beyond that read — :func:`deliver` is what leaves the process, and the caller
    must keep the two apart.
    """
    moment = now or datetime.now(UTC)
    if not should_notify(await previous_run(session, connection.id, run_id), now=moment):
        return None
    return Trouble(
        event=f"connection.{status}",
        household_id=connection.household_id,
        connection_id=connection.id,
        org_name=connection.org_name,
        message=message,
    )


class Notifier:
    """Posts a ``Trouble`` to a webhook, or does nothing at all.

    Constructed per delivery rather than held: notifications are rare (at most a
    few a day), so the cost of building a client is nothing next to the cost of
    owning one in a process that can be killed at any moment with it open.
    """

    def __init__(
        self,
        url: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def send(self, trouble: Trouble) -> bool:
        """Deliver, or report that it was not delivered. Never raises.

        ``False`` covers both "there is no webhook configured" and "the webhook
        refused it", and the caller does not distinguish: neither is a reason for
        anything to change about the sync that produced it.
        """
        if not self.url:
            log.debug("notification.skipped", reason="no webhook configured")
            return False
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(self.url, json=trouble.payload())
        except Exception as exc:  # noqa: BLE001 — a notification may never fail a sync
            # The message, not the exception: an httpx error stringifies the
            # request it failed on, and the webhook URL is a credential.
            log.warning(
                "notification.failed",
                connection_id=str(trouble.connection_id),
                error=sanitize(f"{type(exc).__name__}: {exc}"),
            )
            return False
        if response.status_code >= 400:
            log.warning(
                "notification.rejected",
                connection_id=str(trouble.connection_id),
                http_status=response.status_code,
            )
            return False
        # ``trouble``, not ``event``: structlog's first positional *is* the event,
        # so ``event=`` is a kwarg collision rather than a field.
        log.info(
            "notification.sent",
            trouble=trouble.event,
            connection_id=str(trouble.connection_id),
        )
        return True


def get_notifier() -> Notifier:
    """The process's notifier. A no-op unless a webhook is configured.

    A function rather than a module constant so a test can substitute one without
    the settings cache having to know about it, and so the settings are read at
    delivery time rather than import time — a worker that started before the
    variable was set picks it up on the next failure instead of needing a restart.
    """
    return Notifier(get_settings().notify_webhook_url)


async def deliver(trouble: Trouble) -> bool:
    """Send through the process notifier. Call this **after** the transaction."""
    return await get_notifier().send(trouble)
