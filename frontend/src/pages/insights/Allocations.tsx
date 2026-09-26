// The consolidated allocation (ADR-0011), lifted into Insights from Accounts →
// Investments (session 09, task B4) and extended two ways (ADR-0054):
//
//   - a household's bank cash can be folded in beside the portfolio, so the
//     view can answer "where is all our money", not just "how is the
//     portfolio split";
//   - every row can be tapped to see which accounts it's made of.
//
// The owner liked how the old card looked, so its good parts survive intact:
// the group-by switch, the bars-as-percentages rows, and the honest
// unpriced/FX counts beside the total. What moved is the *shape* — this is now
// a page-width tab rather than a card squeezed into a two-up grid.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAllocation } from "@/api/investments";
import type { Allocation, AllocationGroup, AllocationRow, Money } from "@/api/types";
import AccountMark from "@/components/AccountMark";
import { Day } from "@/components/datetime";
import Dialog from "@/components/Dialog";
import { Checkbox } from "@/components/form";
import { ChevronRightIcon } from "@/components/icons";
import { QueryError, SkeletonRows } from "@/components/QueryStates";
import SegmentedControl, { segmentPanelId, segmentTabId, type Segment } from "@/components/SegmentedControl";
import SheetSelect, { type SheetOption } from "@/components/SheetSelect";
import { formatMoney } from "@/lib/format";
import { STALE_DAYS, excludedSentence, formatPrice, securityTypeLabel, trimDecimal } from "@/lib/investments";

/** `ALLOCATION_GROUPS` in app/schemas/investments.py, in the order the server
 *  lists them. A closed set: the server answers an unknown one with a 422. */
const GROUPS: readonly Segment<AllocationGroup>[] = [
  { id: "security", label: "Security" },
  { id: "type", label: "Type" },
  { id: "account", label: "Account" },
  { id: "currency", label: "Currency" },
];
const GROUP_OPTIONS: readonly SheetOption<AllocationGroup>[] = GROUPS.map((g) => ({
  id: g.id,
  label: String(g.label),
}));

const TESTID = "allocation-groupby";

/** Per-viewer, not per-household: two people looking at the same allocation may
 *  each want a different default, and neither reading should overwrite the
 *  other's. Wrapped in try/catch — `localStorage` throws in a private window,
 *  and the fallback (on) is the more complete picture, so a throw costs a
 *  remembered preference rather than a broken page (see `theme.ts` for the
 *  same shape). */
const CASH_PREF_KEY = "metalmark-allocation-include-cash";

function readCashPref(): boolean {
  try {
    const raw = localStorage.getItem(CASH_PREF_KEY);
    return raw === null ? true : raw === "true";
  } catch {
    return true;
  }
}

function writeCashPref(value: boolean): void {
  try {
    localStorage.setItem(CASH_PREF_KEY, String(value));
  } catch {
    /* Not fatal — the choice just will not survive a reload. */
  }
}

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
 * "Every" has one exception, and it is the row that matters: the cash row
 * ("Cash" or ADR-0021's narrower "Unaccounted cash") is the one `type` row the
 * server names in prose (`services/investments.py`), and its comment says the
 * name is the point — it must not read as an unnamed line. Renaming it on this
 * side would also collide it with the real cash holdings it is not. So the rule
 * is *rename a token, never rename a name*, which needs no list of exceptions.
 */
function groupLabel(groupBy: AllocationGroup, key: string, label: string): string {
  if (groupBy !== "type" || label !== key) return label;
  return securityTypeLabel(key);
}

export default function Allocations() {
  const [groupBy, setGroupBy] = useState<AllocationGroup>("security");
  const [includeCash, setIncludeCash] = useState<boolean>(readCashPref);
  const [detail, setDetail] = useState<AllocationRow | null>(null);

  useEffect(() => {
    writeCashPref(includeCash);
  }, [includeCash]);

  const { data, isPending, isError, error, refetch } = useAllocation(groupBy, includeCash);
  const panelId = segmentPanelId(TESTID, groupBy);

  // The open detail sheet re-keys on the row it names, not on identity: a
  // refetch (the cash toggle, a group-by switch) hands back a *new* rows array
  // every time, and closing a sheet that was open across that swap because its
  // old object reference vanished would be a bug wearing a stale-props costume.
  const liveDetail = useMemo(
    () => (detail ? (data?.rows.find((r) => r.key === detail.key) ?? null) : null),
    [detail, data],
  );

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

      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Phone: a pill that opens a sheet — four segments is exactly the case
            §5 calls out (a row of choices that would not comfortably fit at
            360px becomes a picker, not a scrolling strip). Desktop keeps the
            segmented control, unchanged from the card this replaced. */}
        <div className="sm:hidden">
          <SheetSelect
            label="Group by"
            value={groupBy}
            options={GROUP_OPTIONS}
            onChange={setGroupBy}
            testid={`${TESTID}-sheet`}
          />
        </div>
        <div className="hidden sm:block">
          <SegmentedControl
            label="Group allocation by"
            segments={GROUPS}
            value={groupBy}
            onChange={setGroupBy}
            testid={TESTID}
          />
        </div>
        <Checkbox
          label="Include bank cash"
          checked={includeCash}
          onChange={(e) => setIncludeCash(e.target.checked)}
          data-testid="allocation-include-cash"
        />
      </div>

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
          <AllocationBody data={data} groupBy={groupBy} onSelect={setDetail} />
        )}
      </div>

      {liveDetail && (
        <AllocationDetailSheet
          row={liveDetail}
          groupBy={groupBy}
          ccy={data?.base_currency ?? "USD"}
          onClose={() => setDetail(null)}
        />
      )}
    </section>
  );
}

function AllocationBody({
  data,
  groupBy,
  onSelect,
}: {
  data: Allocation;
  groupBy: AllocationGroup;
  onSelect: (row: AllocationRow) => void;
}) {
  const ccy = data.base_currency;
  const excluded = excludedSentence(data.unpriced_positions, data.no_rate_positions);

  // Nothing to allocate and nothing unpriced: a household with no positions yet
  // (and, with the cash toggle on, no bank cash either).
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
          <li key={r.key} className="border-b border-border last:border-b-0">
            {/* A button, not a static row: every group taps through to its
                sources (§4, the tap-through detail sheet), cash row or not —
                the chevron says so and `min-h-11` clears the target floor with
                room to spare. */}
            <button
              type="button"
              onClick={() => onSelect(r)}
              className="flex min-h-11 w-full min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2 text-left hover:bg-surface-inset"
              data-testid={`allocation-row-${r.key}`}
            >
              <div className="min-w-0">
                <p className="text-sm">{groupLabel(groupBy, r.key, r.label)}</p>
                {/* How many positions the line is made of: a 3% line that is
                    one holding and a 3% line that is thirty read very
                    differently. */}
                <p className="text-xs text-fg-muted" data-testid={`allocation-holdings-${r.key}`}>
                  {r.holdings} {r.holdings === 1 ? "position" : "positions"}
                </p>
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-2">
                <div className="text-right">
                  <p className="text-sm" data-testid={`allocation-value-${r.key}`}>
                    {formatMoney(r.value_base, ccy)}
                  </p>
                  <p className="text-xs text-fg-muted" data-testid={`allocation-percent-${r.key}`}>
                    {formatPercent(r.percent)}
                  </p>
                </div>
                <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
              </div>
            </button>
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
          grouping or the cash toggle changes, so they are announced rather
          than silently repainted. */}
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

/** The tap-through detail: which accounts a row is made of (ADR-0054's
 *  `sources`), each linking back to Accounts — there is no per-account deep
 *  link into that page today, so this goes to the page itself rather than to a
 *  dialog nothing here can address. */
function AllocationDetailSheet({
  row,
  groupBy,
  ccy,
  onClose,
}: {
  row: AllocationRow;
  groupBy: AllocationGroup;
  ccy: string;
  onClose: () => void;
}) {
  return (
    <Dialog open onClose={onClose} title={groupLabel(groupBy, row.key, row.label)} testid="allocation-detail">
      <div className="space-y-4">
        <div className="flex items-baseline justify-between gap-3 rounded-card bg-surface-inset p-3">
          <div>
            <p className="text-xs text-fg-muted">Value</p>
            <p className="text-base font-semibold" data-testid="allocation-detail-value">
              {formatMoney(row.value_base, ccy)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-fg-muted">Share of total</p>
            <p className="text-base font-semibold" data-testid="allocation-detail-percent">
              {formatPercent(row.percent)}
            </p>
          </div>
        </div>

        <div>
          <h3 className="mb-2 text-xs font-medium text-fg-muted">
            {row.sources.length} {row.sources.length === 1 ? "account" : "accounts"}
          </h3>
          <ul className="space-y-1" data-testid="allocation-detail-sources">
            {row.sources.map((s) => (
              <li key={s.account_id}>
                <Link
                  to="/accounts"
                  className="flex min-h-14 w-full items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-inset"
                  data-testid={`allocation-detail-source-${s.account_id}`}
                >
                  <AccountMark name={s.account_name} institution={s.institution} size="md" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{s.account_name}</span>
                    {groupBy === "security" && s.quantity !== null && s.price !== null ? (
                      <span className="block text-xs text-fg-muted">
                        {trimDecimal(s.quantity)} × {formatPrice(s.price, s.price_currency ?? ccy)}
                        {s.price_date && (
                          <>
                            {" "}
                            · <Day value={s.price_date} />
                          </>
                        )}
                      </span>
                    ) : (
                      <span className="block text-xs text-fg-muted">
                        {formatPercent(s.share_of_group)} of this group
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-sm font-semibold">{formatMoney(s.value_base, ccy)}</span>
                    {groupBy === "security" && (
                      <span className="block text-xs text-fg-muted">
                        {formatPercent(s.share_of_group)}
                      </span>
                    )}
                  </span>
                  <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Dialog>
  );
}
