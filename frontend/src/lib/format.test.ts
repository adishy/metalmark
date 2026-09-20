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

  it("renders negatives with a leading minus sign", () => {
    expect(norm(formatMoney("-42.5", "USD"))).toBe("-$42.50");
  });

  it("accepts a numeric amount as well as a string", () => {
    expect(norm(formatMoney(-1000, "USD"))).toBe("-$1,000.00");
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
