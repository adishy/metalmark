"""When a broken connection is worth telling somebody about.

The bar is not "does it send" — it is **"does it stop sending"**. A connection
whose credential was revoked fails on every cron tick, and a notification per
tick is the thing that trains a person to ignore the one that mattered. So the
integration tests here count deliveries across *runs*, not across calls: one for
the failure that was new, none for the repeat, one again when it recovers and
breaks a second time.

Everything is driven through the real ``run_connection_sync`` against
``FakeProvider``, and the deliveries go through the real ``Notifier`` over an
``httpx.MockTransport`` — so the payload that is asserted on is the payload that
would have been posted, sanitization included.

**Two sinks, one decision.** The webhook is delivered and counted in the first
half; the in-app notice is a ``sync_run_events`` row that the browser polls for
(ADR-0037), counted in the second. They are counted *together*, per run, because
the thing that must never happen is the two disagreeing about what is news — so
the two halves of the same failure are asserted in the same tests wherever that
is possible, and the record's own cursor gets tests of its own at the bottom.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import AccountConnection, SyncRun, SyncRunEvent
from app.security.crypto import SecretBox
from app.services import notifications, sync
from app.services.aggregator import ProviderError
from app.services.fake_simplefin import FAKE_ACCESS_URL, FakeProvider
from app.settings import get_settings
from tests.fakes import simplefin as scenarios

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
WEBHOOK = "https://hooks.example.test/metalmark"

#: The credential the provider is handed, in the one form a leaked payload would
#: carry it. Asserted absent from every webhook body.
SECRET = FAKE_ACCESS_URL


class _Hook:
    """A webhook that records what it was sent, through the real ``Notifier``."""

    def __init__(self, *, status: int = 200) -> None:
        self.payloads: list[dict] = []
        self.status = status
        self.raises: Exception | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            self.payloads.append(json.loads(request.content))
            if self.raises is not None:
                raise self.raises
            return httpx.Response(self.status, json={"ok": True})

        self._transport = httpx.MockTransport(handler)

    def notifier(self) -> notifications.Notifier:
        return notifications.Notifier(WEBHOOK, transport=self._transport)


@pytest.fixture
def hook(monkeypatch) -> _Hook:
    """Every delivery in a test goes to one recorded webhook."""
    hook = _Hook()
    monkeypatch.setattr(notifications, "get_notifier", hook.notifier)
    return hook


@pytest.fixture
async def household(household_factory):
    return await household_factory("Notify")


async def _make_connection(household_id, *, name="SimpleFIN Bridge") -> uuid.UUID:
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id,
            provider="fake",
            access_url_encrypted=SecretBox(get_settings().secret_key).encrypt(FAKE_ACCESS_URL),
            org_name=name,
        )
        session.add(connection)
        await session.flush()
        return connection.id


async def _sync(household_id, connection_id, provider, *, now=NOW) -> sync.SyncOutcome:
    return await sync.run_connection_sync(
        household_id, connection_id, provider=provider, now=now
    )


def _revoked() -> FakeProvider:
    """A fetch that comes back 403 — a revoked access URL, which is ``auth``."""
    return FakeProvider(
        raise_on_fetch=ProviderError(
            "the bridge refused the stored credential", kind="auth", status=403
        )
    )


def _auth_errlist() -> FakeProvider:
    """A **200** whose ``errlist`` says the connection's access was revoked.

    The other way this is learned, and the one a naive implementation misses
    because the HTTP call succeeded.
    """
    return FakeProvider(script=[scenarios.with_errlist(scenarios.demo(), scenarios.ERR_AUTH)])


def _healthy() -> FakeProvider:
    return FakeProvider(script=[scenarios.demo()])


async def _connection_status(household_id, connection_id) -> str:
    async with scoped_session(household_id) as session:
        connection = await session.get(AccountConnection, connection_id)
        return connection.status


async def _notified_rows(session) -> list[SyncRunEvent]:
    """Every notice on record, in the order the poll returns them.

    Ordered by the ``(ts, id)`` pair rather than by ``seq``, because that pair is
    the poll's own total order — a helper that sorted differently would let a
    broken cursor pass by agreeing with the wrong expectation.
    """
    return list(
        (
            await session.execute(
                select(SyncRunEvent)
                .where(SyncRunEvent.event == notifications.NOTIFIED_EVENT)
                .order_by(SyncRunEvent.ts, SyncRunEvent.id)
            )
        ).scalars().all()
    )


async def _record(household_id, count=1, *, body="the bridge refused it", ts=None):
    """A failed run carrying ``count`` notices. Returns ``(run_id, notice_ids)``.

    Written in **one transaction**, deliberately: ``SyncRunEvent.ts`` is the
    *transaction* timestamp (the model's docstring says so), so notices decided by
    a single run share it to the microsecond. That is the case the poll's cursor
    exists for, and rows written any other way would only exercise the easy one.
    ``ts`` is opt-in for the tests that need two runs apart from each other.
    """
    async with scoped_session(household_id) as session:
        run = SyncRun(
            household_id=household_id, trigger="cron", status="error", started_at=NOW
        )
        session.add(run)
        await session.flush()
        rows = []
        for seq in range(count):
            row = SyncRunEvent(
                household_id=household_id,
                sync_run_id=run.id,
                seq=seq,
                level="info",
                event=notifications.NOTIFIED_EVENT,
                detail={
                    "title": notifications.NO_TITLE,
                    "body": f"{body} #{seq}",
                    "connection_id": str(uuid.uuid4()),
                    "trouble": "connection.auth_error",
                },
                **({} if ts is None else {"ts": ts}),
            )
            session.add(row)
            rows.append(row)
        await session.flush()
        return run.id, [row.id for row in rows]


# ---- the rule --------------------------------------------------------------


def test_no_history_is_news():
    assert notifications.should_notify(None, now=NOW) is True


@pytest.mark.parametrize(
    ("previous_status", "age_hours", "expected"),
    [
        ("ok", 0, True),        # a transition into failure: the case to catch
        ("partial", 0, True),   # ... including from a run that mostly worked
        ("error", 0, False),    # the same failure again: the case to suppress
        ("error", 23, False),   # still the same failure, still suppressed
        ("error", 25, True),    # an outage that outlasted a day: say so again
    ],
)
def test_only_a_new_failure_is_news(previous_status, age_hours, expected):
    previous = SyncRun(
        household_id=uuid.uuid4(), trigger="cron", status=previous_status,
        started_at=NOW - timedelta(hours=age_hours),
    )
    assert notifications.should_notify(previous, now=NOW) is expected


def test_the_payload_is_sanitized_before_it_leaves_the_process():
    """This is the one thing here that goes to a third party. The URL is a
    credential and the message is whatever the bridge said, so the body is
    redacted at the boundary rather than trusted to have been redacted upstream."""
    trouble = notifications.Trouble(
        event="connection.auth_error",
        household_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        org_name="SimpleFIN Bridge",
        message=f"403 from {SECRET} — the credential was refused",
    )
    body = json.dumps(trouble.payload())
    assert "fake-password" not in body
    assert "[redacted]" in trouble.payload()["message"]


def test_the_notice_carries_the_facts_and_not_the_money():
    """ADR-0037 §6. This one is read over a shoulder and then kept in the browser
    that showed it, which is the one place this household's bank activity must not
    end up. So it says the institution and what is wrong with it, and the
    sanitizing is the same sanitizing the webhook gets — the second sink is not a
    reason to re-derive the first sink's rules."""
    trouble = notifications.Trouble(
        event="connection.auth_error",
        household_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        org_name="SimpleFIN Bridge",
        message=f"403 from {SECRET} — the credential was refused",
    )

    notice = trouble.notice()

    assert notice["title"] == notifications.NO_TITLE
    assert notice["body"].startswith("SimpleFIN Bridge: ")
    assert "[redacted]" in notice["body"]
    assert "fake-password" not in json.dumps(notice)


def test_the_notice_fits_the_log_line_it_is_written_into():
    """``notice()`` is not a free-standing payload — it is splatted straight into
    ``RunLog.emit(level, event, **detail)``, so a key that collides with one of
    that signature's names is a ``TypeError`` in the failure path, which is the
    one place a notification has to work. It was called ``event`` until this test
    existed.

    Asserted against the signature rather than by emitting, so it stays true for
    the next key somebody adds.
    """
    notice = notifications.Trouble(
        event="connection.auth_error", household_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), org_name=None, message="broken",
    ).notice()

    assert set(notice).isdisjoint(inspect.signature(sync.RunLog.emit).parameters)
    assert notice["trouble"] == "connection.auth_error"


def test_a_notice_for_an_unnamed_connection_still_says_something():
    """``org_name`` is nullable — a connection whose claim never completed has
    none — and the alternative to a fallback here is a notification whose first
    words are blank."""
    trouble = notifications.Trouble(
        event="connection.error", household_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), org_name=None, message="broken",
    )
    assert trouble.notice()["body"] == "A bank connection: broken"


async def test_an_unconfigured_instance_sends_nothing():
    """The default. A no-op, not an error, and not a log line per sync."""
    assert await notifications.Notifier(None).send(
        notifications.Trouble(
            event="connection.auth_error", household_id=uuid.uuid4(),
            connection_id=uuid.uuid4(), org_name=None, message="broken",
        )
    ) is False


# ---- end to end ------------------------------------------------------------


async def test_a_revoked_credential_notifies_once_and_not_again(household, hook):
    """The bar. Two failing runs, one notification.

    Both runs are just as broken as each other. What separates them is that the
    first is news and the second is the same news — and the second one is what
    the whole rule exists for, because a revoked credential fails on every tick
    from here until somebody reconnects.
    """
    connection_id = await _make_connection(household)

    first = await _sync(household, connection_id, _revoked())
    assert first.status == "error"
    assert await _connection_status(household, connection_id) == "auth_error"

    second = await _sync(household, connection_id, _revoked(), now=NOW + timedelta(hours=1))
    assert second.status == "error"

    assert len(hook.payloads) == 1
    payload = hook.payloads[0]
    assert payload["event"] == "connection.auth_error"
    assert payload["connection_id"] == str(connection_id)
    assert payload["org_name"] == "SimpleFIN Bridge"
    assert SECRET not in json.dumps(payload)

    # The same one, counted at the other sink. A row per run here would be the
    # same failure the webhook is suppressing, arriving as a desktop notification
    # instead of a POST.
    async with scoped_session(household) as session:
        assert len(await _notified_rows(session)) == 1


async def test_the_decision_is_recorded_where_the_browser_can_read_it(household, hook):
    """The in-app sink, end to end: one failing run, one webhook payload, one row.

    The browser cannot be pushed to, so it polls for the record of a decision the
    worker already made (ADR-0037 §2) — which means the row is written exactly
    when the webhook is sent, by the same ``should_notify``. The two sinks are
    asserted against *each other* here rather than each against its own
    expectation, because "they carry the same facts" is the property that would
    break silently if the notice ever grew a second implementation.
    """
    connection_id = await _make_connection(household)

    first = await _sync(household, connection_id, _revoked())

    assert len(hook.payloads) == 1
    async with scoped_session(household) as session:
        (row,) = await _notified_rows(session)

    assert row.sync_run_id == first.run_id, "the record files itself under its run"
    assert row.level == "info", "a notice is not a failure of the run"
    notice = notifications.notice_from(row)
    payload = hook.payloads[0]
    assert notice.connection_id == connection_id
    assert notice.title == notifications.NO_TITLE
    assert notice.body == f"{payload['org_name']}: {payload['message']}"
    assert SECRET not in json.dumps(row.detail)


async def test_the_record_does_not_depend_on_a_webhook(household, monkeypatch):
    """An instance that configured nothing still notifies in-app: recording is
    unconditional, and only the delivery is gated on the webhook URL. This is the
    whole reason the decision is recorded rather than inferred — the alternative
    is a household with no webhook getting no notification at all."""
    monkeypatch.setattr(notifications, "get_notifier", lambda: notifications.Notifier(None))
    connection_id = await _make_connection(household)

    assert (await _sync(household, connection_id, _revoked())).status == "error"

    async with scoped_session(household) as session:
        assert len(await _notified_rows(session)) == 1


async def test_the_same_failure_is_reported_again_after_a_day(household, hook):
    """Silence for a week is the other failure mode. An outage that outlasts
    ``REPEAT_AFTER`` gets said once more."""
    connection_id = await _make_connection(household)

    await _sync(household, connection_id, _revoked())
    await _sync(household, connection_id, _revoked(), now=NOW + timedelta(hours=25))

    assert len(hook.payloads) == 2
    async with scoped_session(household) as session:
        assert len(await _notified_rows(session)) == 2


async def test_recovery_then_another_failure_is_news_again(household, hook):
    """The reason the rule reads the *previous run* rather than a "we have
    notified" flag: a connection that breaks, is fixed, and breaks again is a
    new fact each time, and the person being told has no other way to know the
    fix stopped working."""
    connection_id = await _make_connection(household)

    assert (await _sync(household, connection_id, _revoked())).status == "error"
    assert (await _sync(
        household, connection_id, _healthy(), now=NOW + timedelta(hours=1)
    )).status == "ok"
    assert (await _sync(
        household, connection_id, _revoked(), now=NOW + timedelta(hours=2)
    )).status == "error"

    assert [p["event"] for p in hook.payloads] == [
        "connection.auth_error",
        "connection.auth_error",
    ]
    async with scoped_session(household) as session:
        assert len(await _notified_rows(session)) == 2


async def test_a_connection_revoked_on_a_200_is_notified_too(household, hook):
    """The errlist path: the bridge answers 200 and says in the body that the
    connection is gone. Same conclusion, and it reaches the same status column —
    so it has to reach the same notification."""
    connection_id = await _make_connection(household)

    outcome = await _sync(household, connection_id, _auth_errlist())

    assert outcome.status == "error"
    assert await _connection_status(household, connection_id) == "auth_error"
    assert len(hook.payloads) == 1
    assert hook.payloads[0]["event"] == "connection.auth_error"
    async with scoped_session(household) as session:
        assert len(await _notified_rows(session)) == 1


async def test_our_own_failure_is_not_a_notification(household, hook):
    """A timeout or a 5xx is our problem and the worker retries it on its own. The
    connection's health is deliberately left alone (see ``_finish_failed``), and
    a notification would contradict that — it would say a bank the app cannot
    reach is a bank that is broken."""
    connection_id = await _make_connection(household)
    provider = FakeProvider(
        raise_on_fetch=ProviderError("the bridge timed out", kind="transient")
    )

    outcome = await _sync(household, connection_id, provider)

    assert outcome.status == "error"
    assert await _connection_status(household, connection_id) == "ok"
    assert hook.payloads == []
    async with scoped_session(household) as session:
        assert await _notified_rows(session) == []


async def test_a_healthy_sync_never_notifies(household, hook):
    connection_id = await _make_connection(household)

    assert (await _sync(household, connection_id, _healthy())).status == "ok"

    assert await _connection_status(household, connection_id) == "ok"
    assert hook.payloads == []
    async with scoped_session(household) as session:
        assert await _notified_rows(session) == []


async def test_a_webhook_that_fails_does_not_fail_the_sync(household, monkeypatch):
    """The one invariant this module must never break: a notification is
    out-of-band, so nothing about the sync may depend on it arriving."""
    connection_id = await _make_connection(household)
    hook = _Hook()
    hook.raises = httpx.ConnectError("no route to host")
    monkeypatch.setattr(notifications, "get_notifier", hook.notifier)

    outcome = await _sync(household, connection_id, _revoked())

    # The run did its job: the failure is recorded where the dashboard reads it.
    assert outcome.status == "error"
    assert await _connection_status(household, connection_id) == "auth_error"
    async with scoped_session(household) as session:
        run = (await session.execute(select(SyncRun))).scalars().one()
        assert run.status == "error"
        assert run.finished_at is not None
        # And the in-app notice survived it too. The two sinks are independent on
        # purpose: a dead webhook is not a reason for the browser to hear nothing.
        assert len(await _notified_rows(session)) == 1


async def test_a_rejected_webhook_is_not_an_error_either():
    """5xx from the webhook: reported as "not delivered", and nothing more. The
    caller has no useful response to it, and treating it as a failure would give
    it one."""
    hook = _Hook(status=500)
    trouble = notifications.Trouble(
        event="connection.error", household_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), org_name=None, message="broken",
    )

    assert await hook.notifier().send(trouble) is False
    assert len(hook.payloads) == 1  # it was attempted, and rejected


# ---- reading the record back -----------------------------------------------
#
# The browser holds one opaque id in its `localStorage` and asks what came after
# it. Everything below is about that id: that it orders, that it does not lose a
# sibling, and that one it cannot resolve is not a reason to go silent.


async def test_a_poll_comes_back_oldest_first(household):
    """A feed, read forwards. The browser shows these in order, so a poll that
    returned yesterday's failure last would show it as the newest thing."""
    _, older = await _record(household, body="the older one", ts=NOW)
    _, newer = await _record(household, body="the newer one", ts=NOW + timedelta(hours=1))

    async with scoped_session(household) as session:
        notices = await notifications.list_notices(session, since=None)

    assert [n.id for n in notices] == [older[0], newer[0]]
    assert [n.body for n in notices] == ["the older one #0", "the newer one #0"]


async def test_a_cursor_returns_only_what_follows_it(household):
    """Three notices from **one run**, so they share a ``ts`` exactly.

    This is what the cursor is *resolved* rather than compared for. Both obvious
    comparisons were tried against this test and each fails, in opposite
    directions: ``ts > since`` returns ``[]`` instead of the two siblings — a
    notification the user never sees, and silent — while ``ts >= since`` re-shows
    the cursor's run on every poll from now on. Nothing else in this file would
    notice either.
    """
    _, _ids = await _record(household, count=3)

    async with scoped_session(household) as session:
        rows = await _notified_rows(session)
        assert len({row.ts for row in rows}) == 1, "the premise: one transaction, one ts"

        after = await notifications.list_notices(session, since=rows[0].id)

    assert [n.id for n in after] == [row.id for row in rows[1:]]


async def test_a_poll_at_the_newest_notice_is_empty(household):
    """The steady state. A browser that has seen everything asks again and is told
    nothing; a row here would be the user being re-notified about a failure they
    have already dismissed."""
    _, ids = await _record(household)

    async with scoped_session(household) as session:
        assert await notifications.list_notices(session, since=ids[0]) == []


async def test_an_unknown_cursor_shows_the_window_again(household):
    """Pruned, mistyped, or never existed. Treated as no cursor at all, and that
    is the right way round: the cost of a lost cursor is one repeated
    notification, while the cost of honouring one that resolves to nothing is
    silence about a bank that is broken."""
    await _record(household, count=2)

    async with scoped_session(household) as session:
        rows = await _notified_rows(session)
        notices = await notifications.list_notices(session, since=uuid.uuid4())

    assert [n.id for n in notices] == [row.id for row in rows]


async def test_another_households_cursor_is_not_a_cursor(household_factory):
    """RLS hides the anchor, so B cannot use A's notice id to seek into A's feed.
    It also must not be an error or an empty list: B gets B's window, which is
    what "no cursor" means."""
    a = await household_factory("A")
    b = await household_factory("B")
    _, a_ids = await _record(a)
    await _record(b)

    async with scoped_session(b) as session:
        rows = await _notified_rows(session)
        notices = await notifications.list_notices(session, since=a_ids[0])

    assert [n.id for n in notices] == [row.id for row in rows]

    async with scoped_session(a) as session:
        assert len(await notifications.list_notices(session, since=None)) == 1


async def test_a_cursor_on_a_log_line_is_not_a_cursor(household):
    """The subtlest way to hold a bad cursor: a real id of this household's, from
    this household's run — that happens to be a ``run.failed`` rather than a
    notice. The anchor query filters on the event, so it resolves to nothing and
    the window is shown, rather than to a position in a feed it is not part of."""
    async with scoped_session(household) as session:
        run = SyncRun(
            household_id=household, trigger="cron", status="error", started_at=NOW
        )
        session.add(run)
        await session.flush()
        failure = SyncRunEvent(
            household_id=household, sync_run_id=run.id, seq=0, level="error",
            event="run.failed", detail={"error": "403"},
        )
        session.add(failure)
        await session.flush()
        failure_id = failure.id

    await _record(household)

    async with scoped_session(household) as session:
        rows = await _notified_rows(session)
        with_it = await notifications.list_notices(session, since=None)
        at_it = await notifications.list_notices(session, since=failure_id)

    # The log line itself is not polled — a timeline row is not a notice — and a
    # cursor pointing at it is not a position either.
    assert [n.id for n in with_it] == [row.id for row in rows]
    assert [n.id for n in at_it] == [row.id for row in rows]


async def test_a_poll_is_bounded(household):
    """A poll is not an archive. A household broken for a year gets a window of
    what is recent, not a year of notifications in one response."""
    await _record(household, count=5)

    async with scoped_session(household) as session:
        rows = await _notified_rows(session)
        notices = await notifications.list_notices(session, since=None, limit=2)

    assert [n.id for n in notices] == [row.id for row in rows[:2]]


async def test_a_notice_row_that_lost_a_key_still_reads(household):
    """The read path is over **stored** rows, so a row written by an older version
    of ``Trouble.notice`` must not be a 500 — and must not be a notification with
    a blank first line either."""
    async with scoped_session(household) as session:
        run = SyncRun(
            household_id=household, trigger="cron", status="error", started_at=NOW
        )
        session.add(run)
        await session.flush()
        session.add(
            SyncRunEvent(
                household_id=household, sync_run_id=run.id, seq=0, level="info",
                event=notifications.NOTIFIED_EVENT,
                detail={"connection_id": "not-a-uuid"},
            )
        )

    async with scoped_session(household) as session:
        (notice,) = await notifications.list_notices(session, since=None)

    assert notice.title == notifications.NO_TITLE
    assert notice.body == ""
    assert notice.connection_id is None
