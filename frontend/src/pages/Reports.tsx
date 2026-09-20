// Reporting: net-worth line (with the currency-revaluation figure surfaced),
// income-vs-expense cash flow and a spending-by-category donut (ECharts). Every
// chart is scoped by the same owner filter.
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { useCashFlow, useNetWorthSeries, useOwners, useSpending } from "@/api/hooks";
import { formatDay, formatMonth, isoDay } from "@/lib/dates";
import { formatMoney } from "@/lib/format";
import Chart from "@/components/Chart";
import OwnerFilterChips from "@/components/OwnerFilterChips";
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

function yearRange(): { start: string; end: string } {
  const year = new Date().getFullYear();
  return { start: isoDay(new Date(year, 0, 1)), end: isoDay(new Date(year, 11, 31)) };
}

export default function Reports() {
  const { start, end } = useMemo(yearRange, []);
  const owners = useOwners();
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const nw = useNetWorthSeries(start, end, ownerFilter);
  const cashFlow = useCashFlow(start, end, ownerFilter);
  const spending = useSpending(start, end, ownerFilter);
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
        // `medium` ("Jan 31"), not the raw `2026-01-31` the API sends: the axis
        // label and the pointer chip both render this string, and decision H
        // says the ISO form is never the text a person reads. The day is what
        // varies along this axis and the series is one year, so the year is the
        // part worth dropping — ECharts hides whatever still overlaps.
        data: nw.data?.points.map((p) => formatDay(p.date, "medium")) ?? [],
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
        // `2026-01` → "Jan". Short because twelve labels have to fit a phone
        // (§5), and the chart is scoped to one year, so the year is the part the
        // reader already knows.
        data: cashFlow.data?.points.map((p) => formatMonth(p.month)) ?? [],
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

  // Accessible names state the finding, not the chart type: a screen-reader user
  // cannot see the shape being described, so "line chart" would say nothing.
  // These complement the `<ul>` under the donut, which is the real text
  // equivalent — a chart is never the only way to read a value (§2.9).
  const nwLabel = nw.data
    ? `Net worth changed by ${formatMoney(nw.data.delta_net_worth, ccy)} year to date, ` +
      `from cash flow of ${formatMoney(nw.data.net_cash_flow, ccy)} and currency revaluation ` +
      `of ${formatMoney(nw.data.currency_revaluation, ccy)}.`
    : "Net worth over time.";

  const cfPoints = cashFlow.data?.points ?? [];
  const cfLabel = cfPoints.length
    ? `Income and expenses across ${cfPoints.length} months, netting to ` +
      `${formatMoney(cfPoints.reduce((a, p) => a + Number(p.net), 0), ccy)}.`
    : "Income and expenses by month.";

  const spendRows = spending.data?.rows ?? [];
  const spendTotal = spendRows.reduce((a, r) => a + Number(r.total), 0);
  const top = spendRows.reduce<(typeof spendRows)[number] | null>(
    (best, r) => (best === null || Number(r.total) > Number(best.total) ? r : best),
    null,
  );
  const donutLabel = top
    ? `Spending by category, ${formatMoney(spendTotal, ccy)} across ${spendRows.length} ` +
      `categories. Largest: ${top.category_name} at ${formatMoney(top.total, ccy)}.`
    : "Spending by category.";

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-medium">Reports</h1>

      <div className="rounded-card bg-surface-raised p-4">
        <OwnerFilterChips
          owners={owners.data ?? []}
          value={ownerFilter}
          onChange={setOwnerFilter}
        />
      </div>

      <section className="rounded-card bg-surface-raised p-6" data-testid="report-net-worth">
        <p className="text-sm text-fg-muted">Net worth change (YTD)</p>
        {nw.data && (
          <>
            <p className="text-2xl font-semibold">{formatMoney(nw.data.delta_net_worth, ccy)}</p>
            {/* The identity behind that figure (ADR-0032). The headline stays the
                delta; this is what says whether to believe it. */}
            <Reconciliation series={nw.data} />
          </>
        )}
        {hasSeries && <Chart option={nwOption} label={nwLabel} testid="net-worth-chart" />}
        {ownerFilter && nw.data && (
          <p className="mt-2 text-xs text-fg-muted" data-testid="net-worth-attribution">
            Attribution: {nw.data.attribution} — ownership is held by whole accounts, so this series
            sums the accounts assigned to this owner rather than the transactions posted to them.
          </p>
        )}
      </section>

      <section className="rounded-card bg-surface-raised p-6" data-testid="report-cash-flow">
        <p className="mb-2 text-sm text-fg-muted">Income vs expense (YTD)</p>
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
      </section>

      <section className="rounded-card bg-surface-raised p-6" data-testid="report-spending">
        <p className="mb-2 text-sm text-fg-muted">Spending by category (YTD)</p>
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
            Attribution: {spending.data.attribution} — this counts entries, not accounts, so it does
            not add up with the net worth figures above for the same owner.
          </p>
        )}
      </section>
    </div>
  );
}
