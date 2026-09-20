"""The reporting window's calendar arithmetic, exhaustively.

Pure by construction: dates in, dates out. The property that matters — the
buckets *partition* the window — is asserted as a property over a spread of
windows and granularities rather than as a handful of examples, because the bug
this module was written to fix was exactly a bucket that reached outside its
window and no example had been chosen to catch it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import periods
from app.services.periods import Bucket, Granularity

# ---- auto -----------------------------------------------------------------


def test_auto_resolves_a_year_to_months() -> None:
    """A one-year window is the default range, and it must stay twelve bars.

    Resolving it to `week` would redraw the app's most-read chart with 53 marks.
    That is a change nobody asked for, and it would read as a regression rather
    than as the feature — so this is the assertion that keeps the default still.
    """
    assert periods.resolve("auto", date(2026, 1, 1), date(2026, 12, 31)) == "month"
    assert periods.resolve("auto", date(2026, 1, 1), date(2026, 9, 20)) == "month"


def test_auto_coarsens_as_the_window_grows() -> None:
    """Monotone in the span, and every rung is reachable."""
    anchor = date(2026, 1, 1)
    spans = {
        7: "day",
        45: "day",
        46: "week",
        120: "week",
        121: "month",
        1095: "month",
        1096: "quarter",
        3650: "quarter",
        3651: "year",
        36525: "year",
    }
    for span, expected in spans.items():
        got = periods.resolve("auto", anchor, anchor + timedelta(days=span - 1))
        assert got == expected, f"{span} days resolved to {got}, expected {expected}"


def test_auto_is_never_the_answer() -> None:
    """Everything `auto` can resolve to is a real granularity."""
    for span in (1, 10, 100, 1000, 4000, 40000):
        resolved = periods.resolve("auto", date(2026, 1, 1), date(2026, 1, 1) + timedelta(span))
        assert resolved in periods.GRANULARITIES


def test_an_explicit_granularity_is_returned_unchanged() -> None:
    for g in periods.GRANULARITIES:
        assert periods.resolve(g, date(2026, 1, 1), date(2026, 12, 31)) == g


# ---- period arithmetic ----------------------------------------------------


@pytest.mark.parametrize(
    ("day", "granularity", "start", "end"),
    [
        (date(2026, 3, 15), "day", date(2026, 3, 15), date(2026, 3, 15)),
        # 2026-03-15 is a Sunday; the week it belongs to runs Mon 9 → Sun 15.
        (date(2026, 3, 15), "week", date(2026, 3, 9), date(2026, 3, 15)),
        (date(2026, 3, 9), "week", date(2026, 3, 9), date(2026, 3, 15)),
        (date(2026, 3, 15), "month", date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 3, 15), "quarter", date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 12, 31), "quarter", date(2026, 10, 1), date(2026, 12, 31)),
        (date(2026, 3, 15), "year", date(2026, 1, 1), date(2026, 12, 31)),
        # Leap February, from both sides.
        (date(2028, 2, 10), "month", date(2028, 2, 1), date(2028, 2, 29)),
        (date(2028, 2, 10), "year", date(2028, 1, 1), date(2028, 12, 31)),
    ],
)
def test_period_bounds(day: date, granularity: Granularity, start: date, end: date) -> None:
    assert periods.period_start(day, granularity) == start
    assert periods.period_end(day, granularity) == end
    assert periods.advance(start, granularity) == end + timedelta(days=1)


def test_quarter_length_follows_its_months() -> None:
    """A quarter is not 90 days; it is three named months."""
    assert periods.period_end(date(2026, 1, 15), "quarter") == date(2026, 3, 31)
    assert periods.period_end(date(2026, 4, 15), "quarter") == date(2026, 6, 30)
    assert periods.period_end(date(2026, 7, 15), "quarter") == date(2026, 9, 30)
    assert periods.period_end(date(2026, 10, 15), "quarter") == date(2026, 12, 31)
    # Q1 of a leap year is the one that gains a day.
    assert periods.period_end(date(2028, 2, 15), "quarter") == date(2028, 3, 31)


def test_a_year_advances_to_the_next_one() -> None:
    assert periods.advance(date(2026, 1, 1), "year") == date(2027, 1, 1)
    assert periods.advance(date(2026, 10, 1), "quarter") == date(2027, 1, 1)
    assert periods.advance(date(2026, 12, 1), "month") == date(2027, 1, 1)


# ---- the buckets ----------------------------------------------------------


def test_the_first_bucket_starts_at_the_window_start() -> None:
    """Which is what makes `points[0].date == start` true without a special case."""
    got = periods.buckets(date(2026, 1, 15), date(2026, 9, 20), "month")
    assert got[0].start == date(2026, 1, 15)
    assert got[0].period == date(2026, 1, 1)


def test_the_last_bucket_ends_at_the_window_end() -> None:
    """The bug this module was written for: the old series ran to the month-end.

    `_month_ends` produced 2026-09-30 for a window ending 2026-09-20, and the
    bucket was then summed over its whole month — so the last bar held ten days
    of September the reader had not asked for, and the total across the chart
    exceeded the total for the window the request described.
    """
    got = periods.buckets(date(2026, 1, 15), date(2026, 9, 20), "month")
    assert got[-1].end == date(2026, 9, 20)
    assert got[-1].period == date(2026, 9, 1)


def test_buckets_partition_the_window() -> None:
    """No gaps, no overlaps, no day outside — for every granularity.

    Stated as the property rather than as expected lists: the failure it guards
    against is a bucket edge a hand-written example would not have landed on.
    """
    windows = [
        (date(2026, 1, 1), date(2026, 12, 31)),
        (date(2026, 1, 15), date(2026, 9, 20)),
        (date(2026, 12, 31), date(2026, 12, 31)),
        (date(2027, 3, 2), date(2027, 3, 8)),
        (date(2028, 2, 1), date(2028, 2, 29)),
        # A window that starts and ends inside one period.
        (date(2026, 5, 4), date(2026, 5, 6)),
        # And one spanning a year boundary.
        (date(2026, 11, 20), date(2027, 2, 3)),
    ]
    for start, end in windows:
        for granularity in periods.GRANULARITIES:
            got = periods.buckets(start, end, granularity)
            assert got, f"{granularity} over {start}..{end} produced no buckets"
            assert got[0].start == start
            assert got[-1].end == end
            covered: list[date] = []
            for prev, cur in zip(got, got[1:], strict=False):
                assert cur.start == prev.end + timedelta(days=1), (
                    f"{granularity} over {start}..{end}: gap or overlap "
                    f"between {prev.end} and {cur.start}"
                )
            for b in got:
                assert b.start <= b.end
                assert b.period <= b.start <= b.end
                day = b.start
                while day <= b.end:
                    covered.append(day)
                    day += timedelta(days=1)
            expected = []
            day = start
            while day <= end:
                expected.append(day)
                day += timedelta(days=1)
            assert covered == expected, f"{granularity} over {start}..{end} does not tile it"


def test_bucket_count_is_bounded_by_the_granularity() -> None:
    """A coarse granularity cannot produce more buckets than the span in periods.

    This is the arithmetic behind `auto`'s ceilings having something to bound.
    """
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    assert len(periods.buckets(start, end, "day")) == 365
    assert len(periods.buckets(start, end, "week")) == 53
    assert len(periods.buckets(start, end, "month")) == 12
    assert len(periods.buckets(start, end, "quarter")) == 4
    assert len(periods.buckets(start, end, "year")) == 1


def test_a_single_day_window_is_one_bucket_at_every_granularity() -> None:
    """A one-day report is a one-day report, whatever it is bucketed by."""
    day = date(2026, 3, 15)
    for granularity in periods.GRANULARITIES:
        got = periods.buckets(day, day, granularity)
        assert got == [Bucket(start=day, end=day, period=periods.period_start(day, granularity))]


def test_an_inverted_window_is_empty_rather_than_an_error() -> None:
    assert periods.buckets(date(2026, 9, 20), date(2026, 1, 1), "month") == []


def test_a_week_bucket_is_never_clipped_to_a_partial_week() -> None:
    """Weeks are the granularity whose boundaries the window can fall inside.

    A Monday-aligned bucket clipped at both ends is still labelled by its Monday,
    so the frontend reads the right week out of a bucket the window cut short.
    """
    got = periods.buckets(date(2026, 3, 11), date(2026, 3, 20), "week")
    assert [(b.start, b.end, b.period) for b in got] == [
        (date(2026, 3, 11), date(2026, 3, 15), date(2026, 3, 9)),
        (date(2026, 3, 16), date(2026, 3, 20), date(2026, 3, 16)),
    ]
