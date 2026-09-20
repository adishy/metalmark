// Money arrives as decimal strings; format for display without float coercion
// where it matters (display rounding only).

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

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
