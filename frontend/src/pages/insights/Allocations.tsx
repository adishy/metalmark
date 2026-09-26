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
//
// Issue #34 adds the treemap as a *view* of the same rows, beside the list
// rather than instead of it: the picture answers "which group is big" at a
// glance, and the list under it is still the text equivalent (§2.9) — every
// group, its figure, its share, and the way into its sources. The list is not
// hidden in either view; the switch only decides whether the picture is drawn
// above it.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { Link } from "react-router-dom";
import { useAllocation } from "@/api/investments";
import type { Allocation, AllocationGroup, AllocationRow } from "@/api/types";
import { formatDay } from "@/lib/dates";
import { formatMoney } from "@/lib/format";
import { STALE_DAYS, excludedSentence, formatPercent, formatPrice, securityTypeLabel, trimDecimal } from "@/lib/investments";
import { notDrawnSentence, tileColour, treemapData, treemapFinding, treemapOption } from "@/lib/treemapChart";
import AccountMark from "@/components/AccountMark";
import Chart from "@/components/Chart";
import { Day } from "@/components/datetime";
import Dialog from "@/components/Dialog";
import { Checkbox } from "@/components/form";
import { ChevronRightIcon } from "@/components/icons";
import { QueryError, SkeletonRows } from "@/components/QueryStates";
import SegmentedControl, { segmentPanelId, segmentTabId, type Segment } from "@/components/SegmentedControl";
import SheetSelect, { type SheetOption } from "@/components/SheetSelect";
import { useChartTokens } from "@/theme/chartTokens";
import type { ChartBox } from "@/theme/chartInteraction";

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

/** How the same rows are shown: as a picture, or as the list alone (issue #34).
 *  The list is present in both — the treemap has no legend of its own, and §2.9
 *  requires a chart's text equivalent — so this is a switch about the chart, not
 *  about the figures. */
type AllocationView = "treemap" | "list";
const VIEWS: readonly Segment<AllocationView>[] = [
  { id: "treemap", label: "Treemap" },
  { id: "list", label: "List" },
];
const VIEW_OPTIONS: readonly SheetOption<AllocationView>[] = VIEWS.map((v) => ({
  id: v.id,
  label: String(v.label),
}));
const VIEW_TESTID = "allocation-view";

/** The treemap is a picture of several groups, so it is given a page-width card's
 *  height on every canvas: squarify splits a canvas's area between the tiles, and
 *  a short one would hand the smaller groups slivers instead of rectangles. */
const TREEMAP_HEIGHT = 300;

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
  const [view, setView] = useState<AllocationView>("treemap");
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
            segmented control, unchanged from the card this replaced. The view
            switch follows it: the same two shapes, one row down on a phone,
            because AGENTS.md's rule is about the *control* ("view switches on a
            phone are SheetSelect"), not about which switch it is. */}
        <div className="flex flex-wrap items-center gap-2">
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
          <div className="sm:hidden">
            <SheetSelect
              label="View"
              value={view}
              options={VIEW_OPTIONS}
              onChange={setView}
              testid={`${VIEW_TESTID}-sheet`}
            />
          </div>
          <div className="hidden sm:block">
            <SegmentedControl
              label="Show the allocation as"
              segments={VIEWS}
              value={view}
              onChange={setView}
              testid={VIEW_TESTID}
            />
          </div>
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
          <AllocationBody data={data} groupBy={groupBy} view={view} onSelect={setDetail} />
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
  view,
  onSelect,
}: {
  data: Allocation;
  groupBy: AllocationGroup;
  view: AllocationView;
  onSelect: (row: AllocationRow) => void;
}) {
  const ccy = data.base_currency;
  const excluded = excludedSentence(data.unpriced_positions, data.no_rate_positions);
  const t = useChartTokens();

  // Split once, at the top: the tiles, the rows that cannot be one, and the
  // swatch each tile is drawn in. The chart and the list are two renderings of
  // this one array, which is what keeps a row's colour and its figure agreeing.
  const tiles = useMemo(() => treemapData(data.rows), [data.rows]);
  const swatch = useMemo(
    () => new Map(tiles.tiles.map((r, i) => [r.key, tileColour(t, i)])),
    [tiles, t],
  );
  // The option is a function of the measured box (§2.9), so it is built per
  // canvas — memoised here as `Chart`'s contract asks, since a fresh object is a
  // whole option re-derived.
  const option = useCallback(
    (box: ChartBox): EChartsOption => treemapOption(tiles, t, ccy, box),
    [tiles, t, ccy],
  );
  const notDrawn = notDrawnSentence(tiles.skipped, ccy);
  const finding = treemapFinding(tiles, data.total_base, groupBy, formatDay(data.as_of), ccy);

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
      {/* The picture, when the treemap view is on and there is something with
          area to draw. It goes *above* the list rather than instead of it: the
          treemap answers "which group is big" at a glance and has no legend of
          its own, so the list beneath it is both the text equivalent §2.9
          requires and the chart's key. */}
      {view === "treemap" && (tiles.tiles.length > 0 || notDrawn) && (
        <div className="mb-4">
          {tiles.tiles.length > 0 && (
            <Chart
              option={option}
              label={finding}
              height={TREEMAP_HEIGHT}
              testid="allocation-treemap"
            />
          )}
          {/* Under the chart, not in the status block below, because it is a
              sentence about *this picture*: the rows it names are the ones with
              no area to draw. It is also inside the chart's own `aria-label`, so
              it is announced with the chart rather than only read by eye.

              Rendered whenever the treemap view is on rather than only when a
              picture was drawn: the case with *nothing* to draw is exactly the
              one where this sentence is the only thing on screen — a selected
              view with no picture in it and, without this, no reason given. */}
          {notDrawn && (
            <p
              className={`text-xs text-warning ${tiles.tiles.length > 0 ? "mt-1" : ""}`}
              data-testid="allocation-not-drawn"
            >
              {notDrawn}
            </p>
          )}
        </div>
      )}

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
              <div className="flex min-w-0 items-start gap-2">
                {/* A row's tile colour, beside the row: it is what makes the
                    chart's colours *mean* something — identity on a treemap is
                    otherwise carried by position alone, and a reader with a
                    greyscale screen or a colour-vision difference gets the name
                    and the figure out of the same list either way. Only drawn
                    when a picture is on screen to key it to; a row without a
                    tile (a short position) has no swatch. */}
                {view === "treemap" && swatch.get(r.key) && (
                  <span
                    aria-hidden="true"
                    className="mt-1 size-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: swatch.get(r.key) }}
                    data-testid={`allocation-swatch-${r.key}`}
                  />
                )}
                <div className="min-w-0">
                  <p className="text-sm">{groupLabel(groupBy, r.key, r.label)}</p>
                  {/* How many positions the line is made of: a 3% line that is
                      one holding and a 3% line that is thirty read very
                      differently. */}
                  <p className="text-xs text-fg-muted" data-testid={`allocation-holdings-${r.key}`}>
                    {r.holdings} {r.holdings === 1 ? "position" : "positions"}
                  </p>
                </div>
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
