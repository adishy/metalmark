"""Calendar-aligned buckets for a reporting window — and the one definition of them.

Every report answers two questions with the same rows: *how much* over the window,
and *when* inside it. The first is a sum over the window; the second needs the
window cut into pieces, and this module is the cut. It is pure — dates in, dates
out, no session — because it is the part most easily got wrong and the part a
test can therefore pin exhaustively.

Three rules, each of which was a bug before it was a rule:

**Buckets are calendar periods, not offsets from `start`.** A month bucket is a
month, so two windows that both cover March produce the same bucket for it and
the two reports are comparable. Stepping by 30 days from an arbitrary start gives
buckets that drift across month boundaries and never line up with a statement.

**Every bucket is clipped to the window.** This is the rule the cash-flow series
was missing: it walked month-ends computed from `start`/`end` but then summed each
bucket from *the first of its month* to *its own month-end*, so a request for
`2026-01-15 … 2026-09-20` reported January 1st–14th and September 21st–30th as
well. The chart showed money from outside the range the reader asked for, and
nothing said so. A clipped bucket cannot do that: its span is a subset of the
window by construction, so the buckets partition the window exactly.

**Granularity may be ``auto``, and the server resolves it.** The client knows its
window; only the server knows how much data is in it and how the response is
shaped. Resolving here means the response can *echo* the choice, so a chart can
label its own axis honestly rather than guessing at what it was given.

A resolved bucket's ``start`` is the clipping point, so the first bucket of every
window starts exactly at the window's start. For the label this is harmless: a
clip can only move a start *forward within its own period*, so a March bucket
clipped to the 15th is still March and still reads "Mar".
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

#: How finely a window is cut. ``auto`` is accepted on input and never returned.
Granularity = Literal["day", "week", "month", "quarter", "year"]
GranularityIn = Literal["auto", "day", "week", "month", "quarter", "year"]

#: The resolved granularities, in increasing coarseness. ``auto`` is not one.
GRANULARITIES: tuple[Granularity, ...] = ("day", "week", "month", "quarter", "year")

#: What ``auto`` picks, by window length in days.
#:
#: The thresholds are chosen against bucket *counts* rather than dates — a chart
#: any denser than about 50 marks is a smear on a phone (DESIGN.md §5) — and the
#: month ceiling is deliberately high: a one-year window is the default range, and
#: it must resolve to twelve monthly buckets. Resolving it to `week` would move
#: every bar in the app's most-read chart, which is a change nobody asked for and
#: would read as a regression rather than as a feature.
_AUTO_CEILING_DAYS: tuple[tuple[Granularity, int], ...] = (
    ("day", 45),
    ("week", 120),
    ("month", 1095),
    ("quarter", 3650),
)


@dataclass(frozen=True, slots=True)
class Bucket:
    """One period of a window.

    ``start`` and ``end`` are inclusive and both lie inside the window. ``period``
    is the calendar period's own first day *before* clipping — the same for a
    bucket whether the window opened in the middle of it or at its start, which is
    what makes it the right thing to key a series on.
    """

    start: date
    end: date
    period: date

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end


def resolve(granularity: GranularityIn, start: date, end: date) -> Granularity:
    """``auto`` → the coarsest granularity still within its bucket ceiling.

    A window with no days in it resolves to ``day`` — the caller gets an empty
    list either way, and picking the finest keeps the answer independent of how
    the empty window was described.
    """
    if granularity != "auto":
        return granularity
    span = (end - start).days + 1
    for candidate, ceiling in _AUTO_CEILING_DAYS:
        if span <= ceiling:
            return candidate
    return "year"


def period_start(day: date, granularity: Granularity) -> date:
    """The first day of the calendar period ``day`` falls in.

    Weeks start on **Monday**, which is what ``date.weekday()`` means and what a
    household reading a weekly spending report expects. A Sunday-start week would
    split every weekend across two buckets.
    """
    match granularity:
        case "day":
            return day
        case "week":
            return day - timedelta(days=day.weekday())
        case "month":
            return date(day.year, day.month, 1)
        case "quarter":
            return date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)
        case "year":
            return date(day.year, 1, 1)


def period_end(day: date, granularity: Granularity) -> date:
    """The last day of the calendar period ``day`` falls in."""
    first = period_start(day, granularity)
    return first + timedelta(days=_period_length(first, granularity) - 1)


def advance(period: date, granularity: Granularity) -> date:
    """The first day of the period after the one ``period`` starts.

    ``period`` must itself be a period start; stepping from a clipped start would
    land inside a period and produce overlapping buckets.
    """
    match granularity:
        case "day":
            return period + timedelta(days=1)
        case "week":
            return period + timedelta(days=7)
        case "month":
            return date(period.year + period.month // 12, period.month % 12 + 1, 1)
        case "quarter":
            return date(period.year + (period.month + 2) // 12, (period.month + 2) % 12 + 1, 1)
        case "year":
            return date(period.year + 1, 1, 1)


def _period_length(period: date, granularity: Granularity) -> int:
    match granularity:
        case "day":
            return 1
        case "week":
            return 7
        case "month":
            return calendar.monthrange(period.year, period.month)[1]
        case "quarter":
            months = [(period.year, period.month + i) for i in range(3)]
            return sum(
                calendar.monthrange(y + (m - 1) // 12, (m - 1) % 12 + 1)[1] for y, m in months
            )
        case "year":
            return 366 if calendar.isleap(period.year) else 365


def buckets(start: date, end: date, granularity: Granularity) -> list[Bucket]:
    """``[start, end]`` cut into calendar periods, clipped at both ends.

    The buckets **partition** the window: they are in order, they do not overlap,
    and every day from ``start`` to ``end`` is in exactly one of them. That is the
    property the cash-flow series needs to add up to its own window total, and it
    is asserted in the tests rather than argued here.

    An inverted window (``end < start``) yields no buckets rather than raising: a
    report over a range the user typed backwards is an empty report, and the API's
    request validation is where a bad range is worth refusing.
    """
    if end < start:
        return []

    out: list[Bucket] = []
    cursor = period_start(start, granularity)
    while cursor <= end:
        bucket_start = max(cursor, start)
        bucket_end = min(period_end(cursor, granularity), end)
        out.append(Bucket(start=bucket_start, end=bucket_end, period=cursor))
        cursor = advance(cursor, granularity)
    return out
