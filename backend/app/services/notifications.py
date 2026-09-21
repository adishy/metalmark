"""Notices when a bank connection breaks — the decision, the record, and the read.

Two sinks and one decision (ADR-0037). The decision is :func:`should_notify`; the
sinks are the webhook (ADR-0028) and the in-app notification the browser shows.
The webhook is *delivered* here and the in-app notice is **recorded** here and read
back later by :func:`list_notices`, because the browser cannot be pushed to: it
polls a row that says a notice was decided. That is the whole reason the record
exists — a browser re-deriving `should_notify` in TypeScript would drift from it,
and it would drift towards notifying too often.

Nothing here is load-bearing for a sync. A sync succeeds or fails on the bank's
answer and never on whether a webhook answered, so every failure in the delivery
half is swallowed, the timeout is short, and the default is a **no-op**: an
instance with no ``METALMARK_NOTIFY_WEBHOOK_URL`` sends nothing and says so at
debug level. Recording happens regardless of the webhook, which is what makes the
in-app sink work on an instance that configured nothing.

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
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import AccountConnection, SyncRun, SyncRunEvent
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

#: The ``sync_run_events.event`` that records a decided notice. Namespaced like
#: every other event the worker writes (``run.failed``, ``run.finished``) — ADR-0037
#: names the string ``notified``, and this is that decision wearing the naming the
#: timeline already uses.
NOTIFIED_EVENT = "run.notified"

#: The event's title when a row somehow has none. A notice with no title is not
#: worth a notification, but it is worth *something* — the alternative is a
#: notification whose first line is blank, on a row that only exists because the
#: worker decided to notify.
NO_TITLE = "Bank connection problem"

#: One poll's worth. A poll is not an archive: this is a handful of things that
#: broke recently, and a household past this many unnotified failures is one that
#: is not reading its notifications anyway.
NOTICE_LIMIT = 50


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

    def notice(self) -> dict:
        """The detail of the ``run.notified`` row — what the browser will show.

        A second shape rather than the payload above, because the two sinks read
        differently: a webhook gets fields, a desktop notification gets a title and
        a line of body text, and composing that line here is what keeps the same
        facts in one place. It carries **the same facts and no more** — the
        institution and what is wrong with it (ADR-0037 §6). No balance, no
        merchant, no amount, no account number: a notification is read over a
        shoulder, and it is also stored in the browser that showed it, which is the
        one place this household's bank activity must not end up.

        ``connection_id`` goes with it so the browser can `tag` the notification —
        a repeat for the same connection replaces the standing one instead of
        stacking a column of them — and so clicking it can route to that
        connection rather than to the panel in general.

        The trouble's own name is ``trouble`` here and ``event`` in the webhook
        payload, and neither spelling is a typo. The detail is splatted into
        ``RunLog.emit(level, event, **detail)``, so a key named ``event`` is a
        duplicate keyword argument and a ``TypeError`` at the one moment a
        connection has just broken — the same collision ``Notifier.send`` records
        for structlog's positional event. And the row already *has* an ``event``
        column, whose value is ``run.notified``: a second ``event`` inside its
        detail, saying something else, is a worse name than a different one.
        """
        who = self.org_name or "A bank connection"
        return {
            "title": NO_TITLE,
            "body": f"{who}: {sanitize(self.message)}",
            "connection_id": str(self.connection_id),
            "trouble": self.event,
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


@dataclass(frozen=True, slots=True)
class Notice:
    """A decided notice, read back out of the run event that recorded it.

    Flattened rather than handed to the client as ``event.detail``: the browser
    should not have to know that a notice's body lives at ``detail["body"]``, and
    the row's shape is the worker's business — this is the one place that reads it.
    """

    id: uuid.UUID
    run_id: uuid.UUID
    ts: datetime
    connection_id: uuid.UUID | None
    title: str
    body: str


def notice_from(event: SyncRunEvent) -> Notice:
    """Read one ``run.notified`` row. Tolerant of a detail that is missing keys.

    Written at write time by :meth:`Trouble.notice`, so the keys are always there
    in practice — but this is a read path over stored rows, and a row from an older
    version of that method must not be a 500. A notice with no title still has a
    title; one with no connection id still shows.
    """
    detail = event.detail or {}
    return Notice(
        id=event.id,
        run_id=event.sync_run_id,
        ts=event.ts,
        connection_id=_uuid_or_none(detail.get("connection_id")),
        title=str(detail.get("title") or NO_TITLE),
        body=str(detail.get("body") or ""),
    )


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def list_notices(
    session: AsyncSession, *, since: uuid.UUID | None, limit: int = NOTICE_LIMIT
) -> list[Notice]:
    """Notices after ``since``, oldest first — the browser's poll (ADR-0037).

    ``since`` is the id of the last notice a browser showed, and it is an id the
    caller cannot order by: ``sync_run_events`` is keyed by UUID and its ``ts`` is
    the *transaction* timestamp, so every event in one run shares it exactly (see
    ``SyncRunEvent``). So the cursor is **resolved, not compared** — read that
    row's ``(ts, id)`` and return what is after that pair, which is the total order
    the two give together.

    A cursor that resolves to nothing — pruned, mistyped, or another household's,
    which RLS hides — is treated as **no cursor at all**. That re-shows the recent
    window rather than showing nothing, and it is the right way round: the failure
    mode of a lost cursor is a repeated notification, and the failure mode of
    ignoring an unknown one is silence about a broken bank.
    """
    stmt = select(SyncRunEvent).where(SyncRunEvent.event == NOTIFIED_EVENT)
    if since is not None:
        anchor = (
            await session.execute(
                select(SyncRunEvent.ts).where(
                    SyncRunEvent.id == since, SyncRunEvent.event == NOTIFIED_EVENT
                )
            )
        ).scalar_one_or_none()
        if anchor is not None:
            stmt = stmt.where(tuple_(SyncRunEvent.ts, SyncRunEvent.id) > tuple_(anchor, since))
    rows = (
        await session.execute(stmt.order_by(SyncRunEvent.ts, SyncRunEvent.id).limit(limit))
    ).scalars().all()
    return [notice_from(row) for row in rows]


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
