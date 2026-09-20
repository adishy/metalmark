import { describe, it, expect } from "vitest";
import { formatMoney, formatDate } from "@/lib/format";

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

describe("formatDate", () => {
  it("renders an ISO date as a short human date", () => {
    const out = formatDate("2026-09-19T12:00:00Z");
    expect(out).toContain("2026");
    // Month is rendered as an abbreviation ("Sep"), not the raw number.
    expect(out).toMatch(/Sep/);
    expect(out).toContain("19");
  });
});
