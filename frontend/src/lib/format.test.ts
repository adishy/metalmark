import { describe, it, expect } from "vitest";
import {
  formatMoney,
  formatMoneyTick,
  formatDuration,
  fromMinorUnits,
  minorUnitScale,
  negateAmount,
  sumMinorUnits,
  toMinorUnits,
} from "@/lib/format";

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

describe("formatMoneyTick", () => {
  // The one licence this formatter takes is *exact* shortening: `$2k` and
  // `$2,000.00` are the same number, and the moment they are not, the full
  // figure comes back. That is the property these tests exist to hold, because
  // "shorten a money string" is one refactor away from "round it in the display"
  // — which §6.5 forbids outright.
  it("shortens only where the short form is the same figure", () => {
    expect(norm(formatMoneyTick(2000))).toBe("$2k");
    expect(norm(formatMoneyTick(40000))).toBe("$40k");
    expect(norm(formatMoneyTick(2500))).toBe("$2.5k");
    expect(norm(formatMoneyTick(1250000))).toBe("$1.25M");
    expect(norm(formatMoneyTick(2_000_000_000))).toBe("$2B");
  });

  it("falls back to the full figure rather than rounding into a different number", () => {
    // 1234 is not 1.2k. A tick that said so would be the display inventing a
    // number the ledger never held.
    expect(norm(formatMoneyTick(1234))).toBe("$1,234.00");
    expect(norm(formatMoneyTick("1234.56"))).toBe("$1,234.56");
  });

  it("prefers the largest unit that is still exact", () => {
    // 1,250,000 is 1.25M *and* 1,250k; the shorter form is the point.
    expect(norm(formatMoneyTick(1_250_000))).toBe("$1.25M");
  });

  it("signs negatives with U+2212, like every other money string", () => {
    const out = norm(formatMoneyTick(-2000));
    expect(out).toBe("−$2k");
    expect(out.charCodeAt(0)).toBe(0x2212);
  });

  it("keeps a short figure as it is", () => {
    // Below a thousand there is nothing to save, and `$850` is not `$0.85k`.
    expect(norm(formatMoneyTick(850))).toBe("$850.00");
  });

  it("states zero without minor units, as a tick and not an amount", () => {
    expect(norm(formatMoneyTick(0))).toBe("$0");
  });

  it("honours the currency, including one with no minor unit", () => {
    const jpy = norm(formatMoneyTick(3000, "JPY"));
    expect(jpy).not.toContain(".");
    expect(jpy.replace(/[^0-9]/g, "")).toBe("3");
    expect(jpy).toContain("k");
  });

  it("falls back for an unknown currency code instead of throwing", () => {
    const out = norm(formatMoneyTick(2000, "NOTREAL"));
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

describe("negateAmount", () => {
  it("flips the sign of a decimal string without rounding it", () => {
    expect(negateAmount("850.0000")).toBe("-850.0000");
    expect(negateAmount("-850.0000")).toBe("850.0000");
    expect(negateAmount("+12.5")).toBe("-12.5");
    expect(negateAmount("0.1")).toBe("-0.1");
  });

  it("never signs a zero", () => {
    expect(negateAmount("0.0000")).toBe("0.0000");
    expect(negateAmount("-0")).toBe("0");
  });
});

describe("minor-unit money arithmetic", () => {
  // ADR-0005 says money is never a float, and this is the client's half of it.
  // The bug it fixes was not "a cent off": floating point is off in the fifteenth
  // decimal place, which is worse, because a total that is wrong in the last bit
  // still renders exactly like a total.
  it("adds money the way the arithmetic does not", () => {
    expect(0.1 + 0.2).not.toBe(0.3); // the reason this module has a helper at all
    expect(sumMinorUnits(["0.1", "0.2"], "USD")).toEqual({ minor: 30, exact: true });
    expect(sumMinorUnits(["0.1", "0.2", "-0.3"], "USD")).toEqual({ minor: 0, exact: true });
  });

  it("parses a decimal string into minor units without a float in the way", () => {
    expect(toMinorUnits("12.34", "USD")).toBe(1234);
    expect(toMinorUnits("-100.0000", "USD")).toBe(-10000); // the API's storage scale
    expect(toMinorUnits("0", "USD")).toBe(0);
    expect(toMinorUnits(".5", "USD")).toBe(50);
    expect(toMinorUnits("+0.05", "USD")).toBe(5);
    expect(toMinorUnits("1234567.89", "USD")).toBe(123456789);
  });

  it("counts in the currency's own minor unit, not in hundredths", () => {
    // JPY has no minor unit: 1234 yen is 1234 units, not 123400.
    expect(toMinorUnits("1234", "JPY")).toBe(1234);
    expect(sumMinorUnits(["1234", "766"], "JPY")).toEqual({ minor: 2000, exact: true });
    expect(minorUnitScale("JPY")).toBe(1);
    expect(minorUnitScale("USD")).toBe(100);
  });

  it("reports a total it cannot state exactly instead of rounding it", () => {
    // "0.004" is not a whole cent. Rounding it here is how a client gate calls a
    // set balanced that the API (which compares Decimals, exactly) refuses.
    expect(sumMinorUnits(["0.004", "0.004"], "USD").exact).toBe(false);
    expect(sumMinorUnits(["0.004", "0.004", "-0.004"], "USD").exact).toBe(false);
    expect(sumMinorUnits(["0.004", "0.006"], "USD")).toEqual({ minor: 1, exact: false });
    // Trailing zeros are not extra precision: the API sends "-100.0000".
    expect(sumMinorUnits(["100.0000"], "USD").exact).toBe(true);
  });

  it("treats an unparseable value as nothing rather than as a number", () => {
    // A half-typed split row contributes nothing until it is a number; the
    // field's own validator is what refuses "abc".
    expect(sumMinorUnits(["", "  ", "abc", "1.00"], "USD")).toEqual({ minor: 100, exact: false });
  });

  it("sums a numeric value by rounding it to the minor unit", () => {
    // A `number` has already been through a float, so this is the display path:
    // it recovers the integer ECharts handed over — and reports the sum as
    // inexact when the scale did not land on one, which is the truth about a
    // float rather than a claim the callers may lean on.
    expect(sumMinorUnits([12.34, 0.06], "USD")).toEqual({ minor: 1240, exact: true });
    expect(sumMinorUnits([12.345], "USD")).toEqual({ minor: 1235, exact: false });
    expect(sumMinorUnits([12, 8], "USD")).toEqual({ minor: 2000, exact: true }); // integers are exact
  });

  it("renders minor units back as an exact decimal string", () => {
    expect(fromMinorUnits(-10000, "USD")).toBe("-100.00");
    expect(fromMinorUnits(5, "USD")).toBe("0.05");
    expect(fromMinorUnits(0, "USD")).toBe("0.00");
    expect(fromMinorUnits(300, "JPY")).toBe("300");
    expect(fromMinorUnits(-7, "JPY")).toBe("-7");
  });

  it("round-trips a money value through minor units without losing it", () => {
    // It comes back at the minor unit's scale rather than the storage scale —
    // "-100.0000" is "-100.00" — and it is the same number either way.
    expect(fromMinorUnits(toMinorUnits("-100.0000", "USD"), "USD")).toBe("-100.00");
    expect(fromMinorUnits(toMinorUnits("0.01", "USD"), "USD")).toBe("0.01");
    expect(fromMinorUnits(toMinorUnits("1234", "JPY"), "JPY")).toBe("1234");
    expect(fromMinorUnits(toMinorUnits("-0.05", "EUR"), "EUR")).toBe("-0.05");
  });
});
