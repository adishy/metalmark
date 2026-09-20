"""The worker's startup role check — and the wait in front of it.

Tenant isolation in this app is a *grant*, not a code path: the same code
connected as a superuser reads every household's rows and looks identical doing
it. ``worker.assert_app_role`` is what turns that into a refusal to start, and
the way such a check rots is that nobody can produce the misconfigured database
to test it against — so the decision is a pure function and this file is the
database.

``worker.wait_for_database`` is the other half, and it exists because the check
used to be reached too early: on a fresh volume the app role does not exist yet,
so the worker died at boot and stayed dead — a stack that served every page and
consumed no jobs. The tests below are about its two decisions: which failure to
wait out, and which to pass straight through.

Nothing here touches Postgres; the row is a stand-in for what ``pg_roles``
returns, and so is the check.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import worker
from app.worker import role_problem

EXPECTED = "metalmark_app"


def _row(role: str = EXPECTED, *, superuser: bool = False, bypassrls: bool = False):
    return SimpleNamespace(role=role, is_superuser=superuser, bypasses_rls=bypassrls)


def test_the_production_role_passes():
    assert role_problem(_row(), EXPECTED) is None


@pytest.mark.parametrize(
    ("row", "expected_words"),
    [
        # A superuser is exempt from every policy without any flag being set on
        # the tables. The loudest failure, and the one a `GRANT ALL` during
        # setup most easily produces.
        (_row(superuser=True), ["superuser"]),
        # The quiet one: an ordinary role that was granted BYPASSRLS. Nothing
        # about the connection looks wrong.
        (_row(bypassrls=True), ["BYPASSRLS"]),
        # The one that looks harmless. The owner role owns the tables, and an
        # owner is not subject to its own policies unless FORCE ROW LEVEL
        # SECURITY is set — which is not how any of these migrations write them.
        (_row("metalmark"), ["connected as"]),
    ],
)
def test_every_way_a_worker_could_lose_isolation_is_refused(row, expected_words):
    problem = role_problem(row, EXPECTED)
    assert problem is not None
    for word in expected_words:
        assert word in problem


def test_a_missing_role_row_is_a_problem_and_not_a_pass():
    """Fail closed. The query reads ``pg_roles`` for ``current_user``, so a row
    that is absent means the lookup itself is wrong — and a check that treats
    "could not tell" as "fine" is not a check."""
    assert role_problem(None, EXPECTED) is not None


# The word Postgres actually uses, and the one an operator will search for.
NOT_MIGRATED = 'password authentication failed for user "metalmark_app"'


def _waiting(monkeypatch, *, seconds: float = 5.0) -> None:
    """Collapse the wait's clock, so these tests are about decisions, not time."""
    monkeypatch.setattr(worker, "DB_WAIT_SECONDS", seconds)
    monkeypatch.setattr(worker, "DB_WAIT_FIRST_DELAY", 0.001)
    monkeypatch.setattr(worker, "DB_WAIT_MAX_DELAY", 0.002)


async def test_a_database_that_is_not_there_yet_is_waited_for(monkeypatch):
    """The ordinary fresh-volume boot: refused, then up.

    This is the failure that made it into CI as three red Playwright tests
    pointing at a browser timeout. The window between "the container starts"
    and "``alembic upgrade head`` has created the role" is normal, so it is
    waited out rather than treated as an answer."""
    _waiting(monkeypatch)
    attempts: list[int] = []

    async def _check() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise OSError(NOT_MIGRATED)

    monkeypatch.setattr(worker, "assert_app_role", _check)
    await worker.wait_for_database(asyncio.Event())
    assert len(attempts) == 3, "should have retried until the role was reachable"


async def test_a_role_verdict_is_not_waited_on(monkeypatch):
    """The retry loop must not swallow the one failure waiting cannot fix.

    ``assert_app_role`` answers by raising ``SystemExit`` — a ``BaseException``
    for exactly this reason. A superuser does not stop being one, so a worker
    that retried it would sit out the whole deadline and then report something
    it knew on the first attempt."""
    _waiting(monkeypatch)
    attempts: list[int] = []

    async def _check() -> None:
        attempts.append(len(attempts) + 1)
        raise SystemExit(1)

    monkeypatch.setattr(worker, "assert_app_role", _check)
    with pytest.raises(SystemExit):
        await worker.wait_for_database(asyncio.Event())
    assert len(attempts) == 1, "a verdict is not a transient failure"


async def test_a_database_that_never_arrives_stops_the_worker(monkeypatch):
    """Bounded, and non-zero when it runs out.

    The deadline is what keeps "wait for the database" from becoming "hang
    forever with no output"; exit 1 is what lets the compose restart policy
    turn it into a retry that is visible in `docker compose ps`."""
    _waiting(monkeypatch, seconds=0.05)
    attempts: list[int] = []

    async def _check() -> None:
        attempts.append(len(attempts) + 1)
        raise OSError("connection refused")

    monkeypatch.setattr(worker, "assert_app_role", _check)
    with pytest.raises(SystemExit) as caught:
        await worker.wait_for_database(asyncio.Event())
    assert caught.value.code == 1
    assert len(attempts) > 1, "one refusal is the ordinary case, not the answer"


async def test_a_shutdown_during_the_wait_is_a_clean_exit(monkeypatch):
    """SIGTERM while waiting is an operator stopping the stack, not a crash.

    Exit 0 says so; the signal also has to be *noticed* during the wait rather
    than after the deadline, which is why the sleep is an interruptible wait on
    ``stop`` rather than ``asyncio.sleep``."""
    _waiting(monkeypatch)
    stop = asyncio.Event()
    attempts: list[int] = []

    async def _check() -> None:
        attempts.append(len(attempts) + 1)
        stop.set()
        raise OSError("connection refused")

    monkeypatch.setattr(worker, "assert_app_role", _check)
    with pytest.raises(SystemExit) as caught:
        await worker.wait_for_database(stop)
    assert caught.value.code == 0
    assert len(attempts) == 1, "a stop signal ends the wait, it does not retry it"
