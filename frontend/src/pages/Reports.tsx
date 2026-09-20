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
import { useCashFlow, useNetWorthSeries, useOwners, useSpending } from "@/api/hooks";
import type { Granularity } from "@/api/types";
import { formatBucket, formatDay } from "@/lib/dates";
import { formatMoney } from "@/lib/format";
import { DEFAULT_PRESET, isUsable, resolvePreset } from "@/lib/reportRange";
import Chart from "@/components/Chart";
import { Day } from "@/components/datetime";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import RangeControl, { useReportRange } from "@/components/RangeControl";
import Reconciliation from "@/components/Reconciliation";
import { useChartTokens } from "@/theme/chartTokens";
import {
  chartArea,
  chartAxis,
  chartLegend,
  chartTooltip,
  emphasisBar,
  emphasisLine,
  emphasisPie,
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

export default function Reports() {
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
  const spending = useSpending(active.start, active.end, ownerFilter);
  const ccy = nw.data?.base_currency ?? "USD";

  // Read from the CSS variables rather than a hardcoded palette, so a theme
  // switch repaints these. `t` is memoised per theme, so it is a stable dep.
  const t = useChartTokens();

  const nwOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 20, right: 16, bottom: 30, left: 60 },
      tooltip: chartTooltip(t),
      xAxis: {
        type: "category",
        data: bucketLabels(nw.data),
        ...chartAxis(t),
      },
      yAxis: { type: "value", ...chartAxis(t, { grid: true }) },
      series: [
        {
          type: "line",
          smooth: true,
          areaStyle: chartArea(t),
          lineStyle: { color: t.accent },
          itemStyle: { color: t.accent },
          emphasis: emphasisLine(t, { area: true }),
          data: nw.data?.points.map((p) => Number(p.net_worth)) ?? [],
        },
      ],
    }),
    [nw.data, t],
  );

  const cashFlowOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 30, right: 16, bottom: 30, left: 60 },
      tooltip: chartTooltip(t),
      legend: chartLegend(t, { top: 0 }),
      xAxis: {
        type: "category",
        data: bucketLabels(cashFlow.data),
        ...chartAxis(t),
      },
      yAxis: { type: "value", ...chartAxis(t, { grid: true }) },
      series: [
        {
          name: "Income",
          type: "bar",
          stack: "cash-flow",
          itemStyle: { color: t.positive },
          emphasis: emphasisBar(t, t.positive),
          data: cashFlow.data?.points.map((p) => Number(p.income)) ?? [],
        },
        {
          name: "Expense",
          type: "bar",
          stack: "cash-flow",
          itemStyle: { color: t.negative },
          emphasis: emphasisBar(t, t.negative),
          // Expenses are summed as positive magnitudes by some backends and as
          // negatives by others; plot them downward either way.
          data: cashFlow.data?.points.map((p) => -Math.abs(Number(p.expense))) ?? [],
        },
        {
          name: "Net",
          type: "line",
          smooth: true,
          lineStyle: { color: t.accent },
          itemStyle: { color: t.accent },
          emphasis: emphasisLine(t),
          data: cashFlow.data?.points.map((p) => Number(p.net)) ?? [],
        },
      ],
    }),
    [cashFlow.data, t],
  );

  const donutOption: EChartsOption = useMemo(
    () => ({
      tooltip: chartTooltip(t, { trigger: "item", formatter: "{b}: {c} ({d}%)" }),
      legend: chartLegend(t, { bottom: 0, type: "scroll" }),
      color: t.series,
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          // The gap between slices is the card behind them, not a fixed navy.
          itemStyle: { borderColor: t.surface, borderWidth: 2 },
          label: { color: t.label },
          emphasis: emphasisPie(t),
          data: (spending.data?.rows ?? []).map((r) => ({
            name: r.category_name,
            value: Number(r.total),
          })),
        },
      ],
    }),
    [spending.data, t],
  );

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

  // Said inside each section rather than once above them: a reader who scrolls
  // to the spending donut should not have to scroll back up to find out why
  // there is nothing in it. The control says *what* is wrong; this says what
  // that means for the charts.
  const notDrawn = (
    <p className="text-sm text-fg-muted">Not drawn — this range ends before it starts.</p>
  );

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-medium">Reports</h1>

      <div className="space-y-4 rounded-card bg-surface-raised p-4">
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
        <p className="text-xs text-fg-muted" data-testid="report-window">
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

      <section className="rounded-card bg-surface-raised p-6" data-testid="report-spending">
        <p className="mb-2 text-sm text-fg-muted">Spending by category</p>
        {blocked ? (
          notDrawn
        ) : (
          <>
            {hasSpending ? (
              <Chart option={donutOption} label={donutLabel} testid="spending-donut" />
            ) : (
              <p className="text-sm text-fg-muted">No spending in range.</p>
            )}
            <ul className="mt-3 space-y-1">
              {spending.data?.rows.map((r) => (
                <li key={r.category_id ?? "none"} className="flex justify-between text-sm">
                  <span>{r.category_name}</span>
                  <span>{formatMoney(r.total, ccy)}</span>
                </li>
              ))}
            </ul>
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
