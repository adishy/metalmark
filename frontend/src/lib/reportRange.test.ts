import { describe, it, expect } from "vitest";
import {
  DEFAULT_GRANULARITY,
  DEFAULT_PRESET,
  GRANULARITY_CHOICES,
  PRESETS,
  granularityFromParams,
  isUsable,
  rangeFromParams,
  resolvePreset,
  type ReportPreset,
} from "@/lib/reportRange";

/** A local mid-quarter date. Local, not UTC: every boundary here is a local one. */
const MARCH = new Date(2026, 2, 15);

const params = (init: Record<string, string>) => new URLSearchParams(init);

describe("resolvePreset", () => {
  it("cuts each calendar period at its own boundaries", () => {
    expect(resolvePreset("this-month", MARCH)).toEqual({
      start: "2026-03-01",
      end: "2026-03-31",
    });
    expect(resolvePreset("this-quarter", MARCH)).toEqual({
      start: "2026-01-01",
      end: "2026-03-31",
    });
    expect(resolvePreset("this-year", MARCH)).toEqual({ start: "2026-01-01", end: "2026-12-31" });
    expect(resolvePreset("last-year", MARCH)).toEqual({ start: "2025-01-01", end: "2025-12-31" });
  });

  it("puts a quarter on the right side of a boundary", () => {
    // The off-by-one a reader notices: the day a quarter turns over. Q2 is
    // Apr–Jun, so 2026-03-31 is still Q1 and 2026-04-01 is not.
    const lastDayOfQ1 = new Date(2026, 2, 31);
    const firstDayOfQ2 = new Date(2026, 3, 1);
    expect(resolvePreset("this-quarter", lastDayOfQ1)).toEqual({
      start: "2026-01-01",
      end: "2026-03-31",
    });
    expect(resolvePreset("this-quarter", firstDayOfQ2)).toEqual({
      start: "2026-04-01",
      end: "2026-06-30",
    });
    // The last quarter, which is the one whose `lastOf` has to roll the year.
    expect(resolvePreset("this-quarter", new Date(2026, 11, 5))).toEqual({
      start: "2026-10-01",
      end: "2026-12-31",
    });
  });

  it("knows how long February is", () => {
    // `lastOf` asks for day 0 of the next month rather than subtracting a day
    // from the 1st; this is the assertion that keeps that true.
    expect(resolvePreset("this-month", new Date(2028, 1, 10)).end).toBe("2028-02-29");
    expect(resolvePreset("this-month", new Date(2026, 1, 10)).end).toBe("2026-02-28");
  });

  it("rolls last-12-months back to the same day of the month", () => {
    // 364 days, not 365: the window's two ends should agree about the day, or
    // "12 months" is 12 months and a day and a reader has to count.
    expect(resolvePreset("last-12-months", MARCH)).toEqual({
      start: "2025-03-16",
      end: "2026-03-15",
    });
    // Across a year boundary and a February, where naive arithmetic breaks.
    expect(resolvePreset("last-12-months", new Date(2026, 0, 1))).toEqual({
      start: "2025-01-02",
      end: "2026-01-01",
    });
  });

  it("leaves all time open at the start", () => {
    // The one range a client cannot compute. `end` is still the reader's today,
    // because the client is the side that knows what day it is there.
    expect(resolvePreset("all", MARCH)).toEqual({ start: null, end: "2026-03-15" });
  });

  it("covers every preset, so a new one cannot go unhandled", () => {
    // Exhaustiveness lives in the switch; this catches the other half — a preset
    // that exists in `PRESETS` (and so is reachable from the URL) but resolves to
    // something unusable.
    for (const p of PRESETS) {
      const r = resolvePreset(p.id, MARCH);
      expect(isUsable(r), `${p.id} resolved to an inverted window`).toBe(true);
    }
    expect(PRESETS.map((p) => p.id)).toContain(DEFAULT_PRESET);
  });
});

describe("rangeFromParams", () => {
  it("defaults when there is no range at all", () => {
    // A reader who never touches the control is on the default preset, which is
    // what the page rendered before the control existed.
    const { mode, range } = rangeFromParams(params({}), MARCH);
    expect(mode).toBe(DEFAULT_PRESET);
    expect(range).toEqual({ start: "2026-01-01", end: "2026-12-31" });
  });

  it("resolves a preset named in the URL", () => {
    expect(rangeFromParams(params({ range: "this-quarter" }), MARCH)).toEqual({
      mode: "this-quarter",
      range: { start: "2026-01-01", end: "2026-03-31" },
    });
  });

  it("takes a custom window verbatim", () => {
    const { mode, range } = rangeFromParams(
      params({ range: "custom", start: "2024-02-01", end: "2024-03-09" }),
      MARCH,
    );
    expect(mode).toBe("custom");
    expect(range).toEqual({ start: "2024-02-01", end: "2024-03-09" });
  });

  it("falls back rather than erroring on a link it cannot use", () => {
    // Hand-edited, truncated or year-old links should show the ordinary report.
    // An error page for a URL someone typed wrong is a worse answer than the
    // default view, and the default view is at least *true*.
    for (const init of [
      { range: "fortnight" },
      { range: "" },
      { range: "custom" },
      { range: "custom", start: "2026-01-01" },
      { range: "custom", end: "2026-03-01" },
      { range: "custom", start: "01/01/2026", end: "2026-03-01" },
      { range: "custom", start: "2026-01-01", end: "yesterday" },
      { range: "custom", start: "custom", end: "custom" },
    ]) {
      const { mode } = rangeFromParams(params(init as Record<string, string>), MARCH);
      expect(mode, JSON.stringify(init)).toBe(DEFAULT_PRESET);
    }
  });

  it("does not silently complete a half-given custom window", () => {
    // The specific case the fallback exists for: a custom range missing its end
    // must not become "…through today", which is a chart indistinguishable from
    // one the reader asked for.
    const { range } = rangeFromParams(params({ range: "custom", start: "2026-01-01" }), MARCH);
    expect(range).not.toEqual({ start: "2026-01-01", end: "2026-03-15" });
    expect(range).toEqual(resolvePreset(DEFAULT_PRESET, MARCH));
  });

  it("refuses an inverted custom window instead of sending it", () => {
    // It parses — both sides are well-formed days — so `rangeFromParams` hands it
    // back and `isUsable` is what the caller checks it with. The server answers
    // 422 for this, which is a worse way to find out.
    const { mode, range } = rangeFromParams(
      params({ range: "custom", start: "2026-06-01", end: "2026-01-01" }),
      MARCH,
    );
    expect(mode).toBe("custom");
    expect(isUsable(range)).toBe(false);
  });
});

describe("granularityFromParams", () => {
  it("takes a cut the server knows", () => {
    for (const g of ["day", "week", "month", "quarter", "year", "auto"] as const) {
      expect(granularityFromParams(params({ granularity: g }))).toBe(g);
    }
  });

  it("defaults when the cut is missing or not in the vocabulary", () => {
    // A cut the server does not know is a 422. A link someone mistyped should
    // show the default report instead, the same way a bad window does.
    for (const raw of ["", "fortnight", "MONTH", "monthly", "30d"]) {
      expect(granularityFromParams(params({ granularity: raw }))).toBe(DEFAULT_GRANULARITY);
    }
    expect(granularityFromParams(params({}))).toBe(DEFAULT_GRANULARITY);
  });

  it("offers auto first, because it is the default", () => {
    expect(GRANULARITY_CHOICES[0].id).toBe(DEFAULT_GRANULARITY);
    expect(GRANULARITY_CHOICES.map((c) => c.id)).toContain("quarter");
  });
});

describe("isUsable", () => {
  it("accepts a one-day window and an open one", () => {
    // A single day is a real range, not an empty one.
    expect(isUsable({ start: "2026-03-15", end: "2026-03-15" })).toBe(true);
    // `all` is open at the start; there is nothing to invert.
    expect(isUsable({ start: null, end: "2026-03-15" })).toBe(true);
  });

  it("rejects an inverted window", () => {
    expect(isUsable({ start: "2026-03-16", end: "2026-03-15" })).toBe(false);
  });
});

describe("the preset vocabulary", () => {
  it("has a label for every preset, and no duplicates", () => {
    const ids = PRESETS.map((p) => p.id as string);
    expect(new Set(ids).size).toBe(ids.length);
    for (const { label } of PRESETS) expect(label.length).toBeGreaterThan(0);
  });

  it("does not include custom among the resolvable presets", () => {
    // `resolvePreset` takes a `ReportPreset`; `custom` is a mode the control can
    // be in, never a window it can compute. Keeping them in separate types is what
    // makes that a compile error rather than a case that returns nonsense.
    expect(PRESETS.map((p) => p.id)).not.toContain("custom" as ReportPreset);
  });
});
