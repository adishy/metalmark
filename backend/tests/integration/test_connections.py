"""The connection lifecycle and the control panel's sync surface, over HTTP.

Two of these are security properties rather than features, and they are the
reason the file exists at all:

* **The credential never leaves.** A claim's access URL is a Basic-auth URL for
  the household's bank. It goes into ``SecretBox`` and appears in no response, no
  log record and no error column — including when a *claim fails*, which is the
  moment a naive implementation is most likely to print what it was given.
* **A 422 does not echo the request.** ``POST /connections/claim`` takes a
  single-use setup token; FastAPI's default validation handler puts the offending
  value in the response body, which would hand the token back to any proxy or
  client-side error reporter that sees it.

The rest is the operator's surface: pause, retune, queue, cancel, disconnect, and
the run log the dashboard reads. Disconnect gets its own test because it is the
one operation whose failure mode is silent — the accounts must survive *and* be
marked manual, and only the second half is easy to forget.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.deps import SESSION_COOKIE
from app.models import Account, AccountConnection, SyncJob, SyncRun, SyncRunEvent
from app.security.crypto import SecretBox
from app.services import connections as svc
from app.services.aggregator import ClaimResult, ProviderError
from app.services.errors import LedgerError
from app.services.fake_simplefin import FAKE_ACCESS_URL, FakeProvider
from app.settings import get_settings
from tests.fakes import simplefin as scenarios

pytestmark = pytest.mark.integration

CSRF = "X-CSRF-Token"
_UNSAFE = {"POST", "PATCH", "PUT", "DELETE"}

#: Stands in for a real setup token. Shaped like one (base64 of a URL) so a test
#: that accidentally asserts on its absence is asserting on something realistic.
SETUP_TOKEN = "aHR0cHM6Ly9icmlkZ2UuZXhhbXBsZS9zaW1wbGVmaW4vY2xhaW0vVE9LRU4="


# ---- the client ------------------------------------------------------------


class _Api:
    """A signed-in client, in miniature.

    ``test_api_owners`` has the full one. This is the third copy of these fifty
    lines and the honest thing to say is that a shared fixture is overdue — but
    it belongs to a commit that converts all three at once, not to this one.
    """

    def __init__(self, app):
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self._cookie: str | None = None
        self.csrf: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def login(self, email, password="password123"):
        resp = await self._http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        self._cookie = resp.cookies[SESSION_COOKIE]
        self.csrf = resp.json()["csrf_token"]
        return resp.json()

    async def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.csrf and method in _UNSAFE:
            headers[CSRF] = self.csrf
        if self._cookie:
            self._http.cookies.set(SESSION_COOKIE, self._cookie)
        return await self._http.request(method, url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


@pytest.fixture
async def api(monkeypatch):
    """A signed-in owner, a household of its own, and a claim that cannot dial out.

    ``get_provider`` is patched for every test in this file, not just the claim
    ones. The default provider is ``simplefin`` — the real bridge — and a test
    that forgot to inject a fake would attempt a genuine outbound request. Making
    the injection the fixture's job means no test can forget.
    """
    from app.db import unscoped_session
    from app.main import create_app
    from app.services import auth as auth_svc

    provider = FakeProvider()
    monkeypatch.setattr(svc, "get_provider", lambda _name=None: provider)

    email = f"{uuid.uuid4().hex[:8]}@example.com"
    async with unscoped_session() as s:
        household, user = await auth_svc.bootstrap_household(
            s, name="Connections", base_currency="USD", owner_email=email,
            owner_name="Alex", owner_password="password123",
        )
    async with _Api(create_app()) as client:
        await client.login(email)
        yield client, household.id, user.id, provider


async def _set_role(household_id, user_id, role):
    from app.db import unscoped_session
    from app.models import HouseholdMember

    async with unscoped_session() as s:
        membership = await s.get(HouseholdMember, (household_id, user_id))
        membership.role = role


async def _connect(client) -> dict:
    """Claim a connection through the API and return its JSON."""
    resp = await client.post("/connections/claim", json={"setup_token": SETUP_TOKEN})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- claim -----------------------------------------------------------------


async def test_a_claim_stores_the_credential_encrypted_and_returns_none_of_it(api):
    client, household_id, _, provider = api

    resp = await client.post("/connections/claim", json={"setup_token": SETUP_TOKEN})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    # The response is a connection, and nothing in it is the credential or any
    # piece of it. Asserted over the whole serialized body rather than field by
    # field, so a field added later cannot quietly reintroduce one.
    assert SETUP_TOKEN not in json.dumps(body)
    assert FAKE_ACCESS_URL not in json.dumps(body)

    async with scoped_session(household_id) as session:
        connection = await session.get(AccountConnection, uuid.UUID(body["id"]))
        assert connection.access_url_encrypted != FAKE_ACCESS_URL
        box = SecretBox(get_settings().secret_key)
        # Stored, recoverable, and only through the key.
        assert box.decrypt(connection.access_url_encrypted) == FAKE_ACCESS_URL

    assert provider.seen_setup_tokens == [SETUP_TOKEN]


async def test_the_claim_uses_the_default_provider_when_none_is_injected(api, monkeypatch):
    """The one place in the backend that has to guess which provider to use.

    Everywhere else the choice is read off the connection row; a connection that
    does not exist yet has no row to read, so ``METALMARK_SIMPLEFIN_PROVIDER``
    decides. Asserted because the guess is the difference between a claim that
    hits the bridge and one that hits nothing.
    """
    client, household_id, _, provider = api
    seen: list[str] = []
    monkeypatch.setattr(svc, "get_provider", lambda name: seen.append(name) or provider)

    await client.post("/connections/claim", json={"setup_token": SETUP_TOKEN})
    assert seen == [get_settings().simplefin_provider]


class _RefusingProvider(FakeProvider):
    """Claims fail with the bridge's own 403, sanitized as the real one is."""

    async def claim(self, setup_token: str) -> ClaimResult:
        self.seen_setup_tokens.append(setup_token)
        raise ProviderError(
            "setup token was already claimed or is not recognized",
            kind="auth",
            status=403,
            secrets=(setup_token,),
        )


async def test_a_used_setup_token_is_a_400_that_does_not_repeat_the_token(api, monkeypatch):
    """400, not 403: the request is allowed, the token is spent.

    That distinction is what makes the UI say "generate a new one" instead of
    "you may not do this". And whatever it says, it must not say the token.
    """
    client, _, _, _ = api
    monkeypatch.setattr(svc, "get_provider", lambda _name=None: _RefusingProvider())

    resp = await client.post("/connections/claim", json={"setup_token": SETUP_TOKEN})

    assert resp.status_code == 400, resp.text
    assert SETUP_TOKEN not in resp.text
    assert "already claimed" in resp.json()["detail"]

    async with scoped_session(api[1]) as session:
        assert (await session.execute(select(AccountConnection))).scalars().all() == []


async def test_the_provider_error_message_never_carries_the_credential(api, monkeypatch):
    """``ProviderError`` sanitizes in its constructor — pinned, because the
    message reaches an HTTP response, ``last_error`` and the run log."""
    client, _, _, _ = api
    monkeypatch.setattr(svc, "get_provider", lambda _name=None: _RefusingProvider())

    resp = await client.post("/connections/claim", json={"setup_token": SETUP_TOKEN})
    assert SETUP_TOKEN not in resp.json()["detail"]


async def test_a_validation_error_does_not_echo_the_setup_token(api):
    """The plan's leak site 3, closed globally in ``main.create_app``.

    FastAPI's stock 422 handler returns ``input`` per error, so a wrong-typed
    token comes back in the body — into devtools, into client error reporting,
    into whatever proxy logs responses. ``loc``/``type``/``msg`` are what a form
    actually needs, and they survive.
    """
    client, _, _, _ = api

    resp = await client.post("/connections/claim", json={"setup_token": 12345})

    assert resp.status_code == 422
    assert SETUP_TOKEN not in resp.text
    errors = resp.json()["detail"]
    assert errors and all("input" not in e and "ctx" not in e for e in errors)
    assert errors[0]["loc"] == ["body", "setup_token"]
    assert "msg" in errors[0] and "type" in errors[0]


# ---- the knobs -------------------------------------------------------------


async def test_a_connection_can_be_paused_and_resumed(api):
    client, household_id, _, _ = api
    connection = await _connect(client)
    assert connection["is_enabled"] is True

    paused = await client.patch(f"/connections/{connection['id']}", json={"is_enabled": False})
    assert paused.status_code == 200
    assert paused.json()["is_enabled"] is False
    # Health is untouched: "paused" and "broken" are different facts and the
    # dashboard renders them as different things.
    assert paused.json()["status"] == "ok"

    resumed = await client.patch(f"/connections/{connection['id']}", json={"is_enabled": True})
    assert resumed.json()["is_enabled"] is True
    assert datetime.fromisoformat(resumed.json()["next_sync_at"]) is not None


async def test_resuming_from_a_long_pause_schedules_the_next_run_now(api):
    """The one edit that would otherwise make "next run" a lie.

    Resuming a connection that has been off since 2020 must not leave February
    2020 as its next run — the dashboard would show a date in the past forever
    and the scheduler would either skip it or fire it in the same breath. The
    answer is "now": the person who just flipped the switch is watching.
    """
    client, household_id, _, _ = api
    connection = await _connect(client)
    await client.patch(f"/connections/{connection['id']}", json={"is_enabled": False})

    async with scoped_session(household_id) as session:
        row = await session.get(AccountConnection, uuid.UUID(connection["id"]))
        row.next_sync_at = datetime(2020, 1, 1, tzinfo=UTC)  # long overdue

    before = datetime.now(UTC)
    resumed = await client.patch(f"/connections/{connection['id']}", json={"is_enabled": True})
    next_sync_at = datetime.fromisoformat(resumed.json()["next_sync_at"])

    assert before - timedelta(seconds=5) <= next_sync_at <= datetime.now(UTC)


async def test_the_interval_is_settable_and_bounded(api):
    client, household_id, _, _ = api
    connection = await _connect(client)

    ok = await client.patch(
        f"/connections/{connection['id']}", json={"sync_interval_minutes": 720}
    )
    assert ok.status_code == 200
    assert ok.json()["sync_interval_minutes"] == 720

    # The CHECK constraint would refuse these as a 500; a range is the client's
    # mistake and has to read like one.
    too_fast = await client.patch(
        f"/connections/{connection['id']}", json={"sync_interval_minutes": 5}
    )
    assert too_fast.status_code == 422
    assert (await client.get("/connections")).json()[0]["sync_interval_minutes"] == 720


async def test_the_cadence_bounds_come_from_the_columns(api):
    """So the UI's slider cannot offer an interval the database refuses."""
    client, _, _, _ = api
    body = (await client.get("/connections/defaults")).json()
    assert body["sync_interval_min_minutes"] == 120
    assert body["sync_interval_max_minutes"] == 10080
    assert body["sync_interval_minutes"] == 360


# ---- queue and cancel ------------------------------------------------------


async def test_triggering_a_sync_queues_a_job_and_returns_it(api):
    """202: nothing has synced yet, and the panel watches the runs list."""
    client, household_id, user_id, _ = api
    connection = await _connect(client)

    resp = await client.post(f"/connections/{connection['id']}/sync")

    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "queued"
    assert job["trigger"] == "manual"
    assert job["connection_id"] == connection["id"]

    async with scoped_session(household_id) as session:
        row = await session.get(SyncJob, uuid.UUID(job["id"]))
        assert row.requested_by == user_id  # who asked is part of the record


async def test_a_paused_connection_refuses_a_manual_sync(api):
    """409, not a silent skip: a pause button that the app ignores when asked
    nicely is not a pause button."""
    client, _, _, _ = api
    connection = await _connect(client)
    await client.patch(f"/connections/{connection['id']}", json={"is_enabled": False})

    resp = await client.post(f"/connections/{connection['id']}/sync")

    assert resp.status_code == 409
    assert "paused" in resp.json()["detail"].lower()
    assert (await client.get("/connections/jobs", params={"active_only": True})).json() == []


async def test_a_broken_connection_can_still_be_synced_by_hand(api):
    """The credential may have been fixed at the bridge; this is the button that
    would confirm it, so refusing here would refuse the one useful action."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    async with scoped_session(household_id) as session:
        row = await session.get(AccountConnection, uuid.UUID(connection["id"]))
        row.status = "auth_error"

    resp = await client.post(f"/connections/{connection['id']}/sync")

    assert resp.status_code == 202


async def test_cancelling_a_queued_job_terminates_it_and_fences_the_worker(api):
    """Cancel is a DB write. Bumping ``claim_token`` is what makes it stick: a
    worker that was mid-fetch finds its token void and discards its results."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    job = (await client.post(f"/connections/{connection['id']}/sync")).json()
    async with scoped_session(household_id) as session:
        before = (await session.get(SyncJob, uuid.UUID(job["id"]))).claim_token

    resp = await client.post(f"/connections/jobs/{job['id']}/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    async with scoped_session(household_id) as session:
        row = await session.get(SyncJob, uuid.UUID(job["id"]))
        assert row.claim_token != before  # the fence, which is the whole mechanism
        assert row.cancel_requested_at is not None
        assert row.finished_at is not None


async def test_cancelling_a_finished_job_says_so(api):
    """ "Cancelled" and "had already finished" are different facts, and the
    operator is owed the second one when it is true."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    job = (await client.post(f"/connections/{connection['id']}/sync")).json()
    await client.post(f"/connections/jobs/{job['id']}/cancel")

    resp = await client.post(f"/connections/jobs/{job['id']}/cancel")

    assert resp.status_code == 409
    assert "finished" in resp.json()["detail"].lower()


async def test_cancelling_a_stale_token_is_fenced(api):
    """The mechanism, directly: a fresh token can never match a worker's."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    job = (await client.post(f"/connections/{connection['id']}/sync")).json()

    async with scoped_session(household_id) as session:
        row = await session.get(SyncJob, uuid.UUID(job["id"]))
        worker_token = uuid.uuid4()
        row.status = "running"
        row.claim_token = worker_token

    await client.post(f"/connections/jobs/{job['id']}/cancel")

    async with scoped_session(household_id) as session:
        row = await session.get(SyncJob, uuid.UUID(job["id"]))
        assert row.claim_token != worker_token


# ---- runs and the log ------------------------------------------------------


async def test_the_run_detail_is_an_ordered_sanitized_log(api):
    """``seq``, not ``ts``: Postgres ``now()`` is the *transaction* timestamp, so
    every event in one ingest transaction shares it exactly."""
    client, household_id, _, _ = api
    connection = await _connect(client)

    from app.services import sync as sync_svc

    async with scoped_session(household_id) as session:
        run = SyncRun(
            household_id=household_id,
            connection_id=uuid.UUID(connection["id"]),
            trigger="manual",
            status="running",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        log = sync_svc.RunLog(session, household_id, run_id)
        await log.emit("info", "first")
        await log.emit("warning", "second")

    listing = (await client.get("/connections/runs")).json()
    assert [r["id"] for r in listing] == [str(run_id)]

    detail = (await client.get(f"/connections/runs/{run_id}")).json()
    assert [e["event"] for e in detail["events"]] == ["first", "second"]
    assert [e["seq"] for e in detail["events"]] == [0, 1]
    assert detail["run"]["trigger"] == "manual"


async def test_listing_runs_can_be_narrowed_to_one_connection(api):
    client, household_id, _, _ = api
    first = await _connect(client)
    second = await _connect(client)

    async with scoped_session(household_id) as session:
        for connection_id in (first["id"], second["id"]):
            session.add(
                SyncRun(
                    household_id=household_id,
                    connection_id=uuid.UUID(connection_id),
                    trigger="cron",
                    status="ok",
                    started_at=datetime.now(UTC),
                )
            )

    params = {"connection_id": first["id"]}
    only_first = (await client.get("/connections/runs", params=params)).json()
    assert [r["connection_id"] for r in only_first] == [first["id"]]
    assert len((await client.get("/connections/runs")).json()) == 2


async def test_a_run_for_a_deleted_connection_still_names_its_institution(api):
    """``connection_id`` is SET NULL on delete, so without the label a disconnect
    would erase the bank's name from its own history."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    async with scoped_session(household_id) as session:
        row = await session.get(AccountConnection, uuid.UUID(connection["id"]))
        row.org_name = "Bridge A"
        session.add(
            SyncRun(
                household_id=household_id,
                connection_id=row.id,
                connection_label=row.org_name,
                trigger="cron",
                status="ok",
                started_at=datetime.now(UTC),
            )
        )

    assert (await client.delete(f"/connections/{connection['id']}")).status_code == 204

    runs = (await client.get("/connections/runs")).json()
    assert runs[0]["connection_id"] is None
    assert runs[0]["connection_label"] == "Bridge A"


# ---- disconnect ------------------------------------------------------------


async def test_disconnecting_keeps_the_ledger_and_hands_it_back_to_the_human(api):
    """The half that is easy to forget: the FK keeps the rows, but nothing makes
    them *manual*, and a synced account no connection feeds is the state
    ADR-0009's decoupling exists to avoid."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    from app.services import sync as sync_svc

    async with scoped_session(household_id) as session:
        await sync_svc.run_connection_sync(
            household_id, uuid.UUID(connection["id"]),
            provider=FakeProvider(script=scenarios.scenario(scenarios.demo())),
        )

    async with scoped_session(household_id) as session:
        accounts = (await session.execute(select(Account))).scalars().all()
        assert len(accounts) == 3
        account_ids = {a.id for a in accounts}

    assert (await client.delete(f"/connections/{connection['id']}")).status_code == 204

    async with scoped_session(household_id) as session:
        query = select(Account).where(Account.id.in_(account_ids))
        rows = (await session.execute(query)).scalars().all()
        assert len(rows) == 3  # nothing deleted
        assert all(r.is_manual for r in rows)
        assert all(r.connection_id is None for r in rows)


async def test_disconnecting_leaves_the_runs_and_their_events_in_place(api):
    client, household_id, _, _ = api
    connection = await _connect(client)
    async with scoped_session(household_id) as session:
        run = SyncRun(
            household_id=household_id,
            connection_id=uuid.UUID(connection["id"]),
            trigger="cron",
            status="ok",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        session.add(
            SyncRunEvent(
                household_id=household_id, sync_run_id=run.id, seq=0,
                level="info", event="run.finished", detail={},
            )
        )

    await client.delete(f"/connections/{connection['id']}")

    async with scoped_session(household_id) as session:
        assert len((await session.execute(select(SyncRun))).scalars().all()) == 1
        assert len((await session.execute(select(SyncRunEvent))).scalars().all()) == 1


async def test_a_queued_job_does_not_outlive_its_connection(api):
    """``sync_jobs.connection_id`` cascades — a job for a deleted connection is
    meaningless, and the queue is the one place the cleanup is automatic."""
    client, household_id, _, _ = api
    connection = await _connect(client)
    job = (await client.post(f"/connections/{connection['id']}/sync")).json()

    await client.delete(f"/connections/{connection['id']}")

    async with scoped_session(household_id) as session:
        assert await session.get(SyncJob, uuid.UUID(job["id"])) is None


# ---- who is allowed --------------------------------------------------------


async def test_every_connection_route_is_owner_only(api):
    """Stricter than most of the ledger, including the reads: a connection names
    the household's banks and its errors name them too."""
    client, household_id, user_id, _ = api
    connection = await _connect(client)
    job = (await client.post(f"/connections/{connection['id']}/sync")).json()

    await _set_role(household_id, user_id, "member")

    cid, jid = connection["id"], job["id"]
    claim = {"json": {"setup_token": SETUP_TOKEN}}
    for method, url, kw in _every_route(cid, jid, claim):
        resp = await client.request(method, url, **kw)
        assert resp.status_code == 403, f"{method} {url} → {resp.status_code} {resp.text}"

    # And nothing slipped through on the way: the sync button did not queue
    # twice, and the cancel did not land.
    async with scoped_session(household_id) as session:
        jobs = (await session.execute(select(SyncJob))).scalars().all()
        assert [j.status for j in jobs] == ["queued"]
        assert [j.id for j in jobs] == [uuid.UUID(jid)]


async def test_every_connection_route_needs_a_session():
    """The CI 401 loop hits these too; here so the full route list is pinned in
    one place rather than only in a workflow file that nothing cross-checks."""
    from app.main import create_app

    claim = {"json": {"setup_token": SETUP_TOKEN}}
    async with _Api(create_app()) as client:
        for method, url, kw in _every_route(uuid.uuid4(), uuid.uuid4(), claim):
            resp = await client.request(method, url, **kw)
            assert resp.status_code == 401, f"{method} {url} → {resp.status_code}"


def _every_route(connection_id, job_id, claim: dict):
    """Every route on this router, with the smallest body each one accepts.

    Kept as one list so the two tests above cannot drift from each other or from
    the router: a new route that is added without an owner check shows up here as
    a failure in a test that was already passing, rather than as a hole nobody
    wrote a test for. Both callers assert the *same* thing about every entry,
    which is the property — not the two specific status codes.
    """
    cid, jid = str(connection_id), str(job_id)
    return (
        ("GET", "/connections", {}),
        ("GET", "/connections/defaults", {}),
        ("GET", "/connections/jobs", {}),
        ("GET", "/connections/runs", {}),
        ("POST", "/connections/claim", claim),
        ("PATCH", f"/connections/{cid}", {"json": {"is_enabled": False}}),
        ("DELETE", f"/connections/{cid}", {}),
        ("POST", f"/connections/{cid}/sync", {}),
        ("POST", f"/connections/jobs/{jid}/cancel", {}),
        ("GET", f"/connections/runs/{uuid.uuid4()}", {}),
    )


# ---- the service layer, where HTTP cannot reach ----------------------------


async def test_claim_refuses_to_invent_an_institution_name(household_factory, monkeypatch):
    """``org_name`` is NULL until a payload says otherwise. The first fetch is
    the first moment anything knows it, and inventing one for a column the
    dashboard displays as the bank's name would be inventing data."""
    household_id = await household_factory()
    provider = FakeProvider()
    monkeypatch.setattr(svc, "get_provider", lambda _name=None: provider)

    async with scoped_session(household_id) as session:
        connection = await svc.claim(session, household_id, setup_token=SETUP_TOKEN)
        assert connection.org_name is None
        assert connection.next_sync_at is not None  # due now: the user is watching


async def test_enqueue_refuses_a_paused_connection_at_the_service_layer(household_factory):
    household_id = await household_factory()
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id, provider="fake", is_enabled=False
        )
        session.add(connection)
        await session.flush()
        with pytest.raises(LedgerError) as exc:
            await svc.enqueue_sync(session, household_id, connection.id)
    assert exc.value.status == 409


async def test_an_unknown_connection_is_a_404_not_a_500(household_factory):
    household_id = await household_factory()
    async with scoped_session(household_id) as session:
        with pytest.raises(LedgerError) as exc:
            await svc.get_connection(session, uuid.uuid4())
        assert exc.value.status == 404


async def test_cancelling_an_unknown_job_is_a_404(household_factory):
    household_id = await household_factory()
    async with scoped_session(household_id) as session:
        with pytest.raises(LedgerError) as exc:
            await svc.cancel_job(session, uuid.uuid4())
        assert exc.value.status == 404


async def test_run_events_are_scoped_to_their_household(household_factory):
    """RLS, on the table the dashboard reads. B's run is not A's to see."""
    from app.db import scoped_session as scope_a

    a = await household_factory()
    b = await household_factory()
    async with scope_a(a) as session:
        run = SyncRun(
            household_id=a, trigger="cron", status="ok", started_at=datetime.now(UTC)
        )
        session.add(run)
        await session.flush()
        session.add(
            SyncRunEvent(
                household_id=a, sync_run_id=run.id, seq=0,
                level="info", event="a-only", detail={},
            )
        )

    async with scope_a(b) as session:
        assert (await session.execute(select(SyncRun))).scalars().all() == []
        assert (await session.execute(select(SyncRunEvent))).scalars().all() == []


async def test_a_stale_next_sync_at_does_not_survive_a_resume(household_factory):
    """The service half of the resume test, without HTTP in the way."""
    from app.schemas.connections import ConnectionUpdate

    household_id = await household_factory()
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id, provider="fake", is_enabled=False,
            next_sync_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(connection)
        await session.flush()

        await svc.update_connection(session, connection.id, ConnectionUpdate(is_enabled=True))

    assert connection.next_sync_at > datetime.now(UTC) - timedelta(seconds=5)
