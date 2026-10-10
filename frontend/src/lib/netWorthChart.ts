// The net-worth line, as an ECharts option built from the report and nothing else.
//
// Pure, so the rules that decide what a reader sees — which points are partial,
// what the tooltip says about them, where an account starts being counted, which
// intervals are a *change* at all — are tested without a canvas. Three decisions
// from session 04's audit live here, and a fourth from the look pass:
//
// * **A time axis.** Points are the window's first day and then each bucket's
//   last, so they are not evenly spaced; a category axis drew them as if they
//   were, stretching the first interval.
// * **A curve that cannot say anything the numbers do not.** `smooth` on its own
//   overshoots at a step and draws curvature between two carried-forward
//   balances where nothing moved. `smoothMonotone: "x"` is the option that keeps
//   a curve to its data: the control points it builds share their own point's
//   value, so a segment between two equal balances is drawn exactly flat and a
//   step cannot swing past the value it steps to. What the line reads as is a
//   rounder telling of the same two numbers.
// * **A partial point looks partial** (ADR-0045). A point that cannot count an
//   account — no rate, no price, no balance — is drawn hollow and says which
//   account and why; one where an account starts being counted says so. Colour
//   is never the only signal: the tooltip and the note under the chart say it
//   in words. The line carries no resting markers, so the two kinds of point
//   that *mean* something are drawn on purpose (`partialMarks`) and the rest are
//   left to the hover, which is what a line is for.
// * **A change is only drawn where both ends count the same accounts.** The
//   strip under the line is the movement from one point to the next, and an
//   interval where an account starts being counted — or stops being countable —
//   is not a movement of money. Those bars are not drawn at all, and the tooltip
//   says which of the two happened rather than leaving a gap unexplained.
import type { EChartsOption } from "echarts";
import type { Granularity, MissingAccount, MissingReason, NetWorthSeries } from "@/api/types";
import { formatBucket, formatDay, isoDay } from "@/lib/dates";
import { formatMoney, formatMoneyTick } from "@/lib/format";
import {
  barEndRadius,
  chartArea,
  chartAxis,
  chartTooltip,
  emphasisLine,
  emphasisStrip,
  linkedPointer,
  phoneCanvas,
  pointerLineOnly,
  rangeBrush,
  valueTicks,
  zeroRule,
  type ChartBox,
  type TooltipPoint,
} from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

/** What each reason means, in the words a reader sees. */
export const MISSING_REASON: Record<MissingReason, string> = {
  not_started: "not tracked yet",
  no_balance: "no balance entered",
  no_rate: "no exchange rate",
  no_price: "a holding has no price",
};

/** A point leaves an account out because the ledger *cannot* value it — as
 *  opposed to one it has not started tracking, which is the normal start of a
 *  history rather than a hole in it. */
export const isGap = (m: MissingAccount) => m.reason !== "not_started";

type Point = NetWorthSeries["points"][number];

/** A local-midnight timestamp for an ISO day, so the axis and `isoDay` agree
 *  about which day a point is on in every timezone. */
function dayTs(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

/**
 * The least room one x label takes along the axis, in px, per bucket: the label
 * itself (`Sep 25` is 40 px of glyphs, `Sep` 22, `2026` 28) and the air that
 * makes the next one a separate word. Measured on the drawn canvas.
 */
const LABEL_PITCH: Record<Granularity, number> = {
  day: 72,
  week: 72,
  month: 48,
  quarter: 44,
  year: 52,
};

/**
 * Which of the series' points the x axis names, as timestamps: every `n`th,
 * counted back from the newest, with `n` the smallest stride that leaves each
 * label `LABEL_PITCH` of the plot's width.
 *
 * The axis used to leave this to ECharts' tick search under `hideOverlap`, and
 * that pair fails in both directions. `hideOverlap` drops a label only once it
 * *touches* the next, so a week on a phone card drew seven of them shoulder to
 * shoulder — one run of text, not seven dates. And a time axis restarts its
 * ticks at every month, so a month's window read `Sep 13, Sep 21,` a hole,
 * `Oct 01, Oct 05, Oct 09`: uneven where it was not crowded.
 *
 * Counting **points** rather than days is what keeps the rhythm: the labels sit
 * on dates the series has a figure for, a constant number of buckets apart. It
 * counts back from the newest because that is the end a reader looks at, and
 * the one that must be named.
 *
 * The two ends of a window are not buckets like the rest, and each gets a rule:
 *
 * - The **newest** point is today, part-way into its bucket, so it can sit a
 *   third of a month from the month-end before it. It is always named — it is
 *   the figure the card's headline states — and a label the stride put too
 *   close to it gives way. That costs the rhythm one beat at the right-hand end
 *   (`… Jul, Aug, Oct`), which is the price of naming today.
 * - The **first** point is the window's opening day. On a daily axis that is a
 *   day like any other and takes its turn in the stride. On any coarser one it
 *   is not a bucket's end, and there is no reader it has to be named for, so it
 *   is left to the tooltip rather than costing the left-hand end a beat too —
 *   unless it would leave the axis with a single label, which says where the
 *   window ends and not what it spans.
 *
 * `points` is whatever the plot is showing: the whole series, or the part of
 * it a brush has narrowed the window to (`brushedAxes`).
 */
export function xLabelPoints(
  points: Point[],
  granularity: Granularity,
  plotWidth: number,
): number[] {
  if (points.length === 0) return [];
  const ts = points.map((p) => dayTs(p.date));
  const last = ts.length - 1;
  const span = ts[last] - ts[0];
  if (span <= 0) return [ts[last]];
  const pitch = LABEL_PITCH[granularity];
  const stride = Math.ceil(last / Math.max(1, Math.floor(plotWidth / pitch)));
  const first = granularity === "day" ? 0 : 1;
  // Months are 28 to 31 days, so "too close" is well short of a full pitch: it
  // is there to catch an end of the window, not a February.
  const tooClose = (0.75 * pitch * span) / plotWidth;
  const picked = [ts[last]];
  for (let i = last - stride; i >= first; i -= stride) {
    if (picked[picked.length - 1] - ts[i] >= tooClose) picked.push(ts[i]);
  }
  if (picked.length === 1) picked.push(ts[0]);
  return picked.reverse();
}

/**
 * What the x axis's labels say, and which points they sit on.
 *
 * The vocabulary is §6.6's, and the entry point is `formatBucket` — the one
 * function that decides what a granularity looks like on screen, and the one the
 * cash-flow chart's axis already goes through. So this axis reads in the same
 * words as the chip the pointer drags along it (`Sep 26`, `formatDay`), which is
 * the whole complaint: an axis saying `04:00` while the chip beside it says
 * `Sep 26` is two vocabularies on one chart.
 *
 * Which *word* is the window's resolved granularity's, which the server echoes
 * back on the series and which is a fact about the data — not something to be
 * inferred from a tick's own value, which is a second opinion about the window
 * that would disagree exactly where one straddles a month.
 */
function xAxisLabels(data: NetWorthSeries | undefined, plotWidth: number) {
  const granularity: Granularity = data?.granularity ?? "day";
  return {
    customValues: xLabelPoints(data?.points ?? [], granularity, plotWidth),
    // A time axis's tick value is a timestamp; `dayTs` built it from local
    // components, so `isoDay` reads back the same day in every timezone.
    formatter: (value: number) => formatBucket(isoDay(new Date(Number(value))), granularity),
  };
}

/** Accounts fully counted at this point that had not started at the one before
 *  it. One that moves from "not tracked yet" to "no rate" has not started being
 *  counted — it has only changed why it is left out. */
export function startsHere(points: Point[], i: number): string[] {
  if (i === 0) return [];
  const before = new Set(
    points[i - 1].missing.filter((m) => m.reason === "not_started").map((m) => m.account_id),
  );
  const now = new Set(points[i].missing.map((m) => m.account_id));
  return points[i - 1].missing
    .filter((m) => before.has(m.account_id) && !now.has(m.account_id))
    .map((m) => m.name);
}

/** Do two points count exactly the same accounts, for the same reasons? */
function sameAccounts(a: Point, b: Point): boolean {
  if (a.missing.length !== b.missing.length) return false;
  return a.missing.every((m) =>
    b.missing.some((n) => n.account_id === m.account_id && n.reason === m.reason),
  );
}

/**
 * What the tooltip says about one interval, and why a bar may be missing.
 *
 * A *value* is a real movement of money between two points that count the same
 * accounts. `gap` and `started` are the two ways a strip's bar is deliberately
 * absent — the interval still happened, but the difference between its ends is
 * not money, and printing it as one would be the strip inventing a figure.
 */
export type ChangeNote =
  | { kind: "value"; value: number; since: string }
  | { kind: "started" }
  | { kind: "gap" };

/** The change between two points: a subtraction of two figures the server
 *  computed, never a sum of transactions in the client. Money arrives as decimal
 *  strings (ADR-0005) and this is the same expression the headline above the
 *  chart has always used — it states the one place a float enters, rather than
 *  adding a second path to it. */
const changeBetween = (a: Point, b: Point) => Number(b.net_worth) - Number(a.net_worth);

/**
 * The change each point states, indexed with it: `null` where there is nothing
 * honest to draw — the first point (no interval before it), and any interval
 * whose two ends do not count the same accounts.
 */
export function changesPerPoint(points: Point[]): (ChangeNote | null)[] {
  return points.map((p, i) => {
    if (i === 0) return null;
    const prev = points[i - 1];
    if (!sameAccounts(prev, p)) {
      // Which of the two: an interval that leaves something out cannot be
      // totalled either, so a gap anywhere in it wins.
      return { kind: p.missing.some(isGap) || prev.missing.some(isGap) ? "gap" : "started" };
    }
    return { kind: "value", value: changeBetween(prev, p), since: prev.date };
  });
}

/**
 * The span a headline may state, as the indices of the first and last point in
 * it.
 *
 * A headline sits above this chart and states one figure, so it has to be a
 * figure the chart below it draws — and the strip draws nothing across two
 * points that do not count the same accounts (`changesPerPoint`). So the delta
 * a headline may print is the change over a *run of intervals the strip does
 * draw*: the latest such run in the window, which is the run ending at the
 * window's own last point unless that point has just started counting an
 * account. The two ends of a run count the same accounts, so one subtraction of
 * them is a movement of money by this module's own definition, and it is what
 * the bars between them add up to.
 *
 * Most windows are one run and this returns the window itself, which is why a
 * headline keeps the window's own words there. Where a window opens on points
 * that count fewer accounts — the usual shape when the household's history is
 * younger than the window, ADR-0045 — the run is shorter than the window and
 * the words have to name it: the alternative is a card whose headline claims a
 * change the picture under it declines to draw.
 *
 * `[to, to]` is reported when no interval in the window is drawn at all: there
 * is no span to state, and a caller must not invent one. `from`/`to` bound the
 * search to the part of the series on screen, so a brushed window never reports
 * a span outside the brush.
 */
export function measuredSpan(points: Point[], from: number, to: number): [number, number] {
  const changes = changesPerPoint(points);
  const drawn = (i: number) => changes[i]?.kind === "value";
  for (let end = to; end > from; end--) {
    if (!drawn(end)) continue;
    // Inside a run every interval is drawn, so walking back from the end is
    // what finds its first point.
    let start = end;
    while (start > from && drawn(start)) start -= 1;
    return [start, end];
  }
  return [to, to];
}

/**
 * How many accounts some point of the line leaves out because the ledger cannot
 * value them (no rate, no price, no balance) — read from the points' own
 * `missing` reasons, never from the wording of a note. Zero when the only thing
 * to say is that an account starts partway through. It is what decides whether
 * the collapsed coverage notes may stay a quiet title.
 */
export function leftOutAccounts(points: Point[]): number {
  const ids = new Set<string>();
  for (const p of points) for (const m of p.missing.filter(isGap)) ids.add(m.account_id);
  return ids.size;
}

/** The collapsed coverage line: a neutral title, unless the line is short of
 *  accounts — then the summary says so, in the warning tone. */
export function coverageSummary(points: Point[]): { summary: string; tone: "neutral" | "warning" } {
  const n = leftOutAccounts(points);
  return n === 0
    ? { summary: "History coverage", tone: "neutral" }
    : { summary: `Chart leaves out ${n} ${n === 1 ? "account" : "accounts"} at some points`, tone: "warning" };
}

/**
 * The sentences under the chart: which accounts the line cannot fully count and
 * why, and which start partway through. Empty when every point counts everything.
 */
export function coverageNotes(points: Point[]): string[] {
  const gaps = new Map<string, Set<string>>();
  for (const p of points) {
    for (const m of p.missing.filter(isGap)) {
      if (!gaps.has(m.name)) gaps.set(m.name, new Set());
      gaps.get(m.name)!.add(MISSING_REASON[m.reason]);
    }
  }
  const notes: string[] = [];
  if (gaps.size) {
    const parts = [...gaps].map(([name, why]) => `${name} (${[...why].join(", ")})`);
    notes.push(`Some points leave out accounts the ledger cannot value: ${parts.join("; ")}.`);
  }
  const starts: string[] = [];
  points.forEach((p, i) => {
    const names = startsHere(points, i);
    if (names.length) starts.push(`${names.join(", ")} from ${formatDay(p.date)}`);
  });
  if (starts.length) notes.push(`Counted partway through: ${starts.join("; ")}.`);
  return notes;
}

/** ECharts renders a formatter's string as HTML, and an account's name is text a
 *  person or a bank chose. */
const escapeHtml = (text: string) =>
  text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export function tooltipBody(
  p: Point,
  started: string[],
  ccy: string,
  change: ChangeNote | null = null,
): string {
  const lines = [`<strong>${formatDay(p.date)}</strong>`, formatMoney(p.net_worth, ccy)];
  const gaps = p.missing.filter(isGap);
  if (gaps.length) {
    lines.push("Leaves out:");
    for (const m of gaps) {
      lines.push(`&nbsp;&nbsp;${escapeHtml(m.name)} — ${MISSING_REASON[m.reason]}`);
    }
  }
  if (started.length) lines.push(`Starts counting: ${started.map(escapeHtml).join(", ")}`);
  // The strip's own figure, at full precision, in the one place a chart prints
  // one. Where the strip draws no bar, this says which of the two reasons it is
  // — a reader who can see the gap is owed the why, and a reader who cannot is
  // owed it twice.
  if (change?.kind === "value") {
    // The arrow is the half of this line a reader reads without reading it, and
    // an interval where nothing moved is not an arrow up: a change that rounds
    // to nothing is stated as nothing, which is the figure on screen.
    const moved = formatMoney(Math.abs(change.value), ccy);
    lines.push(
      moved === formatMoney(0, ccy)
        ? `No change since ${formatDay(change.since)}`
        : `Change since ${formatDay(change.since)}: ${change.value > 0 ? "▲" : "▼"} ${moved}`,
    );
  } else if (change?.kind === "started") {
    lines.push("No change shown: an account starts or stops being counted here.");
  } else if (change?.kind === "gap") {
    lines.push(
      "No change shown: a point here leaves an account out, so the two are not comparable.",
    );
  }
  return lines.join("<br/>");
}

/**
 * The two kinds of point that carry a meaning of their own, drawn on a line that
 * otherwise has no markers at all.
 *
 * `markPoint` rather than the series' own symbols, because a marker on *every*
 * point is what made this read as a dotted line — and ECharts' `showSymbol`
 * switches all of them at once, with no way to keep two. These are the two that
 * have to survive that (ADR-0045): a point that leaves an account out, and one
 * where an account starts being counted.
 *
 * `circle`, not `emptyCircle`: an empty symbol drops the item's `color` into its
 * *stroke* and fills the shape white, which on a surface-coloured disc is a mark
 * that is drawn and cannot be seen. A `circle` takes `color` as its fill and
 * `borderColor` as its ring, so the mark is the surface with a coloured edge.
 */
function partialMarks(points: Point[], t: ChartTokens) {
  const data = points.flatMap((p, i) => {
    const partial = p.missing.some(isGap);
    const starting = startsHere(points, i).length > 0;
    if (!partial && !starting) return [];
    return [
      {
        // The point's own day, which is what `name` is for in a mark item. It
        // draws nothing — the label is off — but it is what the option *means*.
        name: p.date,
        coord: [dayTs(p.date), Number(p.net_worth)],
        symbol: "circle",
        itemStyle: {
          color: t.surface,
          // The ring is what carries the meaning, and it is legible because the
          // disc under it is the surface the chart is already drawn on.
          borderColor: partial ? t.warning : t.accent,
          borderWidth: 2,
        },
      },
    ];
  });
  return {
    // A fact about the line, not a series to be pointed at: it takes no hover
    // and it is not what the tooltip is reading.
    silent: true,
    symbolSize: 8,
    label: { show: false },
    data,
  };
}

// --- the canvas's own measurements, in px -------------------------------
//
// Every number here is read off the drawn chart, not derived: the strip is 34 px
// because that is the height a bar of two or three buckets' width reads at, and
// the gap under the brush is what keeps the top gridline off the drag target.

/** The margin the plot keeps from the canvas edge. */
const CANVAS_PAD = 8;
/** The brush's band, and the room it needs under it. */
const BRUSH_H = 20;
const BRUSH_GAP = 10;
/** The change strip's band, and the gap between it and the line. */
const STRIP_H = 34;
const STRIP_GAP = 12;
/** The x-axis labels' band, under the strip. Reserved, because the strip's grid
 *  cannot measure it: see `VALUE_GUTTER`. */
const AXIS_BAND = 26;

/**
 * What the last x label needs to the right of the plot, in px.
 *
 * A time axis centres each label on its tick, and the newest point — which the
 * axis always names (`xLabelPoints`) — sits on the plot's right edge. Half of
 * its label therefore hangs outside the canvas, which clips it: the axis read
 * `Sep 2` and lost the day. Both layouts reserve it. The wide one gave up
 * `containLabel` (see `VALUE_GUTTER`), and on the phone `containLabel` measures
 * the value labels' gutter but not this overhang: the card read `Oct 1`.
 *
 * 24 px is half of the widest label this axis can be asked for, `Sep 25` — the
 * `day` granularity's `formatBucket` — measured on the drawn canvas. Every other
 * granularity's label (`Sep`, `Q3`, `2026`) is narrower.
 */
const X_LABEL_ROOM = 24;

/**
 * The value axis's gutter, in px — **reserved, not measured**, and the only
 * place in this app that gives up `containLabel`.
 *
 * §2.9 is right that a measured gutter is better: it spends a phone card's
 * scarce plot area on the labels that are actually there. But `containLabel`
 * sizes each grid's gutter from *its own* labels, and the strip's grid has none
 * to measure — so with it, the bars would start 68 px to the left of the line
 * they are measured against. Two grids that must line up have to be told the
 * same number, and a number is what this is.
 *
 * It is measured all the same: 68 px is where `containLabel` puts this chart's
 * plot on the demo household's own labels (a `$40k` tick, 26 px of glyphs, the
 * 8 px ECharts leaves between a label and the axis, and the canvas's own 8 px),
 * read off the drawn canvas at both widths. The ticks that can be wider are the
 * ones `formatMoneyTick` shortens less far or not at all — `$500.00` under a
 * thousand, `$1234.5k` just over a million — at about 50 px, which still fit.
 * `valueExtent` is what keeps a tick from being longer than those.
 *
 * A **phone card does not take this branch at all** — it draws one grid with a
 * measured gutter, because a second grid would cost it a fifth of its width for
 * a strip it has no room to read. That is `phoneCanvas`'s whole argument: the
 * same chart in two places is not the same chart.
 */
const VALUE_GUTTER = 68;

/** Where `containLabel` puts a phone card's plot, in px from the canvas's left
 *  edge: `CANVAS_PAD`, a `$100k` tick, and the 8 px ECharts leaves beside it. An
 *  estimate — a longer tick (`$1234.5k`) takes some 14 px more, which leaves the
 *  labels a few per cent closer than `LABEL_PITCH` asked for, and still apart. */
const PHONE_GUTTER = 56;

/** The steps a value axis may be ruled in, as multiples of a power of ten: the
 *  ones ECharts' own search rounds to, so a step chosen here is one it keeps. */
const NICE_STEPS = [1, 2, 3, 5, 10];

/** The smallest "nice" step that is at least `raw`. */
function niceStep(raw: number): number {
  const unit = 10 ** Math.floor(Math.log10(raw));
  const found = NICE_STEPS.find((m) => m * unit >= raw * (1 - 1e-9)) ?? 10;
  return found * unit;
}

/**
 * Would ECharts rule a range of `count` steps of `step` *in* that step, asked
 * for `splits` intervals?
 *
 * It is given a range, not an interval, and derives the interval itself: the
 * range over `splits`, rounded to a nice number at the thresholds below (its
 * `nice()`, in `util/number`). When that is not the step the range was built
 * from, the ends stop being multiples of it and it adds a tick at each of them
 * — an uneven rule and an unshortened label. So a range is only offered when
 * the two agree, and not within a hair of a threshold, where a float decides.
 */
function ruledIn(step: number, count: number, splits: number): boolean {
  const raw = (step * count) / splits;
  const unit = 10 ** Math.floor(Math.log10(raw));
  const f = raw / unit;
  if ([1.5, 2.5, 4, 7].some((edge) => Math.abs(f - edge) < 1e-6)) return false;
  const rounded = (f < 1.5 ? 1 : f < 2.5 ? 2 : f < 4 ? 3 : f < 7 ? 5 : 10) * unit;
  return Math.abs(rounded - step) < step * 1e-6;
}

/**
 * The value axis's range for figures running from `lo` to `hi`: `[min, max]`,
 * a whole number of nice steps apart — or `null` to leave it to ECharts, when
 * there is nothing to rule an axis around (no figures, or all of them zero).
 *
 * The axis spans what the window holds rather than the distance from zero to
 * it (see `netWorthOption`), and ECharts' own `scale: true` does most of that.
 * It is not left to it for three reasons, each seen on a drawn chart:
 *
 * - **It follows the figures all the way down.** A week in which the only
 *   movement is forty cents of interest is ruled in dimes and drawn as a climb
 *   the height of the plot. So the step has a floor, and the floor is
 *   *relative* — a twentieth of a thousandth of the largest figure, which makes
 *   the narrowest axis about a fiftieth of a per cent of the balance — because
 *   a fixed one is a guess at the currency: ten is nothing in yen and the whole
 *   axis on a balance of 1.20.
 * - **Its ticks stop shortening.** From a thousand up the step is never under
 *   ten, the finest that two decimals of `k` state exactly (`$100.01k`,
 *   `formatMoneyTick`); a finer one prints `$100,003.10` into a gutter sized for
 *   `$100k` — and a `minInterval` does not prevent it, because ECharts adds a
 *   tick at the data's own minimum when the range is narrower than the step.
 * - **Its rule count wanders.** Its search is a request (`valueTicks`), and on
 *   a range that does not start at zero it answered four with seven. Here the
 *   range is `splits` steps, or one either side of that: five rules, give or
 *   take one.
 *
 * Of the ranges that qualify — both ends on a multiple of the step, the lowest
 * figure in the bottom step, one ECharts will rule in that step (`ruledIn`) —
 * it is the **narrowest**, so the figures fill as much of the plot as round
 * numbers allow.
 *
 * Zero needs no rule of its own. A minimum is a multiple of the step at or
 * under the lowest figure, so it is never below zero unless a figure is, and a
 * range that crosses zero has it on a rule.
 */
export function valueExtent(lo: number, hi: number, splits: number): [number, number] | null {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi < lo) return null;
  const size = Math.max(Math.abs(lo), Math.abs(hi));
  if (size === 0) return null;
  const floor = Math.max(niceStep(size * 0.00005), size >= 1000 ? 10 : 0.01);
  // Snapped to the step's own decimals: `0.1 * 3` is not a number an axis prints.
  const snap = (n: number, step: number) =>
    Number(n.toFixed(Math.max(0, 2 - Math.floor(Math.log10(step)))));

  if (hi === lo) {
    // A flat line sits in the middle of its plot, not along the bottom rule —
    // unless that would put a rule under zero for a balance that never was.
    let min = snap((Math.floor(lo / floor) - Math.floor(splits / 2)) * floor, floor);
    if (lo >= 0) min = Math.max(min, 0);
    return [min, snap(min + splits * floor, floor)];
  }

  let best: [number, number] | null = null;
  let step = Math.max(niceStep((hi - lo) / (splits + 1)), floor);
  // Each step is at least 1.5× the last, so once the fewest steps of this one
  // span more than the best range found, no later one can be narrower.
  for (; !best || step * (splits - 1) < best[1] - best[0]; step = niceStep(step * 1.01)) {
    const min = snap(Math.floor(lo / step) * step, step);
    for (let count = Math.max(2, splits - 1); count <= splits + 1; count++) {
      const max = snap(min + count * step, step);
      if (max < hi || !ruledIn(step, count, splits)) continue;
      if (!best || max - min < best[1] - best[0]) best = [min, max];
      break;
    }
  }
  return best;
}

/** How wide the plot is on a canvas this size, in px — what `xLabelPoints`
 *  shares out between its labels. Exact on the wide layout, whose gutters are
 *  constants; `PHONE_GUTTER`'s estimate on a phone card. */
function plotWidthOf(box: ChartBox): number {
  const left = isWide(box) ? VALUE_GUTTER : PHONE_GUTTER;
  return box.width - left - CANVAS_PAD - X_LABEL_ROOM;
}

/** Canvases at or above `PHONE_MAX` carry the strip and the brush. */
function isWide(box: ChartBox): boolean {
  return !phoneCanvas(box);
}

/**
 * The line's plot height on a canvas that carries a strip: what is left of the
 * box after the brush, the strip and the labels have taken their bands.
 *
 * The value axis is asked for its rule count from *this* height and not the
 * canvas's — the count follows the plot the rules divide, and 60 px of the
 * canvas is no longer plot.
 */
function lineBox(box: ChartBox, brush: boolean): ChartBox {
  const top = CANVAS_PAD + (brush ? BRUSH_H + BRUSH_GAP : 0);
  const stripTop = box.height - CANVAS_PAD - AXIS_BAND - STRIP_H;
  return { width: box.width, height: Math.max(48, stripTop - STRIP_GAP - top) };
}

/**
 * Which points a brushed window covers, or `null` for "the whole series".
 *
 * ECharts hands a `dataZoom` event two percentages of the x *values* — which on
 * a time axis are timestamps, not indices — so they are mapped back onto the
 * points here, where the dates are. A window that covers everything is reported
 * as `null` rather than as its ends, because "the whole range" is what the
 * headline says with the reader's own words ("over 3 months") instead of two
 * dates that only repeat it.
 */
export function brushWindow(points: Point[], start: number, end: number): [number, number] | null {
  const n = points.length;
  if (n < 3) return null;
  const first = dayTs(points[0].date);
  const last = dayTs(points[n - 1].date);
  const span = last - first;
  if (!(span > 0) || !Number.isFinite(start) || !Number.isFinite(end)) return null;

  const at = (pct: number) => {
    const target = first + (span * pct) / 100;
    let best = 0;
    for (let i = 1; i < n; i++) {
      if (Math.abs(dayTs(points[i].date) - target) < Math.abs(dayTs(points[best].date) - target)) {
        best = i;
      }
    }
    return best;
  };

  const from = at(start);
  const to = at(end);
  if (from >= to) return null;
  return from === 0 && to === n - 1 ? null : [from, to];
}

/**
 * What a caller can offer on top of the chart itself.
 *
 * `brush` is opt-in rather than part of the look because the window it draws
 * lives inside the ECharts instance, where only the caller can reach it: the
 * Accounts hero re-scopes its own headline from `brushEvents`, and a card whose
 * figures come from the report instead — the Insights overview, where the delta
 * and the reconciliation are whole-window — would otherwise show a brushed plot
 * beside a total for a different span. Both cards keep the strip and the hover;
 * only the one that can state a window draws one.
 */
/**
 * What a brushed window changes about the axes, as an option to **merge** into
 * the chart that is already drawn: the date labels, chosen again for the points
 * `window` holds (`null` is the whole series).
 *
 * `xLabelPoints` shares the plot's width out between the points it is given,
 * and a brush changes which points those are without the option being built
 * again — the window is ECharts' state (see `Accounts.tsx`). Left alone, the
 * labels chosen for a year stay where they were while the plot zooms into a
 * month of it: one label, or none. The value axis needs no such patch; its
 * range is a function ECharts re-asks (`valueExtent`).
 *
 * The array's first entry is empty on purpose: a merge matches axes by index,
 * and the labelled axis is the second, under the strip.
 */
export function brushedAxes(
  data: NetWorthSeries | undefined,
  box: ChartBox,
  window: [number, number] | null,
) {
  const points = data?.points ?? [];
  const shown = window ? points.slice(window[0], window[1] + 1) : points;
  const customValues = xLabelPoints(shown, data?.granularity ?? "day", plotWidthOf(box));
  return { xAxis: [{}, { axisLabel: { customValues } }] };
}

export interface NetWorthOptions {
  brush?: boolean;
}

export function netWorthOption(
  data: NetWorthSeries | undefined,
  t: ChartTokens,
  box: ChartBox,
  { brush = false }: NetWorthOptions = {},
): EChartsOption {
  const points = data?.points ?? [];
  const ccy = data?.base_currency ?? "USD";
  const byTs = new Map(points.map((p, i) => [dayTs(p.date), i]));
  const changes = changesPerPoint(points);
  const wide = isWide(box);
  // A phone canvas draws no brush whatever the caller asks for; see `VALUE_GUTTER`.
  const band = wide && brush;
  const line = lineBox(box, band);
  const top = CANVAS_PAD + (band ? BRUSH_H + BRUSH_GAP : 0);
  const stripTop = box.height - CANVAS_PAD - AXIS_BAND - STRIP_H;
  // The gutter both grids share, or the measured one on a canvas with a single
  // grid. `left` is the only difference between the two layouts' x geometry.
  const gutter = wide ? VALUE_GUTTER : CANVAS_PAD;
  // ...and the right edge, which keeps the room the newest label hangs into
  // (see `X_LABEL_ROOM`): that label is centred on the plot's last pixel, and
  // `containLabel` does not make room for it on the phone either.
  const right = CANVAS_PAD + X_LABEL_ROOM;

  /** How many steps the value axis is ruled in: what the plot's height can carry. */
  const splits = valueTicks(wide ? line : box);

  /** The value axis: money, and as many rules as the plot can carry. */
  const valueAxis = {
    type: "value" as const,
    // The axis spans the figures the window holds, not the distance from zero to
    // them. A value axis includes zero unless told otherwise, and a household's
    // net worth is a large number that moves by a small part of itself: $97k to
    // $105k on a `$0 … $120k` axis is a ruled line with a wobble in its top
    // tenth, on a chart whose whole question is how it moved. Nothing here is
    // measured from the baseline — the fill under the line is a tint, the strip
    // below draws the changes against its own zero — so the baseline is the
    // thing to give up. A series that reaches or crosses zero still shows it.
    //
    // `min` and `max` are functions because ECharts calls them with the extent
    // of the figures *on screen*: a brush that narrows the window re-rules the
    // axis for what is left in it, with nothing here having to hear about it.
    scale: true,
    //
    // `null` hands a range back to ECharts, which its types leave out.
    min: (seen: { min: number; max: number }) =>
      (valueExtent(seen.min, seen.max, splits)?.[0] ?? null) as number,
    max: (seen: { min: number; max: number }) =>
      (valueExtent(seen.min, seen.max, splits)?.[1] ?? null) as number,
    ...chartAxis(t, {
      grid: true,
      tick: (v: number) => formatMoneyTick(v, ccy),
      splitNumber: splits,
    }),
  };

  // The x axis's labels: §6.6's vocabulary, at the window's own granularity, on
  // the points `xLabelPoints` chose for a plot this wide. The phone's gutter is
  // measured by `containLabel` and so not known here; `PHONE_GUTTER` is what it
  // measures on the labels this axis draws. `hideOverlap` stays as the backstop
  // for a label wider than its pitch allowed for.
  const x = xAxisLabels(data, plotWidthOf(box));
  const xLabel = {
    ...chartAxis(t).axisLabel,
    formatter: x.formatter,
    customValues: x.customValues,
    hideOverlap: true,
    // A time axis hides its first and last labels unless told otherwise — a
    // habit for ticks it invented, and wrong for labels that were chosen.
    showMinLabel: true,
    showMaxLabel: true,
  };

  return {
    // Two grids are two x axes, and the pointer has to cross both or a reader
    // tracing a day sees the line marked and the bar under it not.
    ...(wide ? linkedPointer() : {}),
    // `containLabel`: the gutter is the labels' own width, measured, rather than
    // a constant. `left: 60` was a guess that spends a phone's scarce plot area
    // when the ticks are short (`$10k`) and clips a long one (`1,234,567.89`).
    // The wide layout gives that up for a shared `VALUE_GUTTER` — see its note:
    // a strip's grid has no labels of its own to measure, and the two have to
    // agree about where the plot starts.
    grid: wide
      ? [
          {
            left: gutter,
            right,
            top,
            height: line.height,
            containLabel: false,
          },
          {
            left: gutter,
            right,
            top: stripTop,
            height: STRIP_H,
            containLabel: false,
          },
        ]
      : { top: 16, right, bottom: CANVAS_PAD, left: gutter, containLabel: true },
    ...(band ? { dataZoom: rangeBrush(t, { top: 0, height: BRUSH_H }) } : {}),
    tooltip: chartTooltip(t, {
      formatter: (param: TooltipPoint) => {
        // An axis tooltip hands every series at the pointer; the line is the
        // first of them, and both series carry the same timestamp.
        const first = (Array.isArray(param) ? param[0] : param) as TooltipPoint;
        const i = byTs.get((first.value as [number, number])[0]);
        return i === undefined
          ? ""
          : tooltipBody(points[i], startsHere(points, i), ccy, changes[i]);
      },
      // A time axis's value is a timestamp; the chip names the day.
      pointerLabel: (value) => formatDay(isoDay(new Date(Number(value)))),
    }),
    xAxis: wide
      ? [
          {
            type: "time" as const,
            gridIndex: 0,
            ...chartAxis(t),
            // The dates are stated once, under the strip: a second set of day
            // labels between the plot and the strip would be one axis drawn
            // twice. The pointer still crosses this grid, without its own chip.
            axisLine: { show: false },
            axisLabel: { show: false },
            ...pointerLineOnly(),
          },
          {
            type: "time" as const,
            gridIndex: 1,
            ...chartAxis(t),
            axisLabel: xLabel,
          },
        ]
      : {
          type: "time" as const,
          ...chartAxis(t),
          axisLabel: xLabel,
        },
    // The ticks are money, and the axis says so: `$10k`, `$20k` — these read
    // `10,000`, `20,000` with no currency at all until this. The exact figure is
    // in the tooltip and in the headline the section prints above the chart.
    //
    // `splitNumber` is the canvas's own: this chart is 260 px tall in the
    // accounts card and 280 px in Insights, and ECharts' nice-number search
    // answered the first with eight rules (`$0 … $35k` in 5k steps) — a hatch to
    // read a line through, when four rules measure it just as well.
    yAxis: wide
      ? [
          valueAxis,
          {
            // The strip's own scale, and nothing else: no line, no labels, no
            // rules to repeat the ones above. It is a *second measure*, which is
            // why it gets a second grid rather than a second axis on the first —
            // two y-scales over one plot is a chart nobody can read.
            type: "value" as const,
            gridIndex: 1,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        ]
      : valueAxis,
    series: [
      {
        type: "line",
        // A rounder telling of the same numbers: `smoothMonotone: "x"` builds
        // control points that share their own point's value, so a carried-forward
        // balance is drawn flat and a step cannot overshoot it. Without it,
        // `smooth` invents curvature — and values — between two equal balances.
        smooth: 0.4,
        smoothMonotone: "x",
        // The hover is where a line's markers are. `showSymbol: false` is not
        // "no markers": ECharts draws the point under the pointer, which is the
        // marker the reference look is asking for, on a line that reads as a
        // line rather than as a row of dots.
        showSymbol: false,
        symbol: "circle",
        symbolSize: 8,
        areaStyle: chartArea(t),
        lineStyle: { color: t.accent, width: 2 },
        itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
        emphasis: emphasisLine(t, { area: true }),
        markPoint: partialMarks(points, t),
        data: points.map((p) => [dayTs(p.date), Number(p.net_worth)]),
      },
      ...(wide
        ? [
            {
              name: "Change",
              type: "bar" as const,
              xAxisIndex: 1,
              yAxisIndex: 1,
              // Thin, and never wider than the gap a reader has to see between
               // two buckets: a strip is a rhythm, not a set of slabs.
              barMaxWidth: 12,
              emphasis: emphasisStrip(),
              markLine: zeroRule(t),
              data: points.map((p, i) => {
                const change = changes[i];
                return {
                  value: [dayTs(p.date), change?.kind === "value" ? change.value : null],
                  ...(change?.kind === "value"
                    ? {
                        itemStyle: {
                          color: change.value >= 0 ? t.positive : t.negative,
                          // Rounded at the end the value is at, like every other
                          // bar in the app, and square where it meets its own
                          // zero rule.
                          borderRadius: barEndRadius(change.value >= 0 ? "top" : "bottom"),
                        },
                      }
                    : {}),
                };
              }),
            },
          ]
        : []),
    ],
  };
}
