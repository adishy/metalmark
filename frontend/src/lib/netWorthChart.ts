// The net-worth line, as an ECharts option built from the report and nothing else.
//
// Pure, so the rules that decide what a reader sees — which points are partial,
// what the tooltip says about them, where an account starts being counted — are
// tested without a canvas. Three decisions from session 04's audit live here:
//
// * **A time axis.** Points are the window's first day and then each bucket's
//   last, so they are not evenly spaced; a category axis drew them as if they
//   were, stretching the first interval.
// * **Straight segments.** `smooth` overshoots at a step and draws curvature
//   between two carried-forward balances where nothing moved.
// * **A partial point looks partial** (ADR-0045). A point that cannot count an
//   account — no rate, no price, no balance — is drawn hollow and says which
//   account and why; one where an account starts being counted says so. Colour
//   is never the only signal: the tooltip and the note under the chart say it
//   in words.
import type { EChartsOption } from "echarts";
import type { MissingAccount, MissingReason, NetWorthSeries } from "@/api/types";
import { formatDay, isoDay } from "@/lib/dates";
import { formatMoney, formatMoneyTick } from "@/lib/format";
import {
  chartArea,
  chartAxis,
  chartTooltip,
  emphasisLine,
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

export function tooltipBody(p: Point, started: string[], ccy: string): string {
  const lines = [`<strong>${formatDay(p.date)}</strong>`, formatMoney(p.net_worth, ccy)];
  const gaps = p.missing.filter(isGap);
  if (gaps.length) {
    lines.push("Leaves out:");
    for (const m of gaps) {
      lines.push(`&nbsp;&nbsp;${escapeHtml(m.name)} — ${MISSING_REASON[m.reason]}`);
    }
  }
  if (started.length) lines.push(`Starts counting: ${started.map(escapeHtml).join(", ")}`);
  return lines.join("<br/>");
}

export function netWorthOption(data: NetWorthSeries | undefined, t: ChartTokens): EChartsOption {
  const points = data?.points ?? [];
  const ccy = data?.base_currency ?? "USD";
  const byTs = new Map(points.map((p, i) => [dayTs(p.date), i]));
  return {
    // `containLabel`: the gutter is the labels' own width, measured, rather than
    // a constant. `left: 60` was a guess that spends a phone's scarce plot area
    // when the ticks are short (`$10k`) and clips a long one (`1,234,567.89`).
    grid: { top: 16, right: 8, bottom: 8, left: 8, containLabel: true },
    tooltip: chartTooltip(t, {
      formatter: (param: TooltipPoint) => {
        // An axis tooltip hands every series at the pointer; there is one.
        const first = (Array.isArray(param) ? param[0] : param) as TooltipPoint;
        const i = byTs.get((first.value as [number, number])[0]);
        return i === undefined ? "" : tooltipBody(points[i], startsHere(points, i), ccy);
      },
      // A time axis's value is a timestamp; the chip names the day.
      pointerLabel: (value) => formatDay(isoDay(new Date(Number(value)))),
    }),
    xAxis: {
      type: "time",
      ...chartAxis(t),
      axisLabel: { ...chartAxis(t).axisLabel, hideOverlap: true },
    },
    // The ticks are money, and the axis says so: `$10k`, `$20k` — these read
    // `10,000`, `20,000` with no currency at all until this. The exact figure is
    // in the tooltip and in the headline the section prints above the chart.
    yAxis: {
      type: "value",
      ...chartAxis(t, { grid: true, tick: (v) => formatMoneyTick(v, ccy) }),
    },
    series: [
      {
        type: "line",
        smooth: false,
        // Solid by default — ECharts' own default is hollow, which would leave a
        // partial point nothing to be distinguished by.
        symbol: "circle",
        symbolSize: 8,
        areaStyle: chartArea(t),
        lineStyle: { color: t.accent, width: 2 },
        // The surface ring keeps a marker legible where it sits on the line.
        itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
        emphasis: emphasisLine(t, { area: true }),
        data: points.map((p, i) => {
          const partial = p.missing.some(isGap);
          const starting = startsHere(points, i).length > 0;
          return {
            value: [dayTs(p.date), Number(p.net_worth)],
            ...(partial
              ? {
                  symbol: "emptyCircle",
                  itemStyle: { color: t.warning, borderColor: t.warning, borderWidth: 2 },
                }
              : starting
                ? { symbol: "emptyCircle" }
                : {}),
          };
        }),
      },
    ],
  };
}
