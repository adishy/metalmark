// Reporting: net-worth line (with the currency-revaluation figure surfaced),
// income-vs-expense cash flow and a spending-by-category donut (ECharts). Every
// chart is scoped by the same owner filter and cut to the same window.
//
// The window is the page's one piece of linkable state and lives in the URL
// (`RangeControl`); the owner filter is a lens for right now and lives in
// `useState`. Both are read by all three reports, so neither can be true of one
// chart and false of the next.
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import {
  useCashFlow,
  useCashFlowSankey,
  useNetWorthSeries,
  useOwners,
  useSpending,
} from "@/api/hooks";
import type { Granularity } from "@/api/types";
import { formatBucket, formatDay } from "@/lib/dates";
import { formatMoney, formatMoneyTick } from "@/lib/format";
import { coverageNotes, netWorthOption } from "@/lib/netWorthChart";
import { DEFAULT_PRESET, isUsable, resolvePreset } from "@/lib/reportRange";
import {
  LEFTOVER_NODE,
  SAVINGS_NODE,
  WINDOW_NODE,
  buildSankey,
  sideRows,
  type SankeyNode,
} from "@/lib/sankey";
import Chart from "@/components/Chart";
import { Day } from "@/components/datetime";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import RangeControl, { useReportRange } from "@/components/RangeControl";
import Reconciliation from "@/components/Reconciliation";
import { useChartTokens } from "@/theme/chartTokens";
import {
  barEndRadius,
  chartAxis,
  chartLegend,
  chartTooltip,
  emphasisBar,
  emphasisLine,
  emphasisPie,
  emphasisSankey,
  zeroRule,
  type TooltipEdge,
  type TooltipPoint,
} from "@/theme/chartInteraction";

/**
 * Axis labels for a series, read from the payload's own window.
 *
 * The granularity comes off the **response**, never from a control here: `auto`
 * was resolved by the server, and a chart that re-derived it would be a second
 * implementation of the same rule, free to disagree with the bars it was handed.
 * Every bucket label goes through `formatBucket`, which is the one place that
 * decides what a granularity looks like — the axis and the pointer chip render
 * the same string, and decision H says the ISO form is never the text a person
 * reads.
 */
function bucketLabels(
  data: { points: { date: string }[]; granularity: Granularity } | undefined,
): string[] {
  return data?.points.map((p) => formatBucket(p.date, data.granularity)) ?? [];
}

/**
 * How big the donut's centre figure is set.
 *
 * The hole's width is a fact of the canvas — `radius: 45%` of a 310 × 280 phone
 * chart leaves about 126 px across — and an amount's width is a fact of the
 * figure: "$5,063.70" is nine characters, "$1,234,567.89" is thirteen and would
 * run over the ring. §6.5 says never round a figure to make it fit, and the
 * alternative it names is to shrink the type, so the *size* is chosen from the
 * string rather than the string being cut short. `text-base` is the floor for an
 * amount (§2.4), and the returned names are literals so Tailwind keeps them.
 */
function centreSize(amount: string): string {
  if (amount.length <= 9) return "text-xl";
  if (amount.length <= 13) return "text-lg";
  return "text-base";
}

export default function Overview() {
  const range = useReportRange();
  const owners = useOwners();
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);

  // While the reader's custom range is inverted, the three GETs run against the
  // default window rather than firing a request the server answers with a 422.
  // That window is already in the cache — it is where the reader started — so
  // nothing is fetched, and every section below renders nothing while `blocked`,
  // so the cached default is never shown as if it were the window in the URL.
  const blocked = !isUsable({ start: range.start, end: range.end });
  const fallbackWindow = useMemo(() => resolvePreset(DEFAULT_PRESET), []);
  const active = blocked ? fallbackWindow : { start: range.start, end: range.end };

  const nw = useNetWorthSeries(active.start, active.end, ownerFilter, range.granularity);
  const cashFlow = useCashFlow(active.start, active.end, ownerFilter, range.granularity);
  const sankey = useCashFlowSankey(active.start, active.end, ownerFilter);
  const spending = useSpending(active.start, active.end, ownerFilter);
  const ccy = nw.data?.base_currency ?? "USD";

  // Read from the CSS variables rather than a hardcoded palette, so a theme
  // switch repaints these. `t` is memoised per theme, so it is a stable dep.
  const t = useChartTokens();

  // Built in `lib/netWorthChart`: a time axis, straight segments, and partial
  // points drawn and named as partial (ADR-0045).
  const nwOption: EChartsOption = useMemo(() => netWorthOption(nw.data, t), [nw.data, t]);
  const nwNotes = useMemo(() => coverageNotes(nw.data?.points ?? []), [nw.data]);

  const cashFlowOption: EChartsOption = useMemo(
    () => ({
      // `top` clears the legend row. `containLabel` is the phone's share: the
      // tick gutter is measured from the labels rather than reserved as 60 px of
      // a 310 px-wide canvas.
      grid: { top: 30, right: 8, bottom: 8, left: 8, containLabel: true },
      tooltip: chartTooltip(t),
      legend: chartLegend(t, { top: 0 }),
      xAxis: {
        type: "category",
        data: bucketLabels(cashFlow.data),
        ...chartAxis(t),
      },
      // Money ticks: `$5k` in a gutter that used to read `5,000` with no
      // currency anywhere on the axis.
      yAxis: {
        type: "value",
        ...chartAxis(t, { grid: true, tick: (v) => formatMoneyTick(v, ccy) }),
      },
      series: [
        {
          name: "Income",
          type: "bar",
          stack: "cash-flow",
          // Round the end the value is at, and state the baseline the two halves
          // are measured from — a chart that grows both ways has to say where
          // zero is rather than leaving it to whichever gridline the axis chose.
          itemStyle: { color: t.positive, borderRadius: barEndRadius("top") },
          emphasis: emphasisBar(t, t.positive),
          markLine: zeroRule(t),
          data: cashFlow.data?.points.map((p) => Number(p.income)) ?? [],
        },
        {
          name: "Expense",
          type: "bar",
          stack: "cash-flow",
          itemStyle: { color: t.negative, borderRadius: barEndRadius("bottom") },
          emphasis: emphasisBar(t, t.negative),
          // Expenses are summed as positive magnitudes by some backends and as
          // negatives by others; plot them downward either way.
          data: cashFlow.data?.points.map((p) => -Math.abs(Number(p.expense))) ?? [],
        },
        {
          name: "Net",
          type: "line",
          // Straight, as on the net-worth chart: a smoothed line overshoots
          // between bars and draws values no bucket had.
          smooth: false,
          lineStyle: { color: t.accent },
          itemStyle: { color: t.accent },
          emphasis: emphasisLine(t),
          data: cashFlow.data?.points.map((p) => Number(p.net)) ?? [],
        },
      ],
    }),
    [cashFlow.data, t, ccy],
  );

  const donutOption: EChartsOption = useMemo(() => {
    const rows = spending.data?.rows ?? [];
    const total = rows.reduce((sum, r) => sum + Number(r.total), 0);
    // Slices are named by the row's **key**, not by the words a reader sees, for
    // the reason the graph gives: ECharts merges data items that share a name, so
    // two rows with the same label would become one slice with their totals added
    // — a chart that balances against a legend that is wrong. Two categories may
    // share a name, and a household may name a category "Investment fees" while
    // also paying real ones. The names live in this map instead, and both the
    // legend and the tooltip read them back out.
    const nameOf = (key: unknown) =>
      rows.find((r) => r.key === key)?.category_name ?? String(key);
    return {
      tooltip: chartTooltip(t, {
        trigger: "item",
        formatter: (p: TooltipPoint) => {
          const value = Number(p.value);
          const share = total === 0 ? 0 : (value / total) * 100;
          // `share` replaces ECharts' `{d}`, which is only available to the
          // template-string form — and the template cannot map a key to a name.
          return `${nameOf(p.name)}: ${formatMoney(value, ccy)} (${share.toFixed(0)}%)`;
        },
      }),
      legend: chartLegend(t, { bottom: 0, type: "scroll", formatter: nameOf }),
      color: t.series,
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          // The gap between slices is the card behind them, not a fixed navy.
          itemStyle: { borderColor: t.surface, borderWidth: 2 },
          // Named everywhere the reader looks, keyed everywhere ECharts looks.
          label: { color: t.label, formatter: (p: TooltipPoint) => nameOf(p.name) },
          emphasis: emphasisPie(t),
          data: rows.map((r) => ({ name: r.key, value: Number(r.total) })),
        },
      ],
    };
  }, [spending.data, t, ccy]);

  // The graph, built here and nowhere else: the payload carries rows and this is
  // the one place the picture's shape is decided, so a layout bug and a data bug
  // can never be confused for one another.
  const graph = useMemo(
    () => (sankey.data ? buildSankey(sankey.data) : null),
    [sankey.data],
  );

  const sankeyOption: EChartsOption = useMemo(() => {
    if (graph === null) return {};

    // Colour by *side*, not by position in the palette. A Sankey is read as a
    // direction of travel, so the two sides have to be told apart at a glance and
    // must not change colour when the window changes — which a cycling palette
    // would do, since a node's index moves as rows are added and removed. The
    // three nodes that are not a category at all (the window itself and the two
    // residual nodes) take the accent, which reads as "the household's own money"
    // rather than as one more source or destination.
    const color = (node: SankeyNode) =>
      node.name === WINDOW_NODE || node.name === SAVINGS_NODE || node.name === LEFTOVER_NODE
        ? t.accent
        : node.depth === 0
          ? t.positive
          : t.negative;

    return {
      tooltip: chartTooltip(t, {
        trigger: "item",
        // Node captions and the tooltip both need *words* where the graph stores
        // an id: an id has to be unique across the picture and a label does not,
        // so they are different strings and only one of them is readable.
        formatter: (p: TooltipPoint) => {
          const label = (id: unknown) => graph.labels.get(String(id)) ?? String(id);
          // The pointer can be on a node or on a ribbon, and ECharts types the
          // item as either. A link is the case with endpoints — its own `name` is
          // empty, and what it means is the two nodes it joins.
          const edge = p.data as TooltipEdge | null | undefined;
          if (edge?.source !== undefined && edge.target !== undefined) {
            return `${label(edge.source)} → ${label(edge.target)}: ${formatMoney(
              Number(edge.value),
              ccy,
            )}`;
          }
          return `${label(p.name)}: ${formatMoney(Number(p.value), ccy)}`;
        },
      }),
      series: [
        {
          type: "sankey",
          // Not the usual hairline inset: these margins are where the node labels
          // live. Each column's words are placed *outward* from its node — sources
          // to the left, destinations to the right — so a label sits beside the
          // graph instead of on top of a ribbon, which is where ECharts puts it by
          // default. The middle node's goes above it, having no margin of its own.
          left: 88,
          right: 104,
          // Deep enough for the middle column's label, which ECharts draws *above*
          // its node: at a hairline inset the word is cut in half by the canvas
          // edge, which reads as a rendering fault rather than as a crop.
          top: 26,
          bottom: 8,
          // Wide enough that a ribbon reads as a flow rather than as a hairline.
          nodeWidth: 14,
          nodeGap: 10,
          data: graph.nodes.map((n) => ({
            name: n.name,
            depth: n.depth,
            itemStyle: { color: color(n) },
            label: { position: n.depth === 0 ? "left" : n.depth === 2 ? "right" : "top" },
          })),
          links: graph.links,
          label: {
            color: t.label,
            // Bounded so a long name cannot run back over the graph or off the
            // canvas — a household names its own categories, and one can be longer
            // than any margin. Truncation is the honest failure: the alternative is
            // a clipped word or a label over the ribbons, and the list under the
            // chart carries every name in full either way.
            width: 84,
            overflow: "truncate",
            formatter: (p: TooltipPoint) => graph.labels.get(String(p.name)) ?? "",
          },
          // `source` is not decoration: a ribbon is tinted by where it came from,
          // which is what makes "this came out of Salary" readable without a
          // legend. `curveness` keeps two flows from a shared origin apart.
          lineStyle: { color: "source", opacity: 0.4, curveness: 0.5 },
          emphasis: emphasisSankey(t),
        },
      ],
    };
    // `graph.labels` is rebuilt with `graph`, so the two are one dependency, and
    // `color` closes over `t` rather than being a dependency of its own.
  }, [graph, t, ccy]);

  const hasSpending = (spending.data?.rows.length ?? 0) > 0;
  const hasSeries = (nw.data?.points.length ?? 0) > 0;
  const hasCashFlow = (cashFlow.data?.points.length ?? 0) > 0;

  // The window the server actually used, which is the *response's* for one
  // reason: "All time" sends no `start`, and only the server knows what day the
  // household's data begins. Printing what was asked for would print a blank
  // where the answer is.
  const shown = nw.data
    ? { start: nw.data.start, end: nw.data.end }
    : { start: active.start, end: active.end };
  const shownText =
    shown.start === null
      ? `the beginning through ${formatDay(shown.end)}`
      : `${formatDay(shown.start)} to ${formatDay(shown.end)}`;

  // Accessible names state the finding, not the chart type: a screen-reader user
  // cannot see the shape being described, so "line chart" would say nothing.
  // These complement the `<ul>` under the donut, which is the real text
  // equivalent — a chart is never the only way to read a value (§2.9).
  //
  // Each one names its own window, because the window is no longer a constant of
  // the page: "year to date" was hardcoded prose that a range control makes
  // false, and a chart is not allowed to announce a period it is not showing.
  const nwLabel = nw.data
    ? `Net worth changed by ${formatMoney(nw.data.delta_net_worth, ccy)}, ${shownText}, ` +
      `from cash flow of ${formatMoney(nw.data.net_cash_flow, ccy)} and currency revaluation ` +
      `of ${formatMoney(nw.data.currency_revaluation, ccy)}.`
    : `Net worth over time, ${shownText}.`;

  const cfPoints = cashFlow.data?.points ?? [];
  // The unit is read off the same response the bars came from, so a quarter
  // chart cannot announce itself as months.
  const cfUnit = cashFlow.data?.granularity ?? "month";
  const cfLabel = cfPoints.length
    ? `${cfPoints.length} ${cfUnit}${cfPoints.length === 1 ? "" : "s"}, ` +
      `${shownText}, netting to ${formatMoney(cfPoints.reduce((a, p) => a + Number(p.net), 0), ccy)}.`
    : `Income and expenses by ${cfUnit}, ${shownText}.`;

  const spendRows = spending.data?.rows ?? [];
  const spendTotal = spendRows.reduce((a, r) => a + Number(r.total), 0);
  const top = spendRows.reduce<(typeof spendRows)[number] | null>(
    (best, r) => (best === null || Number(r.total) > Number(best.total) ? r : best),
    null,
  );
  const donutLabel = top
    ? `Spending by category, ${formatMoney(spendTotal, ccy)} across ${spendRows.length} ` +
      `categories, ${shownText}. Largest: ${top.category_name} at ${formatMoney(top.total, ccy)}.`
    : `Spending by category, ${shownText}.`;

  // The graph read as words, for the reason §2.9 gives: canvas is invisible, and
  // a picture of where money went is exactly the kind of thing a reader has to be
  // able to *read*. Built from the graph rather than from the payload, so the two
  // residual rows — which exist only in the graph — cannot go missing from the
  // list beside it.
  const sankeyIn = graph ? sideRows(graph, "in") : [];
  const sankeyOut = graph ? sideRows(graph, "out") : [];
  const sankeyLabel = graph
    ? `${formatMoney(sankey.data?.total_income ?? 0, ccy)} in and ` +
      `${formatMoney(sankey.data?.total_expense ?? 0, ccy)} out, ${shownText}, ` +
      `netting ${formatMoney(sankey.data?.net ?? 0, ccy)}. ` +
      `In: ${sankeyIn.map((r) => `${r.label} ${formatMoney(r.total, ccy)}`).join(", ")}. ` +
      `Out: ${sankeyOut.map((r) => `${r.label} ${formatMoney(r.total, ccy)}`).join(", ")}.`
    : `Where the money went, ${shownText}.`;

  // Said inside each section rather than once above them: a reader who scrolls
  // to the spending donut should not have to scroll back up to find out why
  // there is nothing in it. The control says *what* is wrong; this says what
  // that means for the charts.
  const notDrawn = (
    <p className="text-sm text-fg-muted">Not drawn — this range ends before it starts.</p>
  );

  return (
    // No `mx-auto max-w-6xl` of its own any more — that's `Insights.tsx` now
    // (§9.1 gives this content the middle width because it's a two-up grid at
    // `lg:`, and a 1280 px pair of charts is two 620 px charts; the second
    // column isn't worth the stretch), and the heading above the tabs is the
    // page's one `<h1>`, so this tab doesn't carry its own.
    //
    // §9.3's two-up, and the grid is the page rather than a wrapper around two
    // of its children, so a section's place is decided by one class on it and
    // nothing has to know how many rows came before. `space-y-6` is the phone
    // stack; at `lg:` the gap does the same job on both axes, which is why the
    // vertical step is turned off rather than left to double up with it.
    <div
      className="space-y-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0"
      data-testid="insights-overview-page"
    >
      <div className="space-y-4 rounded-card bg-surface-raised p-4 lg:col-span-2">
        <RangeControl state={range} />
        <OwnerFilterChips
          owners={owners.data ?? []}
          value={ownerFilter}
          onChange={setOwnerFilter}
        />
      </div>

      {/* The resolved window, spelled out. A preset names a period ("This
          quarter") but not which days it landed on, and after the turn of a
          month a reader looking at a stored link needs to know both. The
          granularity is the *echoed* one, so it also answers what `Auto` chose. */}
      {!blocked && (
        <p className="text-xs text-fg-muted lg:col-span-2" data-testid="report-window">
          {shown.start === null ? (
            <>From the beginning through <Day value={shown.end} /></>
          ) : (
            <>
              <Day value={shown.start} /> – <Day value={shown.end} />
            </>
          )}
          {nw.data ? <> · {nw.data.granularity}</> : null}
        </p>
      )}

      {/* The pair. These two carry no column classes at all, and that is the
          point: they are the charts that survive half a page, so they take the
          next two free cells and land side by side without anything being
          counted. Adding a section above them re-pairs them for free. */}
      <section className="rounded-card bg-surface-raised p-6" data-testid="report-net-worth">
        <p className="text-sm text-fg-muted">Net worth change</p>
        {blocked ? (
          notDrawn
        ) : (
          <>
            {nw.data && (
              <>
                <p className="text-2xl font-semibold">{formatMoney(nw.data.delta_net_worth, ccy)}</p>
                {/* The identity behind that figure (ADR-0032). The headline stays
                    the delta; this is what says whether to believe it. */}
                <Reconciliation series={nw.data} />
              </>
            )}
            {hasSeries && <Chart option={nwOption} label={nwLabel} testid="net-worth-chart" />}
            {hasSeries && nwNotes.length > 0 && (
              <div className="mt-2 space-y-1 text-xs text-fg-muted" data-testid="net-worth-coverage">
                {nwNotes.map((note) => (
                  <p key={note}>
                    <span aria-hidden="true">⚠ </span>
                    {note}
                  </p>
                ))}
              </div>
            )}
            {ownerFilter && nw.data && (
              <p className="mt-2 text-xs text-fg-muted" data-testid="net-worth-attribution">
                Attribution: {nw.data.attribution} — ownership is held by whole accounts, so this
                series sums the accounts assigned to this owner rather than the transactions posted
                to them.
              </p>
            )}
          </>
        )}
      </section>

      <section className="rounded-card bg-surface-raised p-6" data-testid="report-cash-flow">
        <p className="mb-2 text-sm text-fg-muted">Income vs expense</p>
        {blocked ? (
          notDrawn
        ) : (
          <>
            {hasCashFlow ? (
              <Chart option={cashFlowOption} label={cfLabel} testid="cash-flow-chart" />
            ) : (
              <p className="text-sm text-fg-muted">No cash flow in range.</p>
            )}
            {ownerFilter && cashFlow.data && (
              <p className="mt-2 text-xs text-fg-muted" data-testid="cash-flow-attribution">
                Attribution: {cashFlow.data.attribution} — each entry counts under the owner it is
                assigned to, so this reads the household's postings rather than one owner's accounts.
              </p>
            )}
          </>
        )}
      </section>

      {/* Full width, per §9.3 and for a reason the section itself states: a
          flow diagram read in a 560 px column stops being a flow diagram. Its
          labels are category names on both sides of a band that has to stay
          wide enough to see, so squeezing it does not shrink the chart, it
          removes the thing the chart is for. */}
      <section
        className="rounded-card bg-surface-raised p-6 lg:col-span-2"
        data-testid="report-sankey"
      >
        <p className="mb-2 text-sm text-fg-muted">Where the money went</p>
        {blocked ? (
          notDrawn
        ) : (
          <>
            {graph ? (
              <>
                <Chart
                  option={sankeyOption}
                  label={sankeyLabel}
                  height={360}
                  testid="cash-flow-sankey"
                />
                {/* The text equivalent. Two lists, because the graph is two
                    sides — and the residual row in each is the one that says
                    whether the window was funded by what it earned. */}
                <div className="mt-3 grid gap-4 sm:grid-cols-2">
                  {(
                    [
                      ["In", sankeyIn],
                      ["Out", sankeyOut],
                    ] as const
                  ).map(([side, rows]) => (
                    <div key={side}>
                      {/* The heading is the *side's* total, not the payload's
                          income or expense — and the difference is the residual
                          row. The "Out" list carries "Left over", so heading it
                          with `total_expense` would put a figure above a column
                          that adds up to something else. Both headings are the
                          graph's own throughput, which is what the two sides have
                          in common and the reason there is a picture at all. */}
                      <p className="text-xs font-medium text-fg-muted">
                        {side} — {formatMoney(graph.flow, ccy)}
                      </p>
                      <ul className="mt-1 space-y-1">
                        {rows.map((r) => (
                          <li key={r.key} className="flex justify-between gap-4 text-sm">
                            <span>{r.label}</span>
                            <span>{formatMoney(r.total, ccy)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-sm text-fg-muted">No cash flow in range.</p>
            )}
            {/* Last, and only when there is something to say: a flow dropped for
                want of a rate is a missing branch of a picture that claims to be
                the whole picture, so it is not a footnote on this chart. */}
            {(sankey.data?.warnings.length ?? 0) > 0 && (
              <div className="mt-3 rounded-control bg-warning/10 p-3" data-testid="sankey-warnings">
                <p className="text-xs font-medium text-warning">
                  Some of these flows are missing data
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
                  {sankey.data?.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            {ownerFilter && sankey.data && (
              <p className="mt-2 text-xs text-fg-muted" data-testid="sankey-attribution">
                Attribution: {sankey.data.attribution} — the same entries the income-vs-expense
                trend above counts, so the two are the same money arranged two ways.
              </p>
            )}
          </>
        )}
      </section>

      {/* Also full width, though §9.3 only names the Sankey. The reason is the
          same one, one level down: this section is not one chart but a donut
          *and* its legend as a list of categories with totals beside it. Two
          columns would put a 560 px donut above a list of ~20 rows each
          separated by a single `justify-between`, which at that width reads as
          two ragged columns of text with a lot of white between them. */}
      <section
        className="rounded-card bg-surface-raised p-6 lg:col-span-2"
        data-testid="report-spending"
      >
        <p className="mb-2 text-sm text-fg-muted">Spending by category</p>
        {blocked ? (
          notDrawn
        ) : (
          <>
            {hasSpending ? (
              <div className="relative">
                <Chart option={donutOption} label={donutLabel} testid="spending-donut" />
                {/* The hole in the ring is the one part of the chart carrying
                    nothing, and the total is the figure the picture is about.
                    DOM text over the canvas rather than a canvas `graphic`
                    (§2.9): it is real text for a screen reader, it is selectable,
                    and it takes its colour from the tokens — so a theme flip
                    repaints it with no second palette to keep in step.
                    `top-[45%]` is the pie's own `center` y, so the two cannot
                    drift apart; `pointer-events-none` keeps it out of the
                    chart's own tap handling. */}
                <div
                  className="pointer-events-none absolute left-1/2 top-[45%] -translate-x-1/2 -translate-y-1/2 text-center"
                  data-testid="spending-total"
                >
                  <p className="text-sm text-fg-muted">Total</p>
                  <p className={`font-semibold tabular-nums ${centreSize(formatMoney(spendTotal, ccy))}`}>
                    {formatMoney(spendTotal, ccy)}
                  </p>
                </div>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">No spending in range.</p>
            )}
            <ul className="mt-3 space-y-1">
              {spending.data?.rows.map((r) => (
                // Keyed on the row's identity, not its category: investment fees
                // and unfiled rows both have no category, and keying on that would
                // give two rows one key — one of them silently dropped from the
                // list while the total still counted it.
                <li key={r.key} className="flex justify-between text-sm">
                  <span>{r.category_name}</span>
                  <span>{formatMoney(r.total, ccy)}</span>
                </li>
              ))}
            </ul>
            {/* Same block as the graph's, for the same reason: this report counts
                investment events now, so it can have dropped one, and a fee the
                reader paid is not a footnote. */}
            {(spending.data?.warnings.length ?? 0) > 0 && (
              <div className="mt-3 rounded-control bg-warning/10 p-3" data-testid="spending-warnings">
                <p className="text-xs font-medium text-warning">
                  Some of this spending is missing data
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
                  {spending.data?.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            {ownerFilter && spending.data && (
              <p className="mt-2 text-xs text-fg-muted" data-testid="spending-attribution">
                Attribution: {spending.data.attribution} — this counts entries, not accounts, so it
                does not add up with the net worth figures above for the same owner.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
