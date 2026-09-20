// The reconciliation of the change in net worth (ADR-0032): the terms that
// account for it, and the one of them that is a residual.
//
//     Δ net worth = net cash flow + currency revaluation + market appreciation
//                   + unexplained
//
// Hand-built rather than an ECharts surface, deliberately. This is five numbers
// and their relative sizes, and a canvas would put all five behind `role="img"`
// and one sentence — while here the *text* is the finding. "Unexplained
// $1,240.00" is the thing a person acts on; the bar only says how it compares to
// its neighbours (DESIGN.md §2.9: a chart is never the only way to read a value).
//
// `unexplained` is a row like any other and is never folded away or hidden when
// it is non-zero. A non-zero residual is not automatically a bug — a `stated`
// account's change has no history to attribute it to (ADR-0032 §6), and a
// brokerage account looks the same until its trades are entered — so when it is
// non-zero the component says *why it can be*, instead of either hiding the one
// number that reveals a broken term or crying wolf about it.
import type { NetWorthSeries } from "@/api/types";
import { formatMoney } from "@/lib/format";

/** One step of the waterfall: what it is, and its share of the largest step. */
function Step({
  label,
  money,
  currency,
  width,
  warn = false,
  testid,
  children,
}: {
  label: string;
  money: string;
  currency: string;
  /** Percentage of the widest step, so the bars compare to each other. */
  width: string;
  /** The residual reads as a warning whichever way it points; everything else
   *  is coloured by its sign. */
  warn?: boolean;
  testid: string;
  /** Detail belonging to *this* step — nested inside the `<li>` rather than beside
   *  it, so a screen reader reads it as part of the step it qualifies. */
  children?: React.ReactNode;
}) {
  const value = Number(money);
  // A zero step is neither a gain nor a loss — and its bar is zero-width, so
  // only the figure's colour is visible at all.
  const bar = warn ? "bg-warning" : value < 0 ? "bg-negative" : "bg-positive";
  const text = warn
    ? "text-warning"
    : value < 0
      ? "text-negative"
      : value > 0
        ? "text-positive"
        : "text-fg-muted";
  return (
    <li data-testid={testid}>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-fg-muted">{label}</span>
        {/* The sign is a character, not a colour: a reader who cannot separate
            red from green still gets "+" or "−" (§7 item 1). */}
        <span className={`shrink-0 font-medium tabular-nums ${text}`}>
          {value > 0 ? "+" : ""}
          {formatMoney(money, currency)}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-inset">
        <div className={`h-full rounded-full ${bar}`} style={{ width }} />
      </div>
      {children}
    </li>
  );
}

export default function Reconciliation({ series }: { series: NetWorthSeries }) {
  const currency = series.base_currency;
  const steps = [
    { key: "cash-flow", label: "Cash flow", money: series.net_cash_flow },
    { key: "revaluation", label: "Currency revaluation", money: series.currency_revaluation },
    { key: "appreciation", label: "Market appreciation", money: series.market_appreciation },
  ];
  const unexplained = series.unexplained;
  const named = series.unexplained_by_account;

  // Bars are scaled to the largest *step*, not to the total: in a waterfall the
  // comparison that matters is between consecutive moves, and scaling to the
  // delta would flatten every term of a year that ended roughly where it began.
  //
  // `Number` here is for bar widths only. Every figure printed is the exact
  // decimal string from the API — this app has never rendered money through a
  // float and does not start in a progress bar.
  const max = Math.max(
    ...steps.map((s) => Math.abs(Number(s.money))),
    Math.abs(Number(unexplained)),
  );
  const widthOf = (money: string) =>
    max === 0 ? "0%" : `${Math.min(100, (Math.abs(Number(money)) / max) * 100)}%`;

  return (
    <div className="mt-4" data-testid="reconciliation">
      <p className="text-xs text-fg-muted">
        Net worth moves for four reasons. Three of them are measured from the ledger; the
        fourth is what is left over, and it should be nothing.
      </p>
      <ul className="mt-3 space-y-2">
        {steps.map((step) => (
          <Step
            key={step.key}
            label={step.label}
            money={step.money}
            currency={currency}
            width={widthOf(step.money)}
            testid={`reconciliation-${step.key}`}
          />
        ))}
        <Step
          label="Unexplained"
          money={unexplained}
          currency={currency}
          width={widthOf(unexplained)}
          warn
          testid="reconciliation-unexplained"
        >
          {named.length > 0 && (
            // "Of which", and the wording matters: these are the accounts whose
            // share is big enough to name, not a partition of the figure above —
            // the parts under 1% are still inside it. Presented as a breakdown it
            // would look like an error when the numbers did not add up.
            <div className="mt-2 border-l border-border pl-3" data-testid="reconciliation-attribution">
              <p className="text-xs text-fg-muted">Of which, by account:</p>
              <ul className="mt-1 space-y-0.5">
                {named.map((row) => (
                  <li
                    key={row.account_id}
                    className="flex items-baseline justify-between gap-3 text-xs"
                    data-testid="reconciliation-attribution-row"
                  >
                    <span className="truncate text-fg-muted">{row.name}</span>
                    <span className="shrink-0 tabular-nums text-fg-muted">
                      {formatMoney(row.amount, currency)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Step>
      </ul>

      <div className="mt-3 flex items-baseline justify-between gap-3 border-t border-border pt-2 text-sm">
        <span className="font-medium">Change in net worth</span>
        <span className="shrink-0 font-semibold tabular-nums">
          {formatMoney(series.delta_net_worth, currency)}
        </span>
      </div>

      {Number(unexplained) !== 0 && (
        <p className="mt-2 text-xs text-fg-muted" data-testid="reconciliation-note">
          A change in an account whose balance is <em>stated</em> — the provider's own
          number, with no history we can break down — lands here legitimately. It is
          also where a term that is computed wrong would show up, which is why it is
          reported rather than absorbed.
        </p>
      )}

      {Number(unexplained) !== 0 && named.length === 0 && (
        // Only reachable when the whole residual is under the materiality floor the
        // service attributes at — under 1%, or under a cent. Said out loud rather
        // than left as a blank, because the absence of a list is otherwise
        // indistinguishable from a response that forgot to send one.
        <p className="mt-1 text-xs text-fg-muted" data-testid="reconciliation-unattributed">
          It is spread thinly enough that no single account is worth naming.
        </p>
      )}

      {series.warnings.length > 0 && (
        <div
          className="mt-3 rounded-control bg-warning/10 p-3"
          data-testid="reconciliation-warnings"
        >
          <p className="text-xs font-medium text-warning">
            Some of these figures are missing data
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
            {series.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
