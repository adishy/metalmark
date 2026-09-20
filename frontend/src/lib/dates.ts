// Every date this app shows, in one vocabulary, in one frame.
//
// ---------------------------------------------------------------------------
// Two frames, and they are not interchangeable
// ---------------------------------------------------------------------------
//
//   - A **calendar day** is a day, not a moment: `transacted_at`, a report
//     point's `date`, `rate_date`. It is read from the value's own date
//     part and is **never converted**. A transaction dated 2026-09-20 is the
//     20th in the ledger wherever the reader is standing, which is what a
//     ledger means by a date. The backend anchors date-only imports at noon UTC
//     for this reason (`backend/app/services/imports.py::_transacted_at`), and
//     reading the date part is what makes that anchor harmless rather than
//     load-bearing: noon UTC converts to the wrong day past UTC+11, so a local
//     conversion would have to be re-reasoned every time the anchor moved.
//
//   - An **instant** is a moment: `started_at`, `last_synced_at`, a sync
//     event's `ts`. Someone asking when a sync ran means their own clock, so it
//     is converted to the viewer's local time — the same thing the browser has
//     always done with these, and the reason `relative` is allowed on one and
//     not the other.
//
// Getting this backwards is the whole failure mode: `new Date(iso)` on a
// date-only value is correct in UTC and wrong by a day in every timezone that
// matters, which is invisible in a UTC browser — most of CI.
//
// ---------------------------------------------------------------------------
// The vocabulary is fixed, and it is not `Intl`'s
// ---------------------------------------------------------------------------
//
// Decision H in `docs/PLAN-v0.9.md` fixes five styles — `long` "Jan 02 2026",
// `medium` "Jan 02", `weekday` "Mon", `compact` "Today"/"Yesterday"/"Jan 02",
// `relative` "2h ago" — so they are hand-written from a table rather than
// delegated to `toLocaleDateString`. Two reasons, and the second is the one that
// matters: a locale-aware formatter returns "Jan 2, 2026" in `en-US` and
// something else everywhere else, so the same ledger would read differently on
// two machines; and these strings are a *design decision*, not a localisation.
// `format.ts` supplies the money sign itself for the same reason.
//
// **The ISO form is never the visible text.** It belongs in a `title` and in
// `<time datetime>`. `components/datetime.tsx` is how the app renders a date, so
// that rule cannot be forgotten at a call site.

// The bucket vocabulary is the API's, imported rather than restated: a second
// copy here could drift by one member, and `formatBucket`'s switch would then
// silently fall through for a granularity the server had started sending.
import type { Granularity } from "@/api/types";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

const DAY_MS = 86_400_000;

/**
 * The styles a date can be rendered in. `relative` says "2 h ago" — a phrase
 * about elapsed time, so it is only meaningful for an instant; `formatDay` does
 * not accept it.
 */
export type DateStyle = "long" | "medium" | "weekday" | "compact" | "relative";

/** The styles that make sense for a calendar day. */
export type DayStyle = Exclude<DateStyle, "relative">;

interface Ymd {
  y: number;
  m: number;
  d: number;
}

/** `YYYY-MM-DD`, the shape the API's date fields and the date half of an
 *  instant both start with. */
const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})/;

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function ymd(value: string): Ymd {
  const m = DAY_RE.exec(value);
  if (!m) throw new Error(`Not an ISO date: ${JSON.stringify(value)}`);
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  // Range-checked rather than trusted: the failure without it is an `undefined`
  // month name rendered into the interface, which reads as data rather than as
  // a bug. A date that is not a date is a programming error, and this is a
  // component's render path, so it should be loud.
  if (mo < 1 || mo > 12 || d < 1 || d > 31) {
    throw new Error(`Not a calendar date: ${JSON.stringify(value)}`);
  }
  return { y, m: mo, d };
}

function dayKeyOf(t: Ymd): string {
  return `${t.y}-${pad(t.m)}-${pad(t.d)}`;
}

/**
 * The calendar day a value names, as `YYYY-MM-DD`, taken from the value's own
 * date part. This is the frame, in one function: `transacted_at` keeps the day
 * the ledger recorded it under, whether the value arrived as `2026-09-20` or as
 * `2026-09-20T12:00:00+00:00`.
 */
export function calendarDay(value: string): string {
  return dayKeyOf(ymd(value));
}

/** A day as a count of days, for comparing two of them. Built with `Date.UTC`
 *  from the components so a difference is always a whole number of days — a
 *  local-midnight `Date` pair spans 23 or 25 hours across a DST boundary and
 *  would make "yesterday" fail twice a year. */
function dayNumber(day: string): number {
  const { y, m, d } = ymd(day);
  return Date.UTC(y, m - 1, d);
}

/** "Today" / "Yesterday" against a reference day, or null when it is neither.
 *  Deliberately no "Tomorrow": nothing in the ledger is dated in the future
 *  except by the user's own mistake, and naming it would be a kindness to that
 *  mistake. */
function dayWord(day: string, today: string): string | null {
  if (day === today) return "Today";
  if (dayNumber(day) === dayNumber(today) - DAY_MS) return "Yesterday";
  return null;
}

function longOf(t: Ymd): string {
  return `${MONTHS[t.m - 1]} ${pad(t.d)} ${t.y}`;
}

function mediumOf(t: Ymd): string {
  return `${MONTHS[t.m - 1]} ${pad(t.d)}`;
}

/**
 * A **calendar day**, in the viewer's choice of vocabulary.
 *
 * `now` is a parameter so the output is a function of its inputs: "Today" is
 * decided against the caller's clock, and a helper that reads the clock itself
 * is untestable at exactly the boundary that bites.
 */
export function formatDay(
  value: string,
  style: DayStyle = "medium",
  now: Date = new Date(),
): string {
  const t = ymd(value);
  const day = dayKeyOf(t);
  switch (style) {
    case "long":
      return longOf(t);
    case "weekday":
      // Built from the components, so this is the weekday of *that* day and not
      // of the instant it happens to be anchored at.
      return WEEKDAYS[new Date(t.y, t.m - 1, t.d).getDay()] ?? "";
    case "compact":
      return dayWord(day, isoDay(now)) ?? mediumOf(t);
    default:
      return mediumOf(t);
  }
}

/**
 * An **instant**, in the viewer's local time.
 *
 * `relative` is the same phrase `relativeTime` has always produced — this is a
 * second entry point to it, not a second implementation.
 */
export function formatInstant(
  value: string,
  style: DateStyle = "medium",
  now: Date = new Date(),
): string {
  if (style === "relative") return relativeTime(value, now);
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) throw new Error(`Not an ISO instant: ${JSON.stringify(value)}`);
  const t: Ymd = { y: at.getFullYear(), m: at.getMonth() + 1, d: at.getDate() };
  switch (style) {
    case "long":
      return longOf(t);
    case "weekday":
      return WEEKDAYS[at.getDay()] ?? "";
    case "compact":
      return dayWord(dayKeyOf(t), isoDay(now)) ?? mediumOf(t);
    default:
      return mediumOf(t);
  }
}

/**
 * The time of day of an instant, as `HH:MM` (or `HH:MM:SS`).
 *
 * Not part of decision H's vocabulary, which is about dates: a sync event log
 * has to distinguish two events in the same second, and there is no fixed
 * vocabulary for that. Fixed 24-hour, zero-padded and hand-written rather than
 * `toLocaleTimeString` — for the same reason as the rest of this module, and so
 * a log column stays a comparable column under tabular figures.
 */
export function formatTime(value: string, withSeconds = true): string {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) throw new Error(`Not an ISO instant: ${JSON.stringify(value)}`);
  const hhmm = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  return withSeconds ? `${hhmm}:${pad(at.getSeconds())}` : hhmm;
}

// ---------------------------------------------------------------------------
// Months
// ---------------------------------------------------------------------------

/** `YYYY-MM`, the shape the reports API's `month` field uses. */
const MONTH_RE = /^(\d{4})-(\d{2})(?!\d)/;

/**
 * A month, as `Jan` or `Jan 2026`.
 *
 * The one thing decision H's vocabulary does not cover, and it needs covering
 * because the cash-flow chart's x-axis is a month (`YYYY-MM`), not a day. It
 * lives here rather than in the chart for the same reason the rest does: a
 * second place that turns a date into text is a second vocabulary. The rule is
 * unchanged — the ISO form is never the visible text.
 *
 * `short` (the default) is what an axis wants: twelve labels across a phone's
 * width (§5) have no room for the year, and the chart is scoped to one year, so
 * the year is the one thing the reader already knows. `long` is for a tooltip or
 * a heading, where it is the only thing on screen.
 *
 * Accepts a full date too, ignoring its day: a caller with a `2026-01-31` in
 * hand should not have to slice it, and slicing is how the two frames got
 * confused in the first place.
 */
export function formatMonth(value: string, style: "short" | "long" = "short"): string {
  const m = MONTH_RE.exec(value);
  if (!m) throw new Error(`Not an ISO month: ${JSON.stringify(value)}`);
  const month = Number(m[2]);
  if (month < 1 || month > 12) throw new Error(`Not a calendar month: ${JSON.stringify(value)}`);
  return style === "long" ? `${MONTHS[month - 1]} ${m[1]}` : (MONTHS[month - 1] ?? "");
}

// ---------------------------------------------------------------------------
// Buckets
// ---------------------------------------------------------------------------

/**
 * A reporting bucket, named by the granularity it *is*.
 *
 * A report chart's x-axis is a bucket, and a bucket is not a day — so this is
 * the one place that decides what `granularity` looks like on screen, next to
 * the functions that decide what a day and a month look like. A chart that
 * formatted its own labels would be a second vocabulary, which is how the axis
 * and the tooltip end up disagreeing about the same bar.
 *
 * `value` is a bucket's first day, as the API dates its points. The rules:
 *
 *   - `day`/`week` render as a day. A week is labelled by where it starts, not
 *     by "week of" — the axis title carries the granularity, and repeating it in
 *     twelve labels is twelve times the ink for one piece of information.
 *   - `month` is `formatMonth`, unchanged. The axis of a year is twelve months,
 *     which is what that function was written for.
 *   - `quarter` is `Q1`/`Q1 2026` — a quarter has no name of its own, so it is
 *     numbered, and `long` adds the year it belongs to because `Q1` alone is
 *     ambiguous the moment a reader can see two years at once.
 *   - `year` is the year, at both styles. `2026` is neither short nor long; it
 *     is the whole of it.
 *
 * The ISO form is never the visible text (§6.6), here as everywhere else.
 */
export function formatBucket(
  value: string,
  granularity: Granularity,
  style: "short" | "long" = "short",
): string {
  switch (granularity) {
    case "day":
    case "week":
      return formatDay(value, style === "long" ? "long" : "medium");
    case "month":
      return formatMonth(value, style);
    case "quarter": {
      const t = ymd(value);
      const quarter = Math.floor((t.m - 1) / 3) + 1;
      return style === "long" ? `Q${quarter} ${t.y}` : `Q${quarter}`;
    }
    case "year":
      return String(ymd(value).y);
  }
}

// ---------------------------------------------------------------------------
// Relative time
// ---------------------------------------------------------------------------
//
// Moved here from `format.ts`, which was the only other place a date was
// rendered: two modules rendering dates is how a vocabulary drifts. `format.ts`
// is money and durations now.

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/*
 * Thresholds are in seconds and are chosen so no phrase is ever *wrong*, only
 * coarse. "3 min ago" for something 150 s old is coarse; "1 min ago" for
 * something 90 s old is a lie in the direction a person notices, so each bucket
 * ends past its own boundary (45 → 90 → 60 min) rather than at it.
 *
 * `floor` at every step, and each unit is left at the moment the next one
 * begins: rounding would produce "90 min ago" followed by "2 h ago" as time
 * advanced, and a label that jumps forward by half an hour is worse than one
 * that is merely coarse. Flooring also means the phrase never overstates how
 * much time has passed.
 */
function ago(seconds: number, iso: string): string {
  if (seconds < 45) return "just now";
  if (seconds < 90) return "1 min ago";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} min ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} h ago`;
  if (seconds < 7 * DAY) return `${Math.floor(seconds / DAY)} d ago`;
  return formatInstant(iso, "long");
}

/** Future `relativeTime`: "in 1 min", "in 2 h", "in 3 d". */
function until(seconds: number, iso: string): string {
  if (seconds < 45) return "any second now";
  if (seconds < 90) return "in 1 min";
  if (seconds < HOUR) return `in ${Math.floor(seconds / MINUTE)} min`;
  if (seconds < DAY) return `in ${Math.floor(seconds / HOUR)} h`;
  if (seconds < 7 * DAY) return `in ${Math.floor(seconds / DAY)} d`;
  return formatInstant(iso, "long");
}

/**
 * A coarse interval phrase for a timestamp, in either direction — "3 min ago"
 * for a last run, "in 2 h" for a next one.
 *
 * Coarse deliberately: this labels a sync that the *server* schedules in hours,
 * and a dashboard that says "2 hours and 14 minutes ago" is claiming a precision
 * about the bank's freshness that nothing in the system actually has.
 *
 * Buckets at the day boundary hand off to the `long` date, so the phrase never
 * degrades into "37 d ago".
 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) throw new Error(`Not an ISO instant: ${JSON.stringify(iso)}`);
  const delta = (now.getTime() - then) / 1000;
  // A clock skew of a few seconds is not "in the future"; the server and the
  // browser are different machines and `next_sync_at` is computed server-side.
  return delta >= 0 ? ago(delta, iso) : until(-delta, iso);
}

// ---------------------------------------------------------------------------
// Calendar days on the wire
// ---------------------------------------------------------------------------
//
// The API's date *parameters* (`?start=`, `?end=`) are calendar days, not
// instants, so they must be built from the viewer's local components.

/**
 * A Date's **local** calendar day as `YYYY-MM-DD`.
 *
 * Deliberately not `toISOString().slice(0, 10)`: that converts to UTC first, so
 * `new Date(2026, 11, 31)` — local midnight on New Year's Eve — round-trips as
 * `2026-12-30` for anyone east of UTC. As a report range end that silently drops
 * the last day; as a form default it shows yesterday until the local clock
 * catches up with UTC. Both are invisible in a UTC browser, which is most of
 * them, which is why this lives in one place instead of at each call site.
 *
 * Note the direction: this is the *inverse* of {@link calendarDay}. A `Date` is
 * an instant and has no day of its own, so its day is the viewer's; a string
 * from the API names a day already and is read as one.
 */
export function isoDay(d: Date): string {
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

/** Today, as the viewer's local calendar day. The default for date inputs. */
export function todayIso(): string {
  return isoDay(new Date());
}
