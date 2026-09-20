// The investments vocabulary the API does not carry: how a security type reads
// on screen, how a quantity or a price is shown without rounding it into a
// different number, and the sentence that says a total is missing something.
//
// It lives beside `format.ts` (money and durations) and `dates.ts` (dates) for
// the same reason they do: one module per vocabulary, so the allocation card and
// the holdings list cannot phrase the same fact two ways.

import { formatMoney } from "@/lib/format";
import type { Money } from "@/api/types";

// ---------------------------------------------------------------------------
// Security types
// ---------------------------------------------------------------------------

// `SECURITY_TYPES` in backend/app/models/investments.py, in words. The API sends
// the bare token because it is a wire vocabulary and a grouping key; naming it
// is this side's job.
const SECURITY_TYPE_LABELS: Record<string, string> = {
  stock: "Stock",
  etf: "ETF",
  mutual_fund: "Mutual fund",
  bond: "Bond",
  option: "Option",
  crypto: "Crypto",
  cash: "Cash",
  other: "Other",
};

/**
 * `"mutual_fund"` → `"Mutual fund"`.
 *
 * A type this map has never seen falls back to its own token with the
 * underscores opened out, so a type added to the API later still reads as words
 * rather than as a gap in the row.
 */
export function securityTypeLabel(type: string): string {
  return SECURITY_TYPE_LABELS[type] ?? type.replace(/_/g, " ");
}

/**
 * ADR-0033 §4: uninvested cash inside a derived account is a real holding, not
 * an instrument whose price is missing. It has a quantity and a value like any
 * other position, and naming it here is what stops it reading as a security with
 * no quote.
 */
export function isCash(type: string): boolean {
  return type === "cash";
}

// ---------------------------------------------------------------------------
// Decimals
// ---------------------------------------------------------------------------

/**
 * A decimal string with its trailing zeros trimmed: `10.00000000` → `10`,
 * `1.50000000` → `1.5`.
 *
 * String work rather than `Number`: a quantity is `NUMERIC(19,8)` and what is
 * shown must be what is stored (ADR-0005). This deliberately does not go through
 * `formatMoney` — a share count is not money, has no currency and no minor unit,
 * and the store column pads every one of them to eight decimals.
 */
export function trimDecimal(value: Money): string {
  const v = value.trim();
  if (!v.includes(".")) return v;
  return v.replace(/0+$/, "").replace(/\.$/, "");
}

/** A decimal string reduced to a canonical form for comparison, without a float:
 *  sign, leading zeros and trailing fractional zeros are all dropped. */
function canonicalDecimal(value: string): string {
  let v = value.trim();
  let sign = "";
  if (v.startsWith("+") || v.startsWith("-")) {
    sign = v[0] === "-" ? "-" : "";
    v = v.slice(1);
  }
  const [whole = "", frac = ""] = v.split(".");
  const body = frac.replace(/0+$/, "");
  const digits = whole.replace(/^0+/, "") || "0";
  const out = body ? `${digits}.${body}` : digits;
  // `-0` and `0.00000000` are both zero, and neither is a negative quantity.
  return out === "0" ? "0" : sign + out;
}

/**
 * Whether two decimal strings name the same number — `"10.00000000"` and `"10"`
 * do.
 *
 * This is the test ADR-0034 turns on: `manual_quantity` is what a human typed and
 * `quantity` is what history says, and the whole point of carrying both is to
 * report when they *differ*. Compared as strings so no quantity is ever compared
 * at float precision, and null (nobody typed anything) is never equal to a value.
 */
export function sameQuantity(a: Money | null, b: Money | null): boolean {
  if (a === null || b === null) return false;
  return canonicalDecimal(a) === canonicalDecimal(b);
}

/**
 * Whether a decimal string is zero, without reading it as a float — `"0"`,
 * `"0.0000"` and `"-0.00"` all are.
 *
 * Used to decide whether to *say* something, never to decide what a number is:
 * an unaccounted-cash plug of zero is not worth a line, and asking `Number(x)`
 * would be the one place in this view a decimal becomes a float for a reason
 * that is not display.
 */
export function isZeroDecimal(value: Money): boolean {
  return canonicalDecimal(value) === "0";
}

/**
 * A security price, in the currency it is quoted in.
 *
 * **Not `formatMoney` alone**, and the schema says why: prices are
 * `NUMERIC(19,8)` precisely because `$0.00000412` is a real quote that four
 * decimals round to zero. `formatMoney` rounds to the currency's minor unit and
 * would print `$0.00` — a number replaced by a different number (§6.5), and the
 * one wrong number that also means "worthless". So a nonzero price that the
 * money formatter renders as zero is shown as the stored decimal itself, with
 * the currency code that says what it is quoted in.
 *
 * The test is on the *rendered* digits rather than a minor-unit threshold, so
 * JPY (0 dp) is covered without this module keeping a second copy of
 * `MINOR_UNITS`. A price of exactly zero keeps the ordinary rendering: it is a
 * real price for a written-off position, and `$0.00` is what it means.
 */
export function formatPrice(price: Money, currency: string): string {
  const money = formatMoney(price, currency);
  const renderedAsZero = /^0*$/.test(money.replace(/[^0-9]/g, ""));
  const priceIsNonZero = /[1-9]/.test(price);
  if (renderedAsZero && priceIsNonZero) return `${trimDecimal(price)} ${currency}`;
  return money;
}

// ---------------------------------------------------------------------------
// Staleness
// ---------------------------------------------------------------------------

/**
 * When a price starts reading as stale.
 *
 * Presentation only: the API reports `stale_days` and `max_stale_days` and never
 * judges them (ADR-0032 §5 asks for the staleness to be *surfaced*, and this view
 * surfaces the age of every price it used). A week is where a price stops
 * reading as "current" — prices are entered by hand in v1, and a net-worth total
 * frozen at the last quote forever is the silence that ADR exists to prevent.
 */
export const STALE_DAYS = 7;

// ---------------------------------------------------------------------------
// Unpriced positions
// ---------------------------------------------------------------------------

/**
 * The sentence that says a total is missing positions, or null when it is not.
 *
 * Two counts rather than one because the two problems have different fixes —
 * enter a price, or enter an FX rate — and the backend keeps them apart for that
 * reason (`AllocationOut`). Null rather than an empty string when nothing is
 * missing: this renders inside a live region, and an empty one still announces.
 *
 * It says "not counted as zero" explicitly. That is the whole distinction this
 * view exists to draw: a position we cannot value and a position worth nothing
 * must not look alike (ADR-0032 §5).
 */
export function excludedSentence(unpriced: number, noRate: number): string | null {
  if (unpriced <= 0 && noRate <= 0) return null;
  const parts: string[] = [];
  if (unpriced > 0) {
    parts.push(`${unpriced} ${unpriced === 1 ? "position" : "positions"} with no price`);
  }
  if (noRate > 0) {
    parts.push(`${noRate} ${noRate === 1 ? "position" : "positions"} with no rate`);
  }
  return `Excludes ${parts.join(" and ")} — left out of the total, not counted as zero.`;
}
