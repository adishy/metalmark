import { describe, it, expect } from "vitest";
import {
  calendarDay,
  formatBucket,
  formatDay,
  formatInstant,
  formatMonth,
  formatTime,
  isoDay,
  relativeTime,
  todayIso,
} from "@/lib/dates";

// Repeated here rather than imported from the module: this asserts the output
// against the value's own components, and reusing the module's table would let
// a wrong table pass its own test. Same reasoning as format.test.ts's `norm`.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** A local `Date` at a fixed wall-clock time, so "today" is unambiguous. */
const local = (y: number, m: number, d: number, h = 9) => new Date(y, m - 1, d, h);

describe("isoDay", () => {
  it("formats a date with zero-padded month and day", () => {
    expect(isoDay(new Date(2026, 0, 5))).toBe("2026-01-05");
    expect(isoDay(new Date(2026, 11, 31))).toBe("2026-12-31");
  });

  it("reports the local calendar day, which is what the API's date params mean", () => {
    // The contract, stated without reference to UTC: whatever the local clock
    // reads is what comes back. Note this asserts vacuously in a UTC runtime —
    // the bug it guards (`toISOString().slice(0,10)`) is only observable east or
    // west of UTC, so a browser-timezone e2e is the real tripwire and this is
    // the documentation of the intent.
    const d = new Date(2026, 5, 15, 23, 30);
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    expect(isoDay(d)).toBe(`${d.getFullYear()}-${month}-${day}`);
  });

  it("does not shift a month boundary backwards", () => {
    // `new Date(2026, 2, 1)` is local midnight on the 1st. Converting to UTC
    // first moves it to the last day of the previous month anywhere east of
    // UTC, which is exactly the off-by-one-day this helper exists to prevent.
    expect(isoDay(new Date(2026, 2, 1))).toBe("2026-03-01");
  });
});

describe("todayIso", () => {
  it("defaults a date input to the viewer's own today", () => {
    expect(todayIso()).toBe(isoDay(new Date()));
  });

  it("is a plain YYYY-MM-DD, not an ISO instant", () => {
    expect(todayIso()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("calendarDay", () => {
  it("takes the date part of a date-only value", () => {
    expect(calendarDay("2026-09-20")).toBe("2026-09-20");
  });

  it("takes the date part of an instant without converting it", () => {
    // The frame, in one assertion: the day the string names, not the day the
    // local clock would make of it.
    expect(calendarDay("2026-09-20T12:00:00+00:00")).toBe("2026-09-20");
    expect(calendarDay("2026-09-20T12:00:00Z")).toBe("2026-09-20");
    expect(calendarDay("2026-09-20T02:00:00-07:00")).toBe("2026-09-20");
  });

  it("rejects something that is not an ISO date", () => {
    // Loud, because the failure without it is `undefined` rendering into a
    // month name — which reads as data rather than as a bug.
    expect(() => calendarDay("20/09/2026")).toThrow(/Not an ISO date/);
    expect(() => calendarDay("")).toThrow(/Not an ISO date/);
  });

  it("rejects a month or day that is out of range", () => {
    expect(() => calendarDay("2026-13-01")).toThrow(/Not a calendar date/);
    expect(() => calendarDay("2026-00-10")).toThrow(/Not a calendar date/);
    expect(() => calendarDay("2026-09-00")).toThrow(/Not a calendar date/);
    expect(() => calendarDay("2026-09-32")).toThrow(/Not a calendar date/);
  });
});

describe("formatDay", () => {
  it("renders each style in the fixed vocabulary", () => {
    // An explicit `now`, well away from the value: `compact` is the one style
    // that reads a clock, and a test that let it read the real one would quietly
    // change its answer on the day the value came true.
    const now = local(2026, 3, 15);
    expect(formatDay("2026-09-20", "long", now)).toBe("Sep 20 2026");
    expect(formatDay("2026-09-20", "medium", now)).toBe("Sep 20");
    expect(formatDay("2026-09-20", "weekday", now)).toBe("Sun");
    expect(formatDay("2026-09-20", "compact", now)).toBe("Sep 20");
  });

  it("zero-pads the day, so a column of dates stays a column", () => {
    // "Jan 2 2026" beside "Jan 12 2026" is a ragged left edge under tabular
    // figures, which is the whole reason for a fixed vocabulary.
    expect(formatDay("2026-01-02", "long")).toBe("Jan 02 2026");
    expect(formatDay("2026-01-02", "medium")).toBe("Jan 02");
    expect(formatDay("2026-01-02", "weekday")).toBe("Fri");
  });

  it("takes the weekday from the day itself, not from a converted instant", () => {
    expect(formatDay("2026-01-01", "weekday")).toBe("Thu");
    expect(formatDay("2025-12-31", "weekday")).toBe("Wed");
    expect(formatDay("2026-09-21", "weekday")).toBe("Mon");
  });

  it("does not read the clock, so a value with a time renders as its own day", () => {
    // The bug this covers is `new Date(iso)` on a date-only value: correct in
    // UTC, a day early everywhere west of it. Midnight is the value that shows
    // it, which is precisely why the backend anchors date-only imports at noon
    // (`backend/app/services/imports.py::_transacted_at`) — the anchor is
    // harmless rather than load-bearing because nothing here converts.
    expect(formatDay("2026-09-20T00:00:00Z", "long")).toBe("Sep 20 2026");
    expect(formatDay("2026-09-20T12:00:00Z", "long")).toBe("Sep 20 2026");
    expect(formatDay("2026-09-20T23:30:00Z", "long")).toBe("Sep 20 2026");
  });

  it("is unmoved by an offset that lands on another day in UTC", () => {
    // 02:00 in UTC-7 is 09:00 UTC — a different day from the string's own — and
    // the string's own is what a ledger means.
    expect(formatDay("2026-09-20T02:00:00-07:00", "medium")).toBe("Sep 20");
  });

  it("says Today against the caller's clock", () => {
    const now = local(2026, 9, 20);
    expect(formatDay("2026-09-20T12:00:00Z", "compact", now)).toBe("Today");
    // The same value against a later today is Yesterday, and further out it is
    // an ordinary date — the phrase is about the caller's clock, not the value.
    expect(formatDay("2026-09-20T12:00:00Z", "compact", local(2026, 9, 21))).toBe("Yesterday");
    expect(formatDay("2026-09-20T12:00:00Z", "compact", local(2026, 9, 25))).toBe("Sep 20");
  });

  it("says Yesterday across a month boundary", () => {
    expect(formatDay("2026-02-28T12:00:00Z", "compact", local(2026, 3, 1))).toBe("Yesterday");
  });

  it("says Yesterday across a year boundary", () => {
    expect(formatDay("2025-12-31T12:00:00Z", "compact", local(2026, 1, 1))).toBe("Yesterday");
  });

  it("says Yesterday across a leap day", () => {
    expect(formatDay("2028-02-29T12:00:00Z", "compact", local(2028, 3, 1))).toBe("Yesterday");
  });

  it("says Today on the first of a month, not Yesterday or a date", () => {
    expect(formatDay("2026-03-01T12:00:00Z", "compact", local(2026, 3, 1))).toBe("Today");
  });

  it("counts days by the calendar, not by elapsed hours", () => {
    // The 8th to the 9th is one day everywhere; in a timezone that springs
    // forward overnight it is 23 hours, and a local-midnight subtraction would
    // floor that to zero and say "Today". Vacuous in UTC, load-bearing in
    // America/New_York — which is the point.
    expect(formatDay("2026-03-08T12:00:00Z", "compact", local(2026, 3, 9))).toBe("Yesterday");
  });

  it("has no 'Tomorrow': a future date is just a date", () => {
    // Nothing in the ledger is legitimately dated ahead, so naming it would be
    // a kindness to a typo.
    const now = local(2026, 9, 20);
    expect(formatDay("2026-09-21T12:00:00Z", "compact", now)).toBe("Sep 21");
    expect(formatDay("2027-01-01T12:00:00Z", "compact", now)).toBe("Jan 01");
  });

  it("does not accept a relative style", () => {
    // `relative` is a phrase about elapsed time, and a calendar day has none.
    // The type is the enforcement, so the assertion is the `@ts-expect-error`:
    // if the `DayStyle` exclusion is ever widened, the directive goes unused
    // and `npm run typecheck` fails.
    // @ts-expect-error - "relative" is not a DayStyle
    expect(formatDay("2026-09-20", "relative")).toBe("Sep 20");
  });
});

describe("formatMonth", () => {
  it("renders the reports API's month key as a name", () => {
    // `YYYY-MM`, which is what `/reports/cash-flow` returns and what the chart
    // axis was showing verbatim.
    expect(formatMonth("2026-01")).toBe("Jan");
    expect(formatMonth("2026-12")).toBe("Dec");
    expect(formatMonth("2026-01", "long")).toBe("Jan 2026");
  });

  it("accepts a full date and ignores its day", () => {
    // So a caller holding `2026-01-31` does not have to slice it — slicing is
    // how the two frames got confused in the first place.
    expect(formatMonth("2026-01-31")).toBe("Jan");
    expect(formatMonth("2026-01-31", "long")).toBe("Jan 2026");
  });

  it("rejects something that is not an ISO month", () => {
    expect(() => formatMonth("January")).toThrow(/Not an ISO month/);
    expect(() => formatMonth("2026")).toThrow(/Not an ISO month/);
    expect(() => formatMonth("2026-13")).toThrow(/Not a calendar month/);
    expect(() => formatMonth("2026-00")).toThrow(/Not a calendar month/);
  });
});

describe("formatBucket", () => {
  it("names a bucket by the granularity it is", () => {
    const day = "2026-04-06"; // a Monday, so `week` and `day` share a label here
    expect(formatBucket(day, "day")).toBe("Apr 06");
    expect(formatBucket(day, "day", "long")).toBe("Apr 06 2026");
    // A week is labelled by where it starts: the axis title carries the
    // granularity, and "w/c Apr 06" on twelve labels is twelve times the ink.
    expect(formatBucket(day, "week")).toBe("Apr 06");
    expect(formatBucket(day, "month")).toBe("Apr");
    expect(formatBucket(day, "month", "long")).toBe("Apr 2026");
    expect(formatBucket(day, "year")).toBe("2026");
  });

  it("numbers quarters, because a quarter has no name of its own", () => {
    // One day per quarter, including the two ends of a year — the boundary a
    // hand-rolled `(month / 3) | 0` would put on the wrong side.
    expect(formatBucket("2026-01-01", "quarter")).toBe("Q1");
    expect(formatBucket("2026-03-31", "quarter")).toBe("Q1");
    expect(formatBucket("2026-04-01", "quarter")).toBe("Q2");
    expect(formatBucket("2026-06-30", "quarter")).toBe("Q2");
    expect(formatBucket("2026-07-01", "quarter")).toBe("Q3");
    expect(formatBucket("2026-10-01", "quarter")).toBe("Q4");
    expect(formatBucket("2026-12-31", "quarter")).toBe("Q4");
    // The year is added at `long` only: `Q1` alone is ambiguous the moment two
    // years are on screen, and useless ink when they are not.
    expect(formatBucket("2026-10-01", "quarter", "long")).toBe("Q4 2026");
  });

  it("labels a clipped first bucket by where it begins, not by its period", () => {
    // The server dates a point by its first day *inside the window*, so a window
    // opening 15 March gives a March bucket labelled `2026-03-15`. It must still
    // read "Mar" — the clip only ever moves a bucket start forward within its own
    // period, which is why the label survives it.
    expect(formatBucket("2026-03-15", "month")).toBe("Mar");
    expect(formatBucket("2026-03-15", "quarter")).toBe("Q1");
    expect(formatBucket("2026-02-15", "year")).toBe("2026");
  });

  it("rejects something that is not an ISO day", () => {
    expect(() => formatBucket("March", "day")).toThrow(/Not an ISO date/);
    expect(() => formatBucket("2026-03", "day")).toThrow(/Not an ISO date/);
    expect(() => formatBucket("March", "quarter")).toThrow(/Not an ISO date/);
  });
});

describe("formatInstant", () => {
  it("renders in the viewer's local time, which is what an instant means", () => {
    const iso = "2026-09-20T23:30:00Z";
    const at = new Date(iso);
    expect(formatInstant(iso, "long")).toBe(
      `${MONTHS[at.getMonth()]} ${String(at.getDate()).padStart(2, "0")} ${at.getFullYear()}`,
    );
    expect(formatInstant(iso, "medium")).toBe(
      `${MONTHS[at.getMonth()]} ${String(at.getDate()).padStart(2, "0")}`,
    );
    expect(formatInstant(iso, "weekday")).toBe(WEEKDAYS[at.getDay()]);
  });

  it("says Today and Yesterday against the caller's clock", () => {
    const now = local(2026, 9, 20);
    // Built from the local day rather than from `now.toISOString()`: at 09:00
    // local the UTC instant can be the previous day, and this test is about the
    // local one.
    expect(formatInstant(`${isoDay(now)}T09:00:00Z`, "compact", now)).toBe("Today");
    expect(formatInstant("2026-09-19T09:00:00Z", "compact", now)).toBe("Yesterday");
    expect(formatInstant("2026-09-18T09:00:00Z", "compact", now)).toBe("Sep 18");
  });

  it("routes the relative style to relativeTime rather than reimplementing it", () => {
    const now = new Date("2026-09-20T12:00:00Z");
    const iso = new Date(now.getTime() - 3 * 3600 * 1000).toISOString();
    expect(formatInstant(iso, "relative", now)).toBe(relativeTime(iso, now));
    expect(formatInstant(iso, "relative", now)).toBe("3 h ago");
  });

  it("rejects something that is not an instant", () => {
    expect(() => formatInstant("yesterday", "long")).toThrow(/Not an ISO instant/);
  });
});

describe("formatTime", () => {
  it("renders the local time of day, zero-padded and 24-hour", () => {
    // Fixed at midnight UTC: the local rendering depends on the runtime's
    // timezone, so this asserts against the same local components the helper
    // reads rather than against a string that only holds in UTC.
    const iso = "2026-09-20T00:00:00Z";
    const at = new Date(iso);
    const hh = String(at.getHours()).padStart(2, "0");
    const mm = String(at.getMinutes()).padStart(2, "0");
    const ss = String(at.getSeconds()).padStart(2, "0");
    expect(formatTime(iso)).toBe(`${hh}:${mm}:${ss}`);
    expect(formatTime(iso, false)).toBe(`${hh}:${mm}`);
  });

  it("is fixed-width, so a log column stays a column", () => {
    // Hand-written rather than `toLocaleTimeString`, which would render "9:05:03"
    // and shift the rest of the row (DESIGN.md §6.3: tabular figures assume the
    // digits line up).
    for (const iso of ["2026-09-20T01:02:03Z", "2026-09-20T13:02:03Z"]) {
      expect(formatTime(iso)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    }
  });

  it("rejects something that is not an instant", () => {
    expect(() => formatTime("half past nine")).toThrow(/Not an ISO instant/);
  });
});

describe("relativeTime", () => {
  // Fixed `now`, because the function's whole implementation is its boundaries.
  const now = new Date("2026-09-20T12:00:00Z");
  const ago = (seconds: number) =>
    relativeTime(new Date(now.getTime() - seconds * 1000).toISOString(), now);
  const ahead = (seconds: number) =>
    relativeTime(new Date(now.getTime() + seconds * 1000).toISOString(), now);

  it("calls anything under a minute 'just now'", () => {
    expect(ago(0)).toBe("just now");
    expect(ago(44)).toBe("just now");
  });

  it("rounds the first minute up rather than saying '0 min ago'", () => {
    expect(ago(45)).toBe("1 min ago");
    expect(ago(89)).toBe("1 min ago");
    expect(ago(90)).toBe("1 min ago");
  });

  it("floors, so the label never overstates elapsed time", () => {
    // 119 s is the case that distinguishes floor from round: rounding would
    // claim two minutes for something that has not had them.
    expect(ago(119)).toBe("1 min ago");
    expect(ago(3599)).toBe("59 min ago");
    expect(ago(86_399)).toBe("23 h ago");
  });

  it("advances units at the boundary", () => {
    expect(ago(3600)).toBe("1 h ago");
    expect(ago(86_400)).toBe("1 d ago");
    expect(ago(6 * 86_400)).toBe("6 d ago");
  });

  it("hands off to a date past a week", () => {
    // "37 d ago" is a worse answer than the day it happened.
    const out = ago(8 * 86_400);
    expect(out).toMatch(/2026/);
    expect(out).not.toContain("d ago");
  });

  it("reads forward for a future timestamp", () => {
    expect(ahead(30)).toBe("any second now");
    expect(ahead(60)).toBe("in 1 min");
    expect(ahead(7200)).toBe("in 2 h");
    expect(ahead(86_400)).toBe("in 1 d");
  });

  it("treats a few seconds of clock skew as 'now', not as the future", () => {
    // `next_sync_at` is computed by the server and read by the browser; the two
    // clocks are not the same clock.
    expect(ahead(5)).toBe("any second now");
  });

  it("rejects something that is not an instant", () => {
    expect(() => relativeTime("soon", now)).toThrow(/Not an ISO instant/);
  });
});
