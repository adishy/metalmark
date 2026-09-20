// Reporting: net-worth line (with the currency-revaluation figure surfaced),
// income-vs-expense cash flow and a spending-by-category donut (ECharts). Every
// chart is scoped by the same owner filter.
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { useCashFlow, useNetWorthSeries, useOwners, useSpending } from "@/api/hooks";
import { formatMoney } from "@/lib/format";
import Chart from "@/components/Chart";
import OwnerFilterChips from "@/components/OwnerFilterChips";

function yearRange(): { start: string; end: string } {
  const now = new Date();
  const start = new Date(now.getFullYear(), 0, 1);
  const end = new Date(now.getFullYear(), 11, 31);
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
}

const DONUT_COLORS = [
  "#14b8a6", "#38bdf8", "#818cf8", "#f472b6", "#fbbf24",
  "#34d399", "#f87171", "#a78bfa", "#60a5fa", "#fb923c",
];

const INCOME_COLOR = "#34d399";
const EXPENSE_COLOR = "#f87171";

export default function Reports() {
  const { start, end } = useMemo(yearRange, []);
  const owners = useOwners();
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const nw = useNetWorthSeries(start, end, ownerFilter);
  const cashFlow = useCashFlow(start, end, ownerFilter);
  const spending = useSpending(start, end, ownerFilter);
  const ccy = nw.data?.base_currency ?? "USD";

  const nwOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 20, right: 16, bottom: 30, left: 60 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: nw.data?.points.map((p) => p.date) ?? [],
        axisLine: { lineStyle: { color: "#475569" } },
      },
      yAxis: { type: "value", axisLine: { lineStyle: { color: "#475569" } }, splitLine: { lineStyle: { color: "#1e293b" } } },
      series: [
        {
          type: "line",
          smooth: true,
          areaStyle: { opacity: 0.15 },
          lineStyle: { color: "#14b8a6" },
          itemStyle: { color: "#14b8a6" },
          data: nw.data?.points.map((p) => Number(p.net_worth)) ?? [],
        },
      ],
    }),
    [nw.data],
  );

  const cashFlowOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 30, right: 16, bottom: 30, left: 60 },
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: "#94a3b8" } },
      xAxis: {
        type: "category",
        data: cashFlow.data?.map((p) => p.month) ?? [],
        axisLine: { lineStyle: { color: "#475569" } },
      },
      yAxis: { type: "value", axisLine: { lineStyle: { color: "#475569" } }, splitLine: { lineStyle: { color: "#1e293b" } } },
      series: [
        {
          name: "Income",
          type: "bar",
          stack: "cash-flow",
          itemStyle: { color: INCOME_COLOR },
          data: cashFlow.data?.map((p) => Number(p.income)) ?? [],
        },
        {
          name: "Expense",
          type: "bar",
          stack: "cash-flow",
          itemStyle: { color: EXPENSE_COLOR },
          // Expenses are summed as positive magnitudes by some backends and as
          // negatives by others; plot them downward either way.
          data: cashFlow.data?.map((p) => -Math.abs(Number(p.expense))) ?? [],
        },
        {
          name: "Net",
          type: "line",
          smooth: true,
          lineStyle: { color: "#14b8a6" },
          itemStyle: { color: "#14b8a6" },
          data: cashFlow.data?.map((p) => Number(p.net)) ?? [],
        },
      ],
    }),
    [cashFlow.data],
  );

  const donutOption: EChartsOption = useMemo(
    () => ({
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { bottom: 0, textStyle: { color: "#94a3b8" }, type: "scroll" },
      color: DONUT_COLORS,
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          itemStyle: { borderColor: "#0f172a", borderWidth: 2 },
          label: { color: "#e2e8f0" },
          data: (spending.data?.rows ?? []).map((r) => ({
            name: r.category_name,
            value: Number(r.total),
          })),
        },
      ],
    }),
    [spending.data],
  );

  const hasSpending = (spending.data?.rows.length ?? 0) > 0;
  const hasSeries = (nw.data?.points.length ?? 0) > 0;
  const hasCashFlow = (cashFlow.data?.length ?? 0) > 0;

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-medium">Reports</h2>

      <div className="rounded-2xl bg-slate-900 p-4">
        <OwnerFilterChips
          owners={owners.data ?? []}
          value={ownerFilter}
          onChange={setOwnerFilter}
        />
      </div>

      <section className="rounded-2xl bg-slate-900 p-6" data-testid="report-net-worth">
        <p className="text-sm text-slate-400">Net worth change (YTD)</p>
        {nw.data && (
          <>
            <p className="text-2xl font-semibold">{formatMoney(nw.data.delta_net_worth, ccy)}</p>
            <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-400">
              <span>Cash flow {formatMoney(nw.data.net_cash_flow, ccy)}</span>
              <span data-testid="revaluation">
                Currency revaluation {formatMoney(nw.data.currency_revaluation, ccy)}
              </span>
            </div>
          </>
        )}
        {hasSeries && <Chart option={nwOption} testid="net-worth-chart" />}
        {ownerFilter && nw.data && (
          <p className="mt-2 text-xs text-slate-500" data-testid="net-worth-attribution">
            Attribution: {nw.data.attribution} — ownership is held by whole accounts, so this series
            sums the accounts assigned to this owner rather than the transactions posted to them.
          </p>
        )}
      </section>

      <section className="rounded-2xl bg-slate-900 p-6" data-testid="report-cash-flow">
        <p className="mb-2 text-sm text-slate-400">Income vs expense (YTD)</p>
        {hasCashFlow ? (
          <Chart option={cashFlowOption} testid="cash-flow-chart" />
        ) : (
          <p className="text-sm text-slate-500">No cash flow in range.</p>
        )}
      </section>

      <section className="rounded-2xl bg-slate-900 p-6" data-testid="report-spending">
        <p className="mb-2 text-sm text-slate-400">Spending by category (YTD)</p>
        {hasSpending ? (
          <Chart option={donutOption} testid="spending-donut" />
        ) : (
          <p className="text-sm text-slate-500">No spending in range.</p>
        )}
        <ul className="mt-3 space-y-1">
          {spending.data?.rows.map((r) => (
            <li key={r.category_id ?? "none"} className="flex justify-between text-sm">
              <span>{r.category_name}</span>
              <span>{formatMoney(r.total, ccy)}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
