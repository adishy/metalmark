// Money and durations. Dates and times are not here — they live in `dates.ts`,
// which owns the vocabulary (decision H) and the calendar-day/instant frame
// distinction. Two modules rendering dates is how a vocabulary drifts.

/** Currencies with no minor unit. Anything absent is assumed to have 2. */
const MINOR_UNITS: Record<string, number> = { JPY: 0, KRW: 0, VND: 0 };

/*
 * The sign is supplied here, not by `Intl`, and is U+2212 MINUS SIGN rather than
 * the hyphen-minus (DESIGN.md §6.2). Two reasons, both load-bearing:
 *
 *  - `Intl`'s negative glyph varies by ICU version and locale, so the same
 *    amount could render differently on two machines.
 *  - The sign is not decoration. Colour cannot carry the income/expense
 *    distinction on its own — the token pairs are only 1.18:1 to 1.75:1 apart,
 *    well under the 3:1 WCAG 1.4.1 requires for a colour difference to count as
 *    a second visual distinction. So the glyph is a conformance requirement.
 *
 * `minimumFractionDigits` matters as much as the maximum: without it a value of
 * 12 renders "$12" beside "$12.00", and mixed precision inside one column
 * destroys the alignment that tabular figures buy.
 */
export function formatMoney(amount: string | number, currency = "USD"): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  const dp = MINOR_UNITS[currency] ?? 2;
  try {
    const body = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      minimumFractionDigits: dp,
      maximumFractionDigits: dp,
    }).format(Math.abs(n));
    return n < 0 ? `−${body}` : body;
  } catch {
    // Unknown ISO code: Intl throws. Degrade rather than crash the page.
    const body = `${Math.abs(n).toFixed(dp)} ${currency}`;
    return n < 0 ? `−${body}` : body;
  }
}

/**
 * A decimal string with its sign flipped — still a string, never via a float
 * (ADR-0005). Zero keeps no sign, so "0.00" never becomes "-0.00".
 *
 * The one place a card's balance crosses between the two ways of saying it: the
 * ledger stores it signed (ADR-0043, debt is negative) and a person reads and
 * types it as the amount owed.
 */
export function negateAmount(value: string): string {
  const t = value.trim();
  const body = t.replace(/^[+-]/, "");
  if (!/[1-9]/.test(body)) return body;
  return t.startsWith("-") ? body : `-${body}`;
}

/**
 * A duration in milliseconds, for a table cell: "820 ms", "1.4 s", "2 min 05 s".
 *
 * Two units at most, and never more than one decimal: a sync's duration is read
 * to answer "was that fast or did something hang?", and "1 m 4.284 s" answers
 * that no better than "1 min 04 s" while being harder to compare down a column.
 */
export function formatDuration(ms: number): string {
  const clamped = Math.max(0, ms);
  if (clamped < 1000) return `${Math.round(clamped)} ms`;
  // Bucket on the value *as displayed*, not the raw one: 59 999 ms is under a
  // minute, so an unrounded comparison would render it "60.0 s" — a label that
  // contradicts itself. Rounding to tenths first makes 59 999 ms read
  // "1 min 00 s", which is one millisecond generous and says one thing.
  const tenths = Math.round(clamped / 100);
  if (tenths < 600) return `${(tenths / 10).toFixed(1)} s`;
  const whole = Math.round(clamped / 1000);
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  if (minutes < 60) return `${minutes} min ${String(rest).padStart(2, "0")} s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
}
