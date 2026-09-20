// The consolidated allocation — the "single page that aggregates every holding
// across all accounts" of docs/ARCHITECTURE.md §4, as one card.
//
// The rows cannot show a position with no price: it has no value to add to any
// group. So the counts the API returns *beside* the rows are what make this view
// honest, and they are rendered next to the total rather than down in a data
// quality panel — a total quietly missing 30% of the portfolio is the failure
// ADR-0032 §5 exists to prevent, and it is only visible where the total is.
//
// Two distinct counts, never one: "enter a price" and "enter an FX rate" are
// different fixes, and a single "3 positions excluded" would hide which is
// needed.

import { useState } from "react";
import { useAllocation } from "@/api/investments";
import type { Allocation, AllocationGroup, Money } from "@/api/types";
import { Day } from "@/components/datetime";
import { QueryError, SkeletonRows } from "@/components/QueryStates";
import SegmentedControl, { segmentPanelId, segmentTabId, type Segment } from "@/components/SegmentedControl";
import { formatMoney } from "@/lib/format";
import { STALE_DAYS, excludedSentence, securityTypeLabel } from "@/lib/investments";

/** `ALLOCATION_GROUPS` in app/schemas/investments.py, in the order the server
 *  lists them. A closed set: the server answers an unknown one with a 422. */
const GROUPS: readonly Segment<AllocationGroup>[] = [
  { id: "security", label: "Security" },
  { id: "type", label: "Type" },
  { id: "account", label: "Account" },
  { id: "currency", label: "Currency" },
];

const TESTID = "allocation-groupby";

/**
 * A share, at one decimal.
 *
 * The API sends four (`percent`), which is noise down a column. A percent is not
 * money, so the currency's minor unit does not apply — but the §6.5 concern does:
 * a row that holds value must not display as `0.0%`, so a non-zero share below
 * the displayed precision is bounded rather than rounded away.
 *
 * The bound is on the *magnitude*, and the direction is kept. A group can be
 * negative — short positions are not rounded away by the valuation (ADR-0032) —
 * and `(-0.04).toFixed(1)` is `"-0.0%"`: a non-zero share displayed as zero,
 * which is the exact failure this function exists to prevent. `>-0.1%` says
 * both that the row holds a little value and which way it points.
 */
function formatPercent(percent: Money): string {
  const n = Number(percent);
  if (n !== 0 && Math.abs(n) < 0.05) return n < 0 ? ">-0.1%" : "<0.1%";
  return `${n.toFixed(1)}%`;
}

/**
 * How a group reads. The server's `label` is right for three of the four groups
 * — a ticker, an account name, a currency code are already the words a person
 * uses — but every `type` row labels itself with the wire token (`mutual_fund`),
 * so those are named through the shared security-type vocabulary.
 *
 * "Every" has one exception, and it is the row that matters: the unaccounted-cash
 * plug is the one `type` row the server names in prose ("Unaccounted cash", see
 * `services/investments.py`), and its comment says the name is the point — the
 * plug must not read as an unnamed line. Renaming it "Cash" would also collide it
 * with the real cash holdings it is not. So the rule is *rename a token, never
 * rename a name*, which needs no list of exceptions to maintain.
 */
function groupLabel(groupBy: AllocationGroup, key: string, label: string): string {
  if (groupBy !== "type" || label !== key) return label;
  return securityTypeLabel(key);
}

export default function AllocationBreakdown() {
  const [groupBy, setGroupBy] = useState<AllocationGroup>("security");
  const { data, isPending, isError, error, refetch } = useAllocation(groupBy);

  const panelId = segmentPanelId(TESTID, groupBy);

  return (
    <section className="space-y-3" data-testid="allocation">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Allocation</h2>
        {data && (
          <p className="text-xs text-fg-muted" data-testid="allocation-as-of">
            as of <Day value={data.as_of} />
          </p>
        )}
      </div>

      <SegmentedControl
        label="Group allocation by"
        segments={GROUPS}
        value={groupBy}
        onChange={setGroupBy}
        testid={TESTID}
      />

      <div role="tabpanel" id={panelId} aria-labelledby={segmentTabId(TESTID, groupBy)} data-testid={panelId}>
        {isError ? (
          <QueryError
            what="your allocation"
            error={error}
            onRetry={() => refetch()}
            testid="allocation-error"
          />
        ) : isPending || !data ? (
          <SkeletonRows what="your allocation" rows={4} testid="allocation-loading" />
        ) : (
          <AllocationBody data={data} groupBy={groupBy} />
        )}
      </div>
    </section>
  );
}

function AllocationBody({ data, groupBy }: { data: Allocation; groupBy: AllocationGroup }) {
  const ccy = data.base_currency;
  const excluded = excludedSentence(data.unpriced_positions, data.no_rate_positions);

  // Nothing to allocate and nothing unpriced: a household with no positions yet.
  if (data.rows.length === 0 && excluded === null) {
    return (
      <p
        className="rounded-card bg-surface-raised px-4 py-8 text-center text-sm text-fg-muted"
        data-testid="allocation-empty"
      >
        No holdings yet.
        {/* Where a "Add a holding" button would go — the entry forms for
            securities, prices and positions are not built in this workstream
            (UINV is the read side), and a link to a form that does not exist
            would be worse than this sentence. */}
      </p>
    );
  }

  // Every position unpriced. This is emphatically *not* "No holdings yet": the
  // household holds things, and the honest statement is that none of them could
  // be valued — which is why the empty state above is gated on `excluded`.
  if (data.rows.length === 0) {
    return (
      <div className="rounded-card bg-surface-raised px-4 py-8 text-center" data-testid="allocation-unvalued">
        <p className="text-sm text-fg">No position could be valued, so nothing can be allocated.</p>
        <p className="mt-1 text-xs text-warning" role="status">
          {excluded}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-card bg-surface-raised p-4">
      <ul data-testid="allocation-rows">
        {data.rows.map((r) => (
          <li
            key={r.key}
            className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 border-b border-border py-2 last:border-b-0"
            data-testid={`allocation-row-${r.key}`}
          >
            <div className="min-w-0">
              <p className="text-sm">{groupLabel(groupBy, r.key, r.label)}</p>
              {/* How many positions the line is made of: a 3% line that is one
                  holding and a 3% line that is thirty read very differently. */}
              <p className="text-xs text-fg-muted" data-testid={`allocation-holdings-${r.key}`}>
                {r.holdings} {r.holdings === 1 ? "position" : "positions"}
              </p>
            </div>
            <div className="ml-auto shrink-0 text-right">
              <p className="text-sm" data-testid={`allocation-value-${r.key}`}>
                {formatMoney(r.value_base, ccy)}
              </p>
              <p className="text-xs text-fg-muted" data-testid={`allocation-percent-${r.key}`}>
                {formatPercent(r.percent)}
              </p>
            </div>
          </li>
        ))}
      </ul>

      {/* The total is not the last row: semibold, with a border above it (§6.5),
          and it names its base currency at least once per screen (§6.4). */}
      <div className="mt-2 flex items-baseline justify-between gap-3 border-t border-border pt-3">
        <p className="text-sm font-semibold">Total · {ccy}</p>
        <p className="text-sm font-semibold" data-testid="allocation-total">
          {formatMoney(data.total_base, ccy)}
        </p>
      </div>

      {/* role="status": the counts arrive with the data and change when the
          grouping changes, so they are announced rather than silently repainted. */}
      <div role="status" aria-atomic="true">
        {excluded && (
          <p className="mt-1 text-xs text-warning" data-testid="allocation-excluded">
            {excluded}
          </p>
        )}
        {data.max_stale_days !== null && (
          <p
            className={`mt-1 text-xs ${data.max_stale_days > STALE_DAYS ? "text-warning" : "text-fg-muted"}`}
            data-testid="allocation-stale"
          >
            {/* The age is always stated, however small: the total is only as good
                as the oldest price in it, and "the market was flat" and "nobody
                has entered a price" are different facts (ADR-0032 §5). */}
            Oldest price used: {data.max_stale_days} {data.max_stale_days === 1 ? "day" : "days"} old.
          </p>
        )}
      </div>
    </div>
  );
}
