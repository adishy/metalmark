// Basic reporting. ECharts net-worth line (with the currency-revaluation
// figure) and a category donut are layered on by the UR workstream.
import { useMemo } from "react";
import { useNetWorthSeries, useSpending } from "@/api/hooks";
import { formatMoney } from "@/lib/format";

function monthRange(): { start: string; end: string } {
  const now = new Date();
  const start = new Date(now.getFullYear(), 0, 1);
  const end = new Date(now.getFullYear(), 11, 31);
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
}

export default function Reports() {
  const { start, end } = useMemo(monthRange, []);
  const nw = useNetWorthSeries(start, end);
  const spending = useSpending(start, end);
  const ccy = nw.data?.base_currency ?? "USD";

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-medium">Reports</h2>

      <section className="rounded-2xl bg-slate-900 p-6" data-testid="report-net-worth">
        <p className="text-sm text-slate-400">Net worth change (YTD)</p>
        {nw.data && (
          <>
            <p className="text-2xl font-semibold">
              {formatMoney(nw.data.delta_net_worth, ccy)}
            </p>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-400">
              <span>Cash flow {formatMoney(nw.data.net_cash_flow, ccy)}</span>
              <span data-testid="revaluation">
                Currency revaluation {formatMoney(nw.data.currency_revaluation, ccy)}
              </span>
            </div>
          </>
        )}
      </section>

      <section className="rounded-2xl bg-slate-900 p-6" data-testid="report-spending">
        <p className="mb-3 text-sm text-slate-400">Spending by category (YTD)</p>
        <ul className="space-y-1">
          {spending.data?.rows.map((r) => (
            <li key={r.category_id ?? "none"} className="flex justify-between text-sm">
              <span>{r.category_name}</span>
              <span>{formatMoney(r.total, ccy)}</span>
            </li>
          ))}
          {spending.data && spending.data.rows.length === 0 && (
            <li className="text-sm text-slate-500">No spending in range.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
