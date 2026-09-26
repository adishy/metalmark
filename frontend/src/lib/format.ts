// Money and durations. Dates and times are not here — they live in `dates.ts`,
// which owns the vocabulary (decision H) and the calendar-day/instant frame
// distinction. Two modules rendering dates is how a vocabulary drifts.

/** Currencies with no minor unit. Anything absent is assumed to have 2. */
const MINOR_UNITS: Record<string, number> = { JPY: 0, KRW: 0, VND: 0 };

/** Decimal places the currency's minor unit has — the `?? 2` is the whole of
 *  "assume two unless told otherwise", in one place. */
function minorUnitDigits(currency: string): number {
  return MINOR_UNITS[currency] ?? 2;
}

/**
 * How many minor units make one major unit: `1` for JPY, `100` for USD, `1000`
 * for a 3-decimal currency. The scale the money helpers below count in.
 */
export function minorUnitScale(currency = "USD"): number {
  return 10 ** minorUnitDigits(currency);
}

/**
 * A decimal money string as an integer count of the currency's minor units,
 * **exactly** — the string is split on its point and scaled digits, so no float
 * is involved and nothing is rounded on the way.
 *
 * `null` has two meanings, and both are "this is not a whole number of minor
 * units": the text is not a decimal number at all (an empty split row, a
 * half-typed value — the caller's own validator owns those), or it carries
 * non-zero digits *finer* than the minor unit (`"0.004"` in USD). Trailing zeros
 * are of course fine: the API sends `"-100.0000"`, which is 10000 cents.
 */
function parseMinorUnits(text: string, digits: number): number | null {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(text.trim());
  if (!match || (!match[2] && !match[3])) return null;
  const [, sign, whole = "", frac = ""] = match;
  if (frac.length > digits && !/^0*$/.test(frac.slice(digits))) return null;
  const scaled = Number(`${whole || "0"}${frac.slice(0, digits).padEnd(digits, "0")}`);
  return sign === "-" ? -scaled : scaled;
}

/**
 * One money value as integer minor units (ADR-0005).
 *
 * Take the **string** path for anything exact: it parses the digits, so
 * `toMinorUnits("12.34", "USD")` is 1234 with no float in sight. A `number` is
 * already a float — the caller has left the exact path — so this can only round
 * it to the nearest minor unit, which is what a display figure wants and what
 * the split gate must never rely on (see `sumMinorUnits`).
 *
 * A value finer than the minor unit (`"0.004"`), and a value that is not a
 * number at all (an empty input row), come back rounded and as `0`
 * respectively: this is a *display and gate* conversion, and the callers that
 * care about the difference read `sumMinorUnits(...).exact`.
 */
export function toMinorUnits(value: string | number, currency = "USD"): number {
  if (typeof value === "number") {
    return Number.isFinite(value) ? Math.round(value * minorUnitScale(currency)) : 0;
  }
  const digits = minorUnitDigits(currency);
  const exact = parseMinorUnits(value, digits);
  if (exact !== null) return exact;
  const approx = Number(value.trim());
  return Number.isFinite(approx) ? Math.round(approx * minorUnitScale(currency)) : 0;
}

/** A total in minor units, and whether it is the exact one. */
export interface MinorSum {
  /** The total, counted in the currency's minor units. */
  minor: number;
  /**
   * True when every value was a whole number of minor units, so `minor` is the
   * exact total rather than a rounding of it. A client check that gates a write
   * the server validates exactly has to require this — otherwise `"0.004"` and
   * `"0.004"` "sum" to `"0.004"` here and the API answers 400.
   */
  exact: boolean;
}

/**
 * Sum money as **integer minor units** (ADR-0005 wants every monetary value to be
 * an exact decimal, never a float; this is the client's half of it).
 *
 * This module is the one place that rule is not honoured end to end: JavaScript
 * has no decimal type, and the app's money arrives as decimal strings that
 * `Intl` and ECharts both want as numbers. Integer minor units are the closest
 * thing to the server's `Decimal` that the client can do — the parts are exact
 * decimal strings scaled to the currency's minor unit (`MINOR_UNITS` above, so
 * JPY counts yen rather than hundredths of one) and integers add exactly. Turn
 * the result back into a decimal string with `fromMinorUnits` before it meets
 * `formatMoney`.
 *
 * The alternative is adding money as JS numbers, which is not wrong by a cent —
 * it is wrong by a hundredth of one, which is worse, because `0.1 + 0.2 !== 0.3`
 * and a total that is off in the last bit still renders exactly like a total.
 *
 * The one place this is not a display concern is the split sheet, whose client
 * gate used to accept a half-cent of drift the run above the API refuses
 * outright. Any caller making that judgement must check `.exact` as well — and
 * pass strings, since a `number` has already been through a float.
 */
export function sumMinorUnits(
  values: readonly (string | number)[],
  currency = "USD",
): MinorSum {
  const scale = minorUnitScale(currency);
  const digits = minorUnitDigits(currency);
  let minor = 0;
  let exact = true;
  for (const value of values) {
    if (typeof value === "number") {
      if (!Number.isFinite(value)) continue;
      const scaled = value * scale;
      if (!Number.isInteger(scaled)) exact = false;
      minor += Math.round(scaled);
      continue;
    }
    const parsed = parseMinorUnits(value, digits);
    if (parsed === null) exact = false;
    minor += parsed ?? toMinorUnits(value, currency);
  }
  return { minor, exact };
}

/**
 * Integer minor units back as a decimal string, exactly, for `formatMoney`.
 *
 * The inverse of the string path above, and here for the same reason: a total
 * that leaves the integer world as `minor / 100` re-enters the float one. JPY
 * gets no fractional part at all.
 */
export function fromMinorUnits(minor: number, currency = "USD"): string {
  const digits = minorUnitDigits(currency);
  const whole = Math.round(Math.abs(minor));
  if (digits === 0) return `${minor < 0 ? "-" : ""}${whole}`;
  const padded = String(whole).padStart(digits + 1, "0");
  const body = `${padded.slice(0, -digits)}.${padded.slice(-digits)}`;
  return minor < 0 ? `-${body}` : body;
}

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
 * A **transaction's** amount, with the sign §6.2 makes mandatory: `+$5,200.00`
 * for money in, `−$38.20` for money out.
 *
 * `formatMoney` is the magnitude — right for a balance, a total or a tick, which
 * a reader reads as an amount and not as a direction. A line item is not one of
 * those: whether it moved money in or out is the whole of what the row says, and
 * §6.2 measured that colour cannot carry it (the role pairs are 1.18:1–1.75:1
 * apart, under the 3:1 a colour difference needs to count as one). So the glyph
 * carries it, and it is added here — once — for every surface that shows a
 * transaction's amount, so the ledger and the review queue cannot disagree about
 * the same row.
 *
 * `+` is written rather than taken from `Intl`, for the reason `formatMoney`
 * writes its own minus: a locale- and ICU-dependent glyph in a column of figures
 * is a second vocabulary.
 */
export function formatMoneySigned(amount: string | number, currency = "USD"): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  return n > 0 ? `+${formatMoney(n, currency)}` : formatMoney(n, currency);
}

/**
 * The units a tick may be shortened to, largest first.
 *
 * `k` is there for the phone: an axis tick of `20 000` is six characters of
 * gutter on a 310 px chart, and the gutter is where a phone's plot area goes to
 * die. Everything below a thousand stays as it is — `$850` needs no help.
 */
const TICK_UNITS: ReadonlyArray<readonly [number, string]> = [
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "k"],
];

/** True when `scaled` is a number two decimal places can state *exactly*, so
 *  the shortened form is the same figure rather than a rounded one. */
function isExactTo2dp(scaled: number): boolean {
  const hundredths = scaled * 100;
  return Math.abs(hundredths - Math.round(hundredths)) < 1e-6;
}

/**
 * A money figure for an **axis tick** — a scale mark, not a number a reader acts
 * on. Use `formatMoney` for every figure that is the point of the screen.
 *
 * Ticks are "nice" numbers (0, 2 000, 40 000), so the full form spends five
 * characters saying "000". The shortened form is offered **only where it is
 * exact**: `2,000` is `$2k`, `2,500` is `$2.5k`, `1,250,000` is `$1.25M` — and
 * `1,234` is emphatically *not* `$1.2k`. It falls back to `formatMoney`, because
 * §6.5's rule ("never truncate a number into a different number") is a rule
 * about what the digits *mean*, and an axis is not exempt from it: the only
 * licence taken here is that `k`/`M`/`B` state the same value in fewer glyphs.
 * The exact figure is one tap away in the tooltip and stated in full by the
 * chart's `aria-label`, so nothing is lost by shortening the scale.
 *
 * The formatter it builds is deliberately not `formatMoney`'s: a tick shows no
 * minor units (`$2k`, never `$2.00k`) and no grouping, because the grouping
 * separator has nothing left to separate. Zero keeps its own case — `$0`, not
 * `$0.00`, for the same reason it is not `$0k`.
 */
export function formatMoneyTick(amount: string | number, currency = "USD"): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  // Not a number: `formatMoney` is where that is dealt with, and it is not a
  // case a tick reaches — an axis is built from numbers.
  if (!Number.isFinite(n)) return formatMoney(amount, currency);

  /** The digits a tick carries: symbol, no grouping, no minor units. */
  const compact = (value: number) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
      useGrouping: false,
    }).format(value);

  try {
    // Zero is the one tick with no unit to shorten: it is `$0`, not `$0.00`,
    // because a tick carries no minor units at all.
    if (n === 0) return compact(0);
    const abs = Math.abs(n);
    for (const [unit, suffix] of TICK_UNITS) {
      if (abs < unit) continue;
      const scaled = abs / unit;
      if (!isExactTo2dp(scaled)) continue;
      return `${n < 0 ? "−" : ""}${compact(scaled)}${suffix}`;
    }
  } catch {
    // An ISO code `Intl` refuses: there is no symbol to shorten around, so the
    // full form — which has its own fallback — is the honest answer.
    return formatMoney(n, currency);
  }
  return formatMoney(n, currency);
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
