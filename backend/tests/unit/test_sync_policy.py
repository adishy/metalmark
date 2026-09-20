"""Sync's pure decisions: how long to wait, what a complaint means, what may be stored.

Everything here is a function of its arguments — no database, no provider, no clock.
That is the point: these three are the parts of sync whose *policy* is worth
arguing about, and a policy test that needed a fixture would not get written.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services import sync
from app.services.sync import _jsonable

# ---- backoff --------------------------------------------------------------


def test_backoff_doubles_then_stops_at_the_cap() -> None:
    """Exponential, capped, and the cap is reached rather than approached."""
    assert sync.backoff_seconds(1) == 60
    assert sync.backoff_seconds(2) == 120
    assert sync.backoff_seconds(3) == 240
    assert sync.backoff_seconds(6) == 1920
    assert sync.backoff_seconds(7) == 3600  # 3840 capped
    assert sync.backoff_seconds(50) == 3600


def test_backoff_is_defined_for_attempts_below_one() -> None:
    """Defensive, not decorative: the reaper and the retry path both compute this
    from a counter that a bug elsewhere could leave at zero."""
    assert sync.backoff_seconds(0) == 60
    assert sync.backoff_seconds(-5) == 60


def test_backoff_jitter_only_ever_subtracts() -> None:
    """So the cap stays a hard cap.

    Spreading both ways around it would let a caller who computed "one hour"
    actually wait longer than the ceiling it was reasoning about — which is the one
    thing a cap must not permit.
    """
    assert sync.backoff_seconds(3, rand=0.0) == 240
    assert sync.backoff_seconds(3, rand=1.0) == 180
    assert sync.backoff_seconds(3, rand=0.5) == 210
    for rand in (0.0, 0.25, 0.5, 0.75, 0.99):
        assert 180 <= sync.backoff_seconds(3, rand=rand) <= 240
    # Including at the ceiling, where a symmetric jitter would exceed 3600.
    assert sync.backoff_seconds(20, rand=0.0) == 3600
    assert sync.backoff_seconds(20, rand=0.5) == 3150
    assert sync.backoff_seconds(20, rand=1.0) == 2700


def test_backoff_ignores_a_rand_outside_the_unit_interval() -> None:
    """Clamped, so a caller passing a raw ``random.random()``-shaped value and a
    caller passing ``1`` get the same answer at the ends."""
    assert sync.backoff_seconds(3, rand=2.0) == sync.backoff_seconds(3, rand=1.0)
    assert sync.backoff_seconds(3, rand=-1.0) == sync.backoff_seconds(3, rand=0.0)


# ---- errlist --------------------------------------------------------------


def test_an_empty_errlist_is_a_clean_run() -> None:
    assert sync.classify_errlist([]) == ("ok", None)


def test_a_connection_complaint_is_an_error_about_the_connection() -> None:
    assert sync.classify_errlist(["con.auth: revoked"]) == ("error", "auth_error")
    assert sync.classify_errlist(["con.something: hmm"]) == ("error", "error")


def test_a_request_complaint_leaves_the_connection_alone() -> None:
    """The captured ``gen.api`` warning, and the reason ``partial`` exists.

    Mapping it to a connection failure would mark a healthy bank broken because our
    date range was long — and the run would be wrong about the one thing it is
    reporting on.
    """
    assert sync.classify_errlist(["gen.api: capped"]) == ("partial", None)


def test_an_account_complaint_is_about_the_payload_not_the_connection() -> None:
    assert sync.classify_errlist(["act.unknown: no such account"]) == ("partial", None)


def test_the_worst_connection_complaint_wins() -> None:
    """A payload carrying both an auth failure and another complaint is an auth
    failure, because that is the one with a fix the user can act on."""
    assert sync.classify_errlist(
        ["con.other: x", "con.auth: revoked"]
    ) == ("error", "auth_error")
    assert sync.classify_errlist(
        ["con.auth: revoked", "con.other: x"]
    ) == ("error", "auth_error")


def test_the_code_prefix_decides_and_never_the_message() -> None:
    """Every ``con.*`` message is unverified — the demo bridge will not produce one
    — so routing on text would be routing on a guess."""
    assert sync.classify_errlist(["gen.api: your access was revoked, maybe?"]) == (
        "partial",
        None,
    )
    assert sync.classify_errlist(["con.auth: all good actually"]) == ("error", "auth_error")


# ---- what may be stored in a run event ------------------------------------


def test_a_url_credential_does_not_survive_the_json_conversion() -> None:
    """Write-time sanitation (ADR-0016), tested at the boundary it happens on."""
    assert "hunter2" not in _jsonable("see https://user:hunter2@bridge.example/x")


def test_sanitization_reaches_into_the_tree() -> None:
    """``detail`` is nested, and a top-level-only sanitizer would miss the one
    string that mattered."""
    stored = _jsonable({"outer": [{"url": "https://u:p@h.example/"}]})
    assert stored == {"outer": [{"url": "https://[redacted]@h.example/"}]}
    assert "u:p" not in json.dumps(stored)


def test_money_is_stored_as_a_string_not_a_float() -> None:
    """The event log is not money — but the habit of converting money to float is
    the habit that eventually costs a cent."""
    stored = _jsonable({"amount": Decimal("1.0050")})
    assert stored == {"amount": "1.0050"}
    assert not isinstance(stored["amount"], float)


def test_datetimes_are_stored_as_iso_strings() -> None:
    assert _jsonable(datetime(2026, 6, 24, 9, 30, tzinfo=UTC)) == "2026-06-24T09:30:00+00:00"


def test_sets_and_tuples_come_out_as_lists() -> None:
    """JSONB has no tuple, and psycopg would take a set for a Postgres array."""
    assert _jsonable({"a": (1, 2), "b": {"x"}}) == {"a": [1, 2], "b": ["x"]}


def test_a_non_json_key_becomes_a_string() -> None:
    assert _jsonable({1: "one"}) == {"1": "one"}


@pytest.mark.parametrize("value", [None, True, False, 0, 7, "plain"])
def test_the_simple_values_pass_through_unchanged(value) -> None:
    assert _jsonable(value) == value
