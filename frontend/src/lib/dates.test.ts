import { describe, it, expect } from "vitest";
import { isoDay, todayIso } from "@/lib/dates";

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
