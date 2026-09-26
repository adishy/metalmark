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
import type { MissingAccount, MissingReason, NetWorthSeries } from "@/api/types";
import { formatDay, isoDay } from "@/lib/dates";
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
 * sub-$1,000 ones, which `formatMoneyTick` cannot shorten exactly (`$500.00`,
 * 49 px) and which still fit.
 *
 * A **phone card does not take this branch at all** — it draws one grid with a
 * measured gutter, because a second grid would cost it a fifth of its width for
 * a strip it has no room to read. That is `phoneCanvas`'s whole argument: the
 * same chart in two places is not the same chart.
 */
const VALUE_GUTTER = 68;

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

  /** The value axis: money, and as many rules as the plot can carry. */
  const valueAxis = {
    type: "value" as const,
    ...chartAxis(t, {
      grid: true,
      tick: (v: number) => formatMoneyTick(v, ccy),
      splitNumber: valueTicks(wide ? line : box),
    }),
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
            right: CANVAS_PAD,
            top,
            height: line.height,
            containLabel: false,
          },
          {
            left: gutter,
            right: CANVAS_PAD,
            top: stripTop,
            height: STRIP_H,
            containLabel: false,
          },
        ]
      : { top: 16, right: CANVAS_PAD, bottom: CANVAS_PAD, left: gutter, containLabel: true },
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
            axisLabel: { ...chartAxis(t).axisLabel, hideOverlap: true },
          },
        ]
      : {
          type: "time" as const,
          ...chartAxis(t),
          axisLabel: { ...chartAxis(t).axisLabel, hideOverlap: true },
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
