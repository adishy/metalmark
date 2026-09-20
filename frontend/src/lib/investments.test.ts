import { describe, it, expect } from "vitest";
import {
  STALE_DAYS,
  excludedSentence,
  formatPrice,
  isCash,
  isZeroDecimal,
  sameQuantity,
  securityTypeLabel,
  trimDecimal,
} from "@/lib/investments";
import { formatMoney } from "@/lib/format";

describe("securityTypeLabel", () => {
  it("names a wire token as words", () => {
    expect(securityTypeLabel("mutual_fund")).toBe("Mutual fund");
    expect(securityTypeLabel("etf")).toBe("ETF");
    expect(securityTypeLabel("cash")).toBe("Cash");
  });

  it("opens out a token the map has never seen rather than leaving a gap", () => {
    // A type added to the API later must still read as words: the alternative is
    // `pe_fund` sitting in the middle of a money row.
    expect(securityTypeLabel("pe_fund")).toBe("pe fund");
  });
});

describe("isCash", () => {
  it("recognises the security_type that is really money (ADR-0033 §4)", () => {
    expect(isCash("cash")).toBe(true);
    expect(isCash("etf")).toBe(false);
  });
});

describe("trimDecimal", () => {
  it("shows the stored number and not the column's padding", () => {
    expect(trimDecimal("10.00000000")).toBe("10");
    expect(trimDecimal("1.50000000")).toBe("1.5");
    expect(trimDecimal("100")).toBe("100");
  });

  it("keeps every significant digit of a small quantity", () => {
    // String work, not `Number`: 0.00000001 of a coin is a position, and
    // `toFixed`/float display is how it becomes 0.
    expect(trimDecimal("0.00000001")).toBe("0.00000001");
    expect(trimDecimal("0.10000000")).toBe("0.1");
  });
});

describe("isZeroDecimal", () => {
  it("sees zero through the column's formatting", () => {
    expect(isZeroDecimal("0")).toBe(true);
    expect(isZeroDecimal("0.0000")).toBe(true);
    expect(isZeroDecimal("-0.00")).toBe(true);
    expect(isZeroDecimal("0.00000000")).toBe(true);
  });

  it("is not fooled by a small nonzero amount", () => {
    expect(isZeroDecimal("0.0001")).toBe(false);
    expect(isZeroDecimal("0.00000001")).toBe(false);
    expect(isZeroDecimal("-1.00")).toBe(false);
  });
});

describe("sameQuantity", () => {
  it("treats two spellings of one number as equal", () => {
    expect(sameQuantity("10.00000000", "10")).toBe(true);
    expect(sameQuantity("10.50000000", "10.5")).toBe(true);
    expect(sameQuantity("010.0", "10")).toBe(true);
  });

  it("reports the disagreement ADR-0034 is about", () => {
    expect(sameQuantity("10.00000000", "12")).toBe(false);
    expect(sameQuantity("0.00000000", "10")).toBe(false);
  });

  it("never counts an absent entry as agreement", () => {
    // `null` is "nobody typed anything", which is not the same as "the numbers
    // match" — the override line must not appear for a position with no row.
    expect(sameQuantity(null, "10")).toBe(false);
    expect(sameQuantity("10", null)).toBe(false);
    expect(sameQuantity(null, null)).toBe(false);
  });
});

describe("formatPrice", () => {
  it("renders an ordinary quote as money", () => {
    expect(formatPrice("1234.50000000", "USD")).toBe(formatMoney("1234.5", "USD"));
    expect(formatPrice("0.00000000", "USD")).toBe(formatMoney("0", "USD"));
  });

  it("refuses to round a real quote down to zero (§6.5)", () => {
    // `$0.00000412` is a quote, and `$0.00` is both a different number and the
    // one number that means "worthless".
    expect(formatPrice("0.00000412", "USD")).toBe("0.00000412 USD");
  });

  it("covers a currency with no minor unit", () => {
    // The test is on the rendered digits, so JPY needs no second copy of
    // MINOR_UNITS here: ¥0 for a nonzero quote is the same failure.
    expect(formatPrice("0.40000000", "JPY")).toBe("0.4 JPY");
    expect(formatPrice("1200.00000000", "JPY")).toBe(formatMoney("1200", "JPY"));
  });
});

describe("excludedSentence", () => {
  it("says nothing when nothing is missing", () => {
    // Null rather than "": it renders inside a live region, and an empty one
    // still announces.
    expect(excludedSentence(0, 0)).toBeNull();
  });

  it("names each count and its own fix", () => {
    expect(excludedSentence(1, 0)).toBe(
      "Excludes 1 position with no price — left out of the total, not counted as zero.",
    );
    expect(excludedSentence(0, 1)).toBe(
      "Excludes 1 position with no rate — left out of the total, not counted as zero.",
    );
    // Two counts, never one: "enter a price" and "enter a rate" are different
    // fixes, and a merged "3 excluded" would hide which is needed.
    expect(excludedSentence(2, 1)).toBe(
      "Excludes 2 positions with no price and 1 position with no rate — left out of the total, not counted as zero.",
    );
    expect(excludedSentence(0, 3)).toBe(
      "Excludes 3 positions with no rate — left out of the total, not counted as zero.",
    );
  });
});

describe("STALE_DAYS", () => {
  it("is a presentation threshold and nothing more", () => {
    expect(STALE_DAYS).toBe(7);
  });
});
