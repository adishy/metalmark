// The reports' time window: what each preset means, in one place.
//
// Presets are **calendar periods** — a quarter is Jan–Mar, never "the last 90
// days" — because the reports bucket by calendar period too (`periods.py`), and a
// rolling window would draw buckets that begin and end mid-month while the axis
// claimed to show months. Two presets are not calendar periods, and each is named
// for what it actually is: `last-12-months` rolls, and `all` has no start at all.
//
// Pure and separate from the control that renders it, so the arithmetic a reader
// will notice a bug in (a quarter starting on the wrong month) is testable without
// a browser, a router or a fetch.

import { isoDay } from "@/lib/dates";
import type { GranularityParam } from "@/api/types";

/** The presets that resolve to a window on their own. `custom` is not one. */
export type ReportPreset =
  | "this-month"
  | "this-quarter"
  | "this-year"
  | "last-year"
  | "last-12-months"
  | "all";

/** What the control is set to: a preset, or the reader's own two dates. */
export type RangeMode = ReportPreset | "custom";

/**
 * A window, as the control knows it.
 *
 * `start` is `null` for exactly one case — `all` — and that is the whole reason
 * the API's `start` is optional: no client can compute where a household's data
 * begins, so it says "from the beginning" and the server answers with the date
 * and echoes it back.
 */
export interface ResolvedRange {
  start: string | null;
  end: string;
}

/**
 * The default, and it is deliberate: `this-year` is what the page rendered
 * before there was a control, so a reader who never touches it sees exactly what
 * they saw yesterday.
 */
export const DEFAULT_PRESET: ReportPreset = "this-year";

export const PRESETS: ReadonlyArray<{ id: ReportPreset; label: string }> = [
  { id: "this-month", label: "This month" },
  { id: "this-quarter", label: "This quarter" },
  { id: "this-year", label: "This year" },
  { id: "last-year", label: "Last year" },
  { id: "last-12-months", label: "Last 12 months" },
  { id: "all", label: "All time" },
];

export const MODES: ReadonlyArray<{ id: RangeMode; label: string }> = [
  ...PRESETS,
  { id: "custom", label: "Custom" },
];

export const DEFAULT_GRANULARITY: GranularityParam = "auto";

/**
 * The granularity control, in the order `periods.py` declares them.
 *
 * `Auto` first because it is the default and the one most readers want: it lets
 * the *server* pick the cut from the span, which is the only place that knows how
 * much data is in the window. The rest are an override for a reader who wants a
 * specific shape.
 */
export const GRANULARITY_CHOICES: ReadonlyArray<{ id: GranularityParam; label: string }> = [
  { id: "auto", label: "Auto" },
  { id: "day", label: "Daily" },
  { id: "week", label: "Weekly" },
  { id: "month", label: "Monthly" },
  { id: "quarter", label: "Quarterly" },
  { id: "year", label: "Yearly" },
];

/**
 * A preset's window, against the reader's own clock.
 *
 * `today` is a parameter rather than a call to `new Date()` inside, so a test can
 * pin a quarter boundary without freezing time globally — and so the caller can
 * pass the same "now" it used for anything else on screen.
 */
export function resolvePreset(preset: ReportPreset, today: Date = new Date()): ResolvedRange {
  const y = today.getFullYear();
  const m = today.getMonth();
  const firstOf = (yy: number, mm: number) => isoDay(new Date(yy, mm, 1));
  // Day 0 of the *next* month is the last day of this one — which is also how a
  // February gets 28 or 29 days without this module knowing about leap years.
  const lastOf = (yy: number, mm: number) => isoDay(new Date(yy, mm + 1, 0));

  switch (preset) {
    case "this-month":
      return { start: firstOf(y, m), end: lastOf(y, m) };
    case "this-quarter": {
      const q = Math.floor(m / 3) * 3;
      return { start: firstOf(y, q), end: lastOf(y, q + 2) };
    }
    case "this-year":
      return { start: firstOf(y, 0), end: lastOf(y, 11) };
    case "last-year":
      return { start: firstOf(y - 1, 0), end: lastOf(y - 1, 11) };
    case "last-12-months":
      // 364 days back, so it ends on the same day-of-month it starts: 365 would
      // make "12 months" 12 months and a day, and a window whose two ends disagree
      // about the day of the month is one a reader has to count.
      return { start: isoDay(new Date(y, m, today.getDate() - 364)), end: isoDay(today) };
    case "all":
      // Open at the start on purpose: the server knows where the data begins and
      // this does not. `end` is the reader's own today, because the *client* is
      // the one that knows it — a server in UTC would answer one day off for a
      // household in UTC+13.
      return { start: null, end: isoDay(today) };
  }
}

/** `YYYY-MM-DD` and nothing else. A malformed bound is dropped, not guessed at. */
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The window a set of URL params describes.
 *
 * Anything unusable falls back to the default preset rather than erroring: a
 * hand-edited or truncated link should show the ordinary report, not an error
 * page. The one thing never guessed at is `custom` with a missing or malformed
 * side — half a window silently completed with today's date is a chart the reader
 * would have no way to tell from one they asked for.
 */
export function rangeFromParams(
  params: URLSearchParams,
  today: Date = new Date(),
): { mode: RangeMode; range: ResolvedRange } {
  const fallback = {
    mode: DEFAULT_PRESET as RangeMode,
    range: resolvePreset(DEFAULT_PRESET, today),
  };
  const requested = params.get("range");
  if (requested === null) return fallback;

  if (requested === "custom") {
    const start = params.get("start");
    const end = params.get("end");
    if (start !== null && end !== null && ISO_DAY.test(start) && ISO_DAY.test(end)) {
      return { mode: "custom", range: { start, end } };
    }
    return fallback;
  }

  const preset = PRESETS.find((p) => p.id === requested);
  if (!preset) return fallback;
  return { mode: preset.id, range: resolvePreset(preset.id, today) };
}

/**
 * The granularity a set of URL params asks for.
 *
 * Validated against the vocabulary rather than cast, for the same reason the
 * window is: an unknown cut is a 422 from the server, and a link someone mistyped
 * should show the default report rather than an error. Kept beside
 * `rangeFromParams` so the two halves of the URL are read by the same rules.
 */
export function granularityFromParams(params: URLSearchParams): GranularityParam {
  const raw = params.get("granularity");
  return GRANULARITY_CHOICES.some((c) => c.id === raw)
    ? (raw as GranularityParam)
    : DEFAULT_GRANULARITY;
}

/** Whether a preset's two days are a window the API will accept. */
export function isUsable(range: ResolvedRange): boolean {
  // An inverted window is a 422 from the server, which is a worse way for a
  // reader to find out than not sending it: the control keeps the last good
  // window on screen instead. `start` is null only for `all`, which is open.
  return range.start === null || range.start <= range.end;
}
