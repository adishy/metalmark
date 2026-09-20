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

/*
 * Thresholds are in seconds and are chosen so no phrase is ever *wrong*, only
 * coarse. "3 min ago" for something 150 s old is coarse; "1 min ago" for
 * something 90 s old is a lie in the direction a person notices, so each bucket
 * ends past its own boundary (45 → 90 → 60 min) rather than at it.
 *
 * `now` is a parameter so the output is a function of its inputs. A helper that
 * reads the clock is untestable at its boundaries, and these boundaries are the
 * whole implementation.
 */
const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Past `relativeTime`: "just now", "3 min ago", "2 h ago", "5 d ago", then a
 *  date — "37 d ago" is a worse answer than "Jul 3, 2026" once it is that old.
 *
 *  `floor` at every step, and each unit is left at the moment the next one
 *  begins: rounding would produce "90 min ago" followed by "2 h ago" as time
 *  advanced, and a label that jumps forward by half an hour is worse than one
 *  that is merely coarse. Flooring also means the phrase never overstates how
 *  much time has passed. */
function ago(seconds: number, iso: string): string {
  if (seconds < 45) return "just now";
  if (seconds < 90) return "1 min ago";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} min ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} h ago`;
  if (seconds < 7 * DAY) return `${Math.floor(seconds / DAY)} d ago`;
  return formatDate(iso);
}

/** Future `relativeTime`: "in 1 min", "in 2 h", "in 3 d". */
function until(seconds: number, iso: string): string {
  if (seconds < 45) return "any second now";
  if (seconds < 90) return "in 1 min";
  if (seconds < HOUR) return `in ${Math.floor(seconds / MINUTE)} min`;
  if (seconds < DAY) return `in ${Math.floor(seconds / HOUR)} h`;
  if (seconds < 7 * DAY) return `in ${Math.floor(seconds / DAY)} d`;
  return formatDate(iso);
}

/**
 * A coarse interval phrase for a timestamp, in either direction — "3 min ago"
 * for a last run, "in 2 h" for a next one.
 *
 * Coarse deliberately: this labels a sync that the *server* schedules in hours,
 * and a dashboard that says "2 hours and 14 minutes ago" is claiming a precision
 * about the bank's freshness that nothing in the system actually has.
 *
 * Buckets at the day boundary hand off to {@link formatDate}, so the phrase
 * never degrades into "37 d ago".
 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  const delta = (now.getTime() - then) / 1000;
  // A clock skew of a few seconds is not "in the future"; the server and the
  // browser are different machines and `next_sync_at` is computed server-side.
  return delta >= 0 ? ago(delta, iso) : until(-delta, iso);
}
