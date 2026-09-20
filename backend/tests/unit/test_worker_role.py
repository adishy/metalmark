"""The worker's startup role check, tested where it can be reached.

Tenant isolation in this app is a *grant*, not a code path: the same code
connected as a superuser reads every household's rows and looks identical doing
it. ``worker.assert_app_role`` is what turns that into a refusal to start, and
the way such a check rots is that nobody can produce the misconfigured database
to test it against — so the decision is a pure function and this file is the
database.

Nothing here touches Postgres; the row is a stand-in for what ``pg_roles``
returns.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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
