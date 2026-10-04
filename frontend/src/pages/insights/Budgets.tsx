// Insights → Budgets (ADR-0058).
//
// A plan for one category in one month, and the spend it is read against. The
// whole feature is that comparison, so the page is a list of it: every figure
// here is the Spending report's own figure for the same window — `total_spent`
// is that report's total to the cent — which is why there is no arithmetic on
// this side beyond the difference between two numbers the server sent.
//
// **The window is stated, once, at the top, and every sentence that could be
// read as a bare figure names it.** A budget without its month is the bug this
// app has been burned by: "$600" means one thing in September and another in
// October, and a reader who has to guess which one they are looking at has been
// given a number rather than an answer. The stepper moves a month at a time and
// the server echoes back the month it resolved, so what is drawn is always the
// period the response names rather than one this component worked out.
//
// **What is planned is written in Settings → Categories**, which is the complete
// list of categories and therefore the only place a plan can be made for one
// with no spending yet. This tab lists what has a plan *or* has spend — the two
// things worth looking at — and offers the same dialog on any row, because the
// number that makes a person want to change a plan is on this screen.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { BudgetReport, BudgetRow } from "@/api/types";
import { useBudgetReport } from "@/api/hooks";
import { formatMonth } from "@/lib/dates";
import { formatMoney, fromMinorUnits, sumMinorUnits } from "@/lib/format";
import BudgetDialog from "@/components/BudgetDialog";
import DataNotes from "@/components/DataNotes";
import { Button, Spinner } from "@/components/form";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import { QueryError, SkeletonRows } from "@/components/QueryStates";

/** The month `delta` months from `period`, as the first day of that month.
 *
 *  Calendar arithmetic on the *year and month* of an ISO day, never on a Date:
 *  a `Date` would put this in the viewer's timezone and "the month after
 *  September" is not a timezone question. The day is always 01 by then, so no
 *  month-end carry is possible.
 */
export function addMonths(period: string, delta: number): string {
  const [year, month] = period.split("-").map(Number);
  const total = year * 12 + (month - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}-01`;
}

export default function Budgets() {
  // Which month is being looked at lives in the **URL**, like the tab above it
  // and the window on Overview: "here is September's budget" is a link, and this
  // is the same argument `RangeControl` makes for its own window. An absent
  // param asks the server for the current month — it resolves "now" in the
  // household's timezone and echoes the period back, so this component never
  // decides what month it is, only what to ask for next. `replace` rather than
  // push for the same reason the range chips use it: stepping through months
  // should not put every one of them between the reader and what they came from.
  const [params, setParams] = useSearchParams();
  const period = params.get("period");
  const [editing, setEditing] = useState<BudgetRow | null>(null);
  const report = useBudgetReport(period);

  const goTo = (next: string | null) => {
    const query = new URLSearchParams(params);
    if (next === null) query.delete("period");
    else query.set("period", next);
    setParams(query, { replace: true });
  };

  if (report.isError) {
    return (
      <QueryError
        what="your budgets"
        error={report.error}
        onRetry={() => report.refetch()}
        testid="budgets-error"
      />
    );
  }
  if (report.isPending || !report.data) {
    return <SkeletonRows what="your budgets" rows={4} testid="budgets-loading" />;
  }

  return (
    <BudgetsBody
      data={report.data}
      onPeriod={goTo}
      hasPeriodParam={period !== null}
      onEdit={setEditing}
      editing={editing}
      onCloseEdit={() => setEditing(null)}
      refetching={report.isFetching}
    />
  );
}

function BudgetsBody({
  data,
  onPeriod,
  hasPeriodParam,
  onEdit,
  editing,
  onCloseEdit,
  refetching,
}: {
  data: BudgetReport;
  onPeriod: (period: string | null) => void;
  /** Whether the address bar names a month — which is what "This month" undoes. */
  hasPeriodParam: boolean;
  onEdit: (row: BudgetRow) => void;
  editing: BudgetRow | null;
  onCloseEdit: () => void;
  refetching: boolean;
}) {
  const ccy = data.base_currency;
  const monthLabel = formatMonth(data.period_start, "long");
  const planned = data.rows.filter((r) => r.budget !== null);
  // Nothing planned and nothing spent: there is no comparison on this screen, so
  // the four totals below would be four zeroes dressed up as a report. What a
  // reader needs instead is where a plan comes from.
  const nothingYet = data.rows.length === 0 && Number(data.total_spent) === 0;

  return (
    <section className="space-y-3" data-testid="budgets">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* The window, as a control: a plan belongs to a month, and looking at
            last month's is how a person decides this month's. It shows the
            period the *response* named, never a period computed here — the two
            would disagree across a timezone boundary. */}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            aria-label="Previous month"
            onClick={() => onPeriod(addMonths(data.period_start, -1))}
            data-testid="budgets-prev"
          >
            <ChevronLeftIcon aria-hidden="true" className="size-5" />
          </Button>
          <h2 className="min-w-0 text-sm font-semibold" data-testid="budgets-period">
            {monthLabel}
          </h2>
          <Button
            variant="ghost"
            aria-label="Next month"
            onClick={() => onPeriod(addMonths(data.period_start, 1))}
            data-testid="budgets-next"
          >
            <ChevronRightIcon aria-hidden="true" className="size-5" />
          </Button>
          {refetching && <Spinner className="text-fg-muted" />}
        </div>
        {hasPeriodParam && (
          <Button
            variant="ghost"
            className="px-2 py-1 text-xs"
            onClick={() => onPeriod(null)}
            data-testid="budgets-this-month"
          >
            This month
          </Button>
        )}
      </div>

      {nothingYet ? (
        <p
          className="rounded-card bg-surface-raised px-4 py-8 text-center text-sm text-fg-muted"
          data-testid="budgets-empty"
        >
          Nothing planned and nothing spent in {monthLabel}. Set a budget for a category in
          Settings → Categories.
        </p>
      ) : (
        <div className="space-y-3 rounded-card bg-surface-raised p-4">
          {/* Four figures, each labelled with what it is *of*. `budgeted_spent`
              and `unbudgeted_spent` partition `total_spent` server-side, so the
              reader can see how much of the month was planned for at all —
              which is the question the row list below then answers one category
              at a time. */}
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure
              label={`Spent · ${monthLabel}`}
              value={data.total_spent}
              ccy={ccy}
              testid="budgets-total-spent"
            />
            <Figure
              label="Planned"
              value={data.total_budget}
              ccy={ccy}
              testid="budgets-total-budget"
            />
            <Figure
              label="Spent on planned categories"
              value={data.budgeted_spent}
              ccy={ccy}
              testid="budgets-total-budgeted"
            />
            <Figure
              label="Spent with no plan"
              value={data.unbudgeted_spent}
              ccy={ccy}
              testid="budgets-total-unbudgeted"
            />
          </dl>

          <DataNotes notes={data.warnings} summary="Some spending has incomplete data" testid="budgets-warning" />

          {/* Planned categories first, then the spend nobody planned for — the
              order the server states, and the order a reader wants: their own
              budgets first, "and here is what you have not planned" after. */}
          <ul className="divide-y divide-border" data-testid="budgets-rows">
            {data.rows.map((r) => (
              <BudgetLine
                key={r.category_id}
                row={r}
                ccy={ccy}
                monthLabel={monthLabel}
                onEdit={() => onEdit(r)}
              />
            ))}
          </ul>

          {planned.length === 0 && (
            <p className="text-xs text-fg-muted" data-testid="budgets-no-plans">
              Nothing is planned for {monthLabel} yet. Every category above is spending without a
              budget; set one from any row.
            </p>
          )}

          {/* A category deleted after its plan was set is not a state to draw:
              the foreign key is ON DELETE CASCADE, so the plan went with the
              category and the row is simply absent from this response. There is
              no client-side cleanup to write — the honest answer is the list a
              reader sees, which no longer mentions it. */}

          {editing && (
            <BudgetDialog
              category={{ id: editing.category_id, name: editing.category_name }}
              period={data.period_start}
              monthLabel={monthLabel}
              row={editing}
              currency={ccy}
              onClose={onCloseEdit}
            />
          )}
        </div>
      )}
    </section>
  );
}

/** One figure of the period's totals. Money is `text-base font-semibold` and
 *  never truncated (§6): it is the content, and the label beside it is one line
 *  of context that may wrap under it on a phone. */
function Figure({
  label,
  value,
  ccy,
  testid,
}: {
  label: string;
  value: string;
  ccy: string;
  testid: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-base font-semibold text-fg" data-testid={testid}>
        {formatMoney(value, ccy)}
      </dd>
    </div>
  );
}

/**
 * The row's columns at `lg:` (§9.4), written once because Tailwind reads source
 * *text* — `grid-cols-${n}` is never generated, and the failure is silent (the
 * row collapses to one column and still renders, just wrong).
 *
 * A row here is a sentence: **the figure, then what it is a share of**. On a
 * phone those are two lines and the sentence reads straight down them, because
 * both lines are short. At 1280 the same two lines were 1100 px apart — the
 * figure at the right edge of the first, its "of $1,000.00 planned for Sep 2026"
 * at the left edge of the second — and a sentence has to be read, not
 * reassembled. So the figure gets a column of its own (7rem, right-aligned, so
 * the amounts decimal-align down the list) and its qualifier the column
 * immediately after it: 16 px apart at every width above `lg:`, and nothing can
 * be inserted between them because a column boundary is where that cell *ends*.
 *
 * The name and the qualifier are the two columns that take what is left over,
 * and they do not take it evenly: `0.6fr` against `1.4fr`, because a category
 * name is a short label ("Public Transit") and the qualifier is the sentence the
 * row exists to say. Evenly split, at 1024 px the sentence lost a word to the
 * next line — "…planned for Sep / 2026" — for no gain to the name. Nothing is
 * truncated either way: §6.5 gives way to a second line, never to an ellipsis
 * over a number. The bar, the difference sentence and the action are
 * fixed-width or content-sized, so the columns that matter line up down the
 * list; the action is last, so its own width — "Edit" against "Set a budget" —
 * shifts nothing but itself.
 *
 * `minmax(0,…)` rather than `1fr` because a grid item's default `min-width:
 * auto` refuses to shrink below its content, which is how one long category
 * name pushes a row into sideways scroll (§5).
 */
const ROW_COLUMNS =
  "lg:grid-cols-[minmax(0,0.6fr)_7rem_minmax(0,1.4fr)_9rem_13rem_max-content] lg:gap-x-4";

/**
 * One category: what was spent, what was planned, and which way the difference
 * points.
 *
 * The bar is a *proportion of the plan*, so it is capped at full when the plan
 * is passed: a bar that ran past its track would say "twice the plan" by
 * overflowing the card, and the sentence below already says the amount over. It
 * is `aria-hidden` because it carries no information the two figures and the
 * sentence do not (§2.9 — a picture is never the only way to read a value).
 *
 * At `lg:` the row gains columns rather than height (§9.4): the phone's stacked
 * lines become cells of one grid, one line tall, and the row is *shorter* than
 * its phone form — never under 48 px, because §5's 44 px target rule is not a
 * touch-only rule. One tree, two layouts: the phone's first line is `contents`
 * at `lg:` and stops being layout, which promotes the name and the figure to
 * cells of the row's own grid (§9 — no second tree to drift, and the row's
 * accessible text is the same at both widths).
 *
 * The three states a row can be in, and what each says:
 *
 *   - **planned, unspent** — `$0.00` against the plan, the bar empty, and the
 *     sentence says nothing has been spent *yet* rather than claiming the month
 *     came in under. A plan with no spending is not a result.
 *   - **planned, overspent** — the difference is named and coloured as a
 *     warning. It is not red: an expense is not a loss (§6.2), and this is a
 *     plan missed rather than money gone.
 *   - **spend with no plan** — no bar, because there is nothing to measure
 *     against, and the row says so instead of drawing an empty one that would
 *     read as "under budget by everything".
 */
function BudgetLine({
  row,
  ccy,
  monthLabel,
  onEdit,
}: {
  row: BudgetRow;
  ccy: string;
  monthLabel: string;
  onEdit: () => void;
}) {
  const spent = Number(row.spent);
  const planned = row.budget === null ? null : Number(row.budget);
  // Floats, and only here: this is a bar's width and the sign of a comparison.
  // The *figures* beside them are the exact decimal strings the server sent,
  // formatted through `lib/format.ts`'s one exact path (§6.1) — and the
  // difference is taken in minor units for the same reason, so "over by" cannot
  // be a cent out. A float that lands on the wrong side of `>` would still be a
  // wrong answer, so the comparison is on minor units too.
  const ratio =
    planned === null || planned <= 0 ? (spent > 0 ? 1 : 0) : Math.min(spent / planned, 1);
  const plannedMinor = sumMinorUnits([row.budget ?? "0"], ccy).minor;
  const spentMinor = sumMinorUnits([row.spent], ccy).minor;
  const differenceMinor = spentMinor - plannedMinor;
  const over = row.budget !== null && differenceMinor > 0;

  return (
    <li
      className={`space-y-1 py-3 lg:grid lg:min-h-12 lg:items-center lg:space-y-0 lg:py-2 ${ROW_COLUMNS}`}
      data-testid={`budget-row-${row.category_id}`}
    >
      {/* Phone: the name and the figure share a line, `justify-between`, so the
          row opens with its subject and its amount. At `lg:` this wrapper is
          `contents` — it stops being layout and its two children become cells of
          the row's own grid, the name in the first column and the figure in the
          second, immediately left of the sentence that qualifies it. The amount
          is right-aligned in its column so the figures decimal-align down the
          list (§6.1), which is also what the phone line does with one figure on
          it. */}
      <div className="flex items-baseline justify-between gap-3 lg:contents">
        <span className="min-w-0 text-sm">
          {row.category_icon ? `${row.category_icon} ` : ""}
          {row.category_name}
        </span>
        <span
          className="shrink-0 text-base font-semibold text-fg lg:text-right"
          data-testid={`budget-spent-${row.category_id}`}
        >
          {formatMoney(row.spent, ccy)}
        </span>
      </div>

      {row.budget === null ? (
        // The sentence takes the three columns the plan, the bar and the
        // difference would have used, so it is never squeezed into the width of
        // one of them — and the bar it does not have leaves no gap that reads as
        // a bar at zero.
        <p
          className="min-w-0 text-xs text-fg-muted lg:col-span-3"
          data-testid={`budget-noplan-${row.category_id}`}
        >
          No plan for {monthLabel} — this is spending nothing has been budgeted for.
        </p>
      ) : (
        <>
          <p className="min-w-0 text-xs text-fg-muted">
            of{" "}
            <span className="font-medium text-fg" data-testid={`budget-planned-${row.category_id}`}>
              {formatMoney(row.budget, ccy)}
            </span>{" "}
            planned for {monthLabel}
          </p>
          <div
            aria-hidden="true"
            className="h-1.5 overflow-hidden rounded-full bg-surface-inset"
            data-testid={`budget-bar-${row.category_id}`}
          >
            <div
              className={`h-full rounded-full ${over ? "bg-warning" : "bg-positive"}`}
              style={{ width: `${Math.round(ratio * 100)}%` }}
            />
          </div>
          <p
            className={`text-xs ${over ? "text-warning" : "text-fg-muted"}`}
            data-testid={`budget-remaining-${row.category_id}`}
          >
            {over
              ? `Over by ${formatMoney(fromMinorUnits(differenceMinor, ccy), ccy)} this month.`
              : spentMinor === 0
                ? "Nothing spent yet this month."
                : `${formatMoney(fromMinorUnits(-differenceMinor, ccy), ccy)} left this month.`}
          </p>
        </>
      )}

      <Button
        variant="ghost"
        className="px-2 py-1 text-xs"
        onClick={onEdit}
        data-testid={`budget-edit-${row.category_id}`}
      >
        {row.budget === null ? "Set a budget" : "Edit"}
      </Button>
    </li>
  );
}
