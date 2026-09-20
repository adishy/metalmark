import { describe, it, expect } from "vitest";
import { formatMoney, formatDuration } from "@/lib/format";

// Intl uses NBSP / narrow-NBSP between symbol and digits in some runtimes;
// normalize to a plain space so assertions are locale-runtime-stable.
const norm = (s: string) => s.replace(/ | /g, " ");

describe("formatMoney", () => {
  it("formats USD with 2 decimal places", () => {
    expect(norm(formatMoney("1234.5", "USD"))).toBe("$1,234.50");
  });

  it("formats a whole USD amount with trailing zeros", () => {
    expect(norm(formatMoney(10, "USD"))).toBe("$10.00");
  });

  it("formats JPY with 0 decimal places (rounds)", () => {
    // JPY has no minor unit — must not show a decimal point.
    const out = norm(formatMoney("1234.5", "JPY"));
    expect(out).not.toContain(".");
    expect(out.replace(/[^0-9]/g, "")).toBe("1235");
  });

  // U+2212 MINUS SIGN, not the hyphen-minus, and not whatever `Intl` would emit
  // (DESIGN.md §6.2). Asserted with the codepoint named rather than the literal,
  // because the two glyphs are visually near-identical in most editors — which
  // is exactly how a regression here would slip through review.
  it("renders negatives with U+2212 MINUS SIGN", () => {
    const out = norm(formatMoney("-42.5", "USD"));
    expect(out).toBe("−$42.50");
    expect(out.charCodeAt(0)).toBe(0x2212);
    expect(out).not.toContain("-");
  });

  it("accepts a numeric amount as well as a string", () => {
    expect(norm(formatMoney(-1000, "USD"))).toBe("−$1,000.00");
  });

  it("does not put a sign on positive amounts", () => {
    // A bare `$12.00` is a balance or a total; `+` is only added where the
    // call site knows the value is income (§6.2).
    expect(norm(formatMoney("12", "USD"))).toBe("$12.00");
  });

  it("keeps two decimals on a whole number so a column stays aligned", () => {
    expect(norm(formatMoney("12", "USD"))).toBe("$12.00");
    expect(norm(formatMoney(0, "USD"))).toBe("$0.00");
  });

  it("falls back gracefully for an unknown currency code", () => {
    // Intl throws on a bogus ISO code; the fn must not crash.
    const out = norm(formatMoney("5", "NOTREAL"));
    expect(out).toContain("5.00");
    expect(out).toContain("NOTREAL");
  });
});

describe("formatDuration", () => {
  it("stays in milliseconds under a second", () => {
    expect(formatDuration(0)).toBe("0 ms");
    expect(formatDuration(412)).toBe("412 ms");
    expect(formatDuration(999)).toBe("999 ms");
  });

  it("uses one decimal for seconds", () => {
    expect(formatDuration(1000)).toBe("1.0 s");
    expect(formatDuration(1840)).toBe("1.8 s");
    expect(formatDuration(59_000)).toBe("59.0 s");
  });

  it("never renders a self-contradicting label at a bucket edge", () => {
    // 59 999 ms is under a minute, but a naive `seconds < 60` check formats it
    // as "60.0 s". Bucketing on the displayed value is what fixes that.
    expect(formatDuration(59_999)).toBe("1 min 00 s");
  });

  it("switches to minutes and seconds, zero-padded", () => {
    expect(formatDuration(64_000)).toBe("1 min 04 s");
    expect(formatDuration(3_599_000)).toBe("59 min 59 s");
  });

  it("switches to hours and minutes past an hour", () => {
    expect(formatDuration(3_600_000)).toBe("1 h 00 min");
    expect(formatDuration(3_930_000)).toBe("1 h 05 min");
  });

  it("clamps a negative to zero rather than rendering a negative duration", () => {
    // A duration is a difference of two server timestamps. Clock skew between
    // the two should not produce "-3 ms" in a table.
    expect(formatDuration(-5)).toBe("0 ms");
  });
});
