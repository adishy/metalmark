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
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import AccountConnection, SyncRun
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


async def test_the_same_failure_is_reported_again_after_a_day(household, hook):
    """Silence for a week is the other failure mode. An outage that outlasts
    ``REPEAT_AFTER`` gets said once more."""
    connection_id = await _make_connection(household)

    await _sync(household, connection_id, _revoked())
    await _sync(household, connection_id, _revoked(), now=NOW + timedelta(hours=25))

    assert len(hook.payloads) == 2


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


async def test_a_healthy_sync_never_notifies(household, hook):
    connection_id = await _make_connection(household)

    assert (await _sync(household, connection_id, _healthy())).status == "ok"

    assert await _connection_status(household, connection_id) == "ok"
    assert hook.payloads == []


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
