import { useCallback, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import {
  useAccounts,
  useBalances,
  useCreateAccount,
  useDeleteAccount,
  useDeleteBalance,
  useNetWorth,
  useNetWorthSeries,
  useOwners,
  usePutBalance,
  useUpdateAccount,
} from "@/api/hooks";
import { downloadAccountCsv } from "@/api/portability";
import { useConnections } from "@/api/sync";
import { connectionName, isStalled } from "@/lib/bankFreshness";
import type { Account, AccountCreate, AccountType, Owner } from "@/api/types";
import { formatMoney, negateAmount } from "@/lib/format";
import { formatDay, isoDay, todayIso } from "@/lib/dates";
import { brushWindow, coverageNotes, measuredSpan, netWorthOption } from "@/lib/netWorthChart";
import { useChartTokens } from "@/theme/chartTokens";
import { brushEvents, type ChartBox } from "@/theme/chartInteraction";
import { Button, Checkbox, Field, Input, Select, Spinner, useFieldId, validAmount, validCurrency, requiredText } from "@/components/form";
import Chart from "@/components/Chart";
import Dialog from "@/components/Dialog";
import { Day } from "@/components/datetime";
import AccountMark from "@/components/AccountMark";
import OwnerAvatar from "@/components/OwnerAvatar";
import OwnerSelect from "@/components/OwnerSelect";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import InvestmentsView from "@/components/InvestmentsView";
import SegmentedControl, { segmentPanelId, segmentTabId, type Segment } from "@/components/SegmentedControl";
import { ChevronRightIcon, FilterIcon, PlusIcon } from "@/components/icons";

const TYPES: AccountType[] = ["depository", "credit", "investment", "loan", "other"];

/** What a person calls each type. "depository" is the ledger's word, not theirs. */
const TYPE_LABEL: Record<AccountType, string> = {
  depository: "Cash",
  credit: "Credit cards",
  investment: "Investments",
  loan: "Loans",
  other: "Other",
};

/*
 * A card or loan's balance is stored signed — debt is negative (ADR-0043) — and
 * read and typed here as the amount owed. These two are the only places the page
 * crosses between the two, so what a person enters for a card is never the
 * opposite of what the ledger means by it.
 */
const isLiability = (t: AccountType) => t === "credit" || t === "loan";
const shownBalance = (t: AccountType, signed: string) => (isLiability(t) ? negateAmount(signed) : signed);
const storedBalance = shownBalance;
// A balance date is a calendar day where the user is, so it comes from the local
// clock — ``toISOString()`` would roll it back a day for anyone east of UTC.
const today = () => todayIso();

/*
 * One page, laid out the way people read their money: the whole first, then
 * the parts. Net worth and its line sit at the top; tabs below narrow the list
 * to one kind of account. The Investments tab also carries the portfolio —
 * allocation and holdings — which used to be a separate view switch: a
 * portfolio is the investment accounts at a different depth, so it lives with
 * them. Tab ids keep their old names where they had one ("balances" is All,
 * "investments" is Investments) so a link or a test that named them still works.
 */
type TabId = "balances" | "cash" | "credit" | "investments" | "loans" | "other";
const TABS: { id: TabId; label: string; types: AccountType[] | null }[] = [
  { id: "balances", label: "All", types: null },
  { id: "cash", label: "Cash", types: ["depository"] },
  { id: "credit", label: "Credit cards", types: ["credit"] },
  { id: "investments", label: "Investments", types: ["investment"] },
  { id: "loans", label: "Loans", types: ["loan"] },
  { id: "other", label: "Other", types: ["other"] },
];
const VIEW_TESTID = "accounts-view";

/**
 * The hero chart's windows. Calendar-free on purpose: "the last three months"
 * is the question this chart answers, unlike the reports' calendar presets.
 *
 * The three short ones are the reader's ask — "more varied filters like last
 * week, last day etc." — because a day and a fortnight are the questions a
 * household actually asks of a flat line: did the card payment land, did the
 * paycheck arrive. Eight chips is the whole ladder and still one row on a
 * desktop, so it is a scale rather than a pile.
 *
 * Each row carries the words the change line uses as well as the window, because
 * those two are one fact. The headline said "over 3 months" from a string beside
 * the control that set the window; a row cannot state the wrong period for the
 * window it opens, and a new one cannot be added without saying how the change
 * over it is described.
 */
type RangeId = "1d" | "1w" | "2w" | "1m" | "3m" | "6m" | "1y" | "all";

/**
 * How far back the window opens, from the reader's own today.
 *
 * `days` for the short ranges, `months` for the rest: a fortnight is a count of
 * days, while "three months" has to mean the same day three months ago and not
 * ninety days — the reader who opens this on the 31st can see the difference.
 * `null` is all time, which no client can compute the start of: the server opens
 * it where the household's data does.
 */
type Lookback = { days: number } | { months: number } | null;

const RANGES: ReadonlyArray<{ id: RangeId; label: string; back: Lookback; words: string }> = [
  { id: "1d", label: "1D", back: { days: 1 }, words: "over the past day" },
  { id: "1w", label: "1W", back: { days: 7 }, words: "over the past week" },
  { id: "2w", label: "2W", back: { days: 14 }, words: "over the past 2 weeks" },
  { id: "1m", label: "1M", back: { months: 1 }, words: "this past month" },
  { id: "3m", label: "3M", back: { months: 3 }, words: "over 3 months" },
  { id: "6m", label: "6M", back: { months: 6 }, words: "over 6 months" },
  { id: "1y", label: "1Y", back: { months: 12 }, words: "over the year" },
  { id: "all", label: "All", back: null, words: "since you started" },
];

/** What the card opens on. A fortnight is the reader's ask: long enough to hold
 *  a rent payment and a paycheck, short enough to be about this month's money. */
const DEFAULT_RANGE: RangeId = "2w";

/**
 * The window this reader last chose, remembered.
 *
 * Per-viewer rather than per-household, like every other remembered choice: two
 * people looking at the same accounts may watch different windows, and neither
 * reading should overwrite the other's. Wrapped in try/catch — `localStorage`
 * throws in a private window, and the fallback is the window the card is built
 * around, so a throw costs a remembered preference rather than a broken card
 * (the shape `Allocations.tsx` uses for its include-cash switch).
 *
 * A stored value that is not one of today's ranges is ignored rather than
 * trusted: the ladder changes between releases, and a window from an older build
 * is one this card cannot draw.
 */
const RANGE_PREF_KEY = "metalmark-networth-range";

function readRangePref(): RangeId {
  try {
    const raw = localStorage.getItem(RANGE_PREF_KEY);
    return RANGES.some((r) => r.id === raw) ? (raw as RangeId) : DEFAULT_RANGE;
  } catch {
    return DEFAULT_RANGE;
  }
}

function writeRangePref(value: RangeId): void {
  try {
    localStorage.setItem(RANGE_PREF_KEY, value);
  } catch {
    /* Not fatal — the choice just will not survive a reload. */
  }
}

/** The day a window opens on, or `null` for all time. */
function rangeStart(back: Lookback): string | null {
  if (back === null) return null;
  const d = new Date();
  if ("days" in back) d.setDate(d.getDate() - back.days);
  else d.setMonth(d.getMonth() - back.months);
  return isoDay(d);
}

export default function Accounts() {
  const owners = useOwners();
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("balances");
  const [filtersOpen, setFiltersOpen] = useState(false);
  // The filter scopes the list and the header together: an owner's net worth is
  // the sum of that owner's accounts, so a filtered list with an unfiltered
  // total would read as a bug.
  const netWorth = useNetWorth(ownerFilter);
  const accounts = useAccounts(ownerFilter);
  const create = useCreateAccount();
  const connections = useConnections();
  const stalled = (connections.data ?? []).filter((c) => isStalled(c));
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  const ownerById = useMemo(() => {
    const m = new Map<string, Owner>();
    owners.data?.forEach((o) => m.set(o.id, o));
    return m;
  }, [owners.data]);

  const counts = useMemo(() => {
    const c = new Map<AccountType, number>();
    accounts.data?.forEach((a) => c.set(a.type, (c.get(a.type) ?? 0) + 1));
    return c;
  }, [accounts.data]);

  // A tab for a kind the household has none of is a door to an empty room.
  // All and Investments always show: All is the default, and Investments holds
  // the portfolio even before any account is typed as one.
  const segments: Segment<TabId>[] = TABS.filter(
    (t) => t.types === null || t.id === "investments" || t.types.some((ty) => counts.get(ty)),
  ).map((t) => ({ id: t.id, label: t.label }));
  const current = TABS.find((t) => t.id === tab) ?? TABS[0];
  const panelId = segmentPanelId(VIEW_TESTID, tab);

  return (
    // `max-w-6xl`: §9.1's middle width, the one a two-up grid needs to stop
    // being two stretched columns. `mx-auto` centres it in the wider shell.
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Accounts</h1>
        <div className="flex items-center gap-2">
          {/* On a phone the owner chips fold behind this button, top right, so
              the net worth is the first thing on screen. From `sm:` up there is
              room for them in line, and the button goes away. */}
          <Button
            variant="secondary"
            className="sm:hidden"
            aria-expanded={filtersOpen}
            aria-controls="accounts-filters"
            onClick={() => setFiltersOpen((v) => !v)}
            data-testid="accounts-filters-toggle"
          >
            <FilterIcon />
            <span className="sr-only">{ownerFilter ? "Filters (one owner selected)" : "Filters"}</span>
            {ownerFilter && (
              <svg viewBox="0 0 8 8" aria-hidden="true" className="size-2 text-accent">
                <circle cx="4" cy="4" r="4" fill="currentColor" />
              </svg>
            )}
          </Button>
          <Button onClick={() => setOpen((v) => !v)} aria-expanded={open} data-testid="add-account">
            <PlusIcon />
            Add account
          </Button>
        </div>
      </div>

      <div id="accounts-filters" className={filtersOpen ? "block" : "hidden sm:block"}>
        <OwnerFilterChips owners={owners.data ?? []} value={ownerFilter} onChange={setOwnerFilter} />
      </div>

      {open && (
        <AddAccountForm
          owners={owners.data ?? []}
          pending={create.isPending}
          error={create.isError ? (create.error as Error).message : null}
          onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
        />
      )}

      {stalled.length > 0 && (
        // Said where the numbers are, not only in Admin: a bridge that stops
        // sending new transactions looks, from here, like money gone missing.
        <p
          className="rounded-card bg-warning/15 px-4 py-3 text-sm text-warning-ink"
          role="status"
          data-testid="bank-stalled"
        >
          {stalled.length === 1
            ? `${connectionName(stalled[0], "A bank")} hasn’t sent new transactions for a while, though its syncs succeed.`
            : `${stalled.length} banks haven’t sent new transactions for a while, though their syncs succeed.`}{" "}
          Check the link at SimpleFIN Bridge; Admin has the details.
        </p>
      )}

      <NetWorthHero
        ownerFilter={ownerFilter}
        ownerLabel={ownerFilter ? (ownerName.get(ownerFilter) ?? "owner") : null}
        netWorth={netWorth.data}
      />

      <SegmentedControl
        label="Account type"
        segments={segments}
        value={tab}
        onChange={setTab}
        testid={VIEW_TESTID}
      />

      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={segmentTabId(VIEW_TESTID, tab)}
        data-testid={panelId}
        className="space-y-6"
      >
        <AccountGroups
          accounts={(accounts.data ?? []).filter(
            (a) => current.types === null || current.types.includes(a.type),
          )}
          loaded={accounts.data !== undefined}
          owners={ownerById}
          onOpen={setEditing}
          emptyLabel={current.types === null ? null : current.label.toLowerCase()}
        />
        {tab === "investments" && (
          <InvestmentsView
            ownerName={ownerFilter ? (ownerName.get(ownerFilter) ?? "that owner") : null}
          />
        )}
      </div>

      {editing && <EditAccountDialog account={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

/**
 * The first thing on the page: what the household is worth, and which way it
 * has been going. The figure is the one `text-3xl` on the screen (§2.4); the
 * change beside it carries its sign in a glyph and a word as well as a colour
 * (§6.2), because colour is never the only signal.
 */
function NetWorthHero({
  ownerFilter,
  ownerLabel,
  netWorth,
}: {
  ownerFilter: string | null;
  ownerLabel: string | null;
  netWorth: ReturnType<typeof useNetWorth>["data"];
}) {
  // The remembered window, or the default. Read once, at mount: a preference
  // that changed under the reader mid-session would move the ground of the chart
  // they are looking at.
  const [range, setRange] = useState<RangeId>(readRangePref);
  // Total by construction — `RangeId` is this table's own key set, and the table
  // is the only place one is defined — so the assertion cannot be wrong, and a
  // fallback row here would be a window no reader could reach or name.
  const row = RANGES.find((r) => r.id === range)!;
  const start = useMemo(() => rangeStart(row.back), [row]);
  const series = useNetWorthSeries(start, today(), ownerFilter);
  const t = useChartTokens();
  // A function of the box: this card is 260 px tall, of which the line keeps the
  // top ~140 px on a wide canvas — how many values the reader is asked to count
  // off its axis follows from that (`valueTicks`).
  //
  // This is the card that asks for the brush, because it is the one that can
  // state the window: a drag reports back through `brushEvents` below and the
  // headline above re-scopes to the same span. A card that cannot say which
  // days its figures cover does not draw one.
  const option = useCallback(
    (box: ChartBox): EChartsOption => netWorthOption(series.data, t, box, { brush: true }),
    [series.data, t],
  );

  const points = series.data?.points ?? [];
  // Which part of the range the chart is showing, as two points of the series.
  // The brush belongs to the wide layout — a phone canvas draws no strip and no
  // brush — so this stays `null` there and the headline keeps its own words.
  //
  // The window itself is ECharts' state, not React's: a drag sets it inside the
  // instance and reports it back through `brushEvents`. Clearing this therefore
  // does not move the window, and the chart would go on showing a span the
  // headline no longer states — so a range click clears both, and the chart is
  // re-mounted by the `key` below rather than re-optioned.
  const [brushed, setBrushed] = useState<[number, number] | null>(null);
  // Bumped only when there is a window to hand back: a range click with no brush
  // on screen re-uses the chart instance, as it always did.
  const [brushReset, setBrushReset] = useState(0);
  // A window belongs to the points it was drawn on: a new range or a new owner
  // filter is a different series, and the old indices would name different days.
  // Anything that no longer fits is dropped rather than clamped — the chart has
  // been rebuilt for the new range and shows the whole of it.
  const win = brushed && brushed[1] < points.length ? brushed : null;
  // Memoised: the wrapper re-binds its handlers when this prop's value changes.
  const onEvents = useMemo(
    () => brushEvents((zoom) => setBrushed(zoom ? brushWindow(points, zoom[0], zoom[1]) : null)),
    [points],
  );

  // The window on screen, as indices: the whole range, or the brushed part of it.
  const winFrom = win ? win[0] : 0;
  const winTo = win ? win[1] : points.length - 1;
  // The figure the headline may state, over the span the chart will draw it
  // over — `measuredSpan` is this chart's own rule (a change is only a change
  // where both ends count the same accounts) applied to a span instead of one
  // interval. On a window whose first points count fewer accounts than its last
  // — the household's history being younger than the window — the span is the
  // part the chart can measure, and the words below name that part rather than
  // the window: a headline reading "up $X over the year" while the bars beneath
  // it sum to something else is the card contradicting itself.
  const [firstIdx, lastIdx] = measuredSpan(points, winFrom, winTo);
  const first = points[firstIdx];
  const last = points[lastIdx];
  const change = first && last ? Number(last.net_worth) - Number(first.net_worth) : null;
  const ccy = netWorth?.base_currency ?? series.data?.base_currency ?? "USD";
  // What the change is *over*, said in the window's own words when the figure
  // does cover the whole window. A brush narrows the line to part of the range,
  // and a headline that still said "over 2 weeks" would be claiming a figure
  // the chart above it is not showing — so the brush names its two days and the
  // range states the period its row was built with. A figure narrower than
  // either names its own two days, in the same words, because it is the same
  // kind of statement; a window with no drawable interval at all names the one
  // day it has.
  const spanWords =
    firstIdx < 0
      ? // An empty series: no points, so no span, and the change line is not
        // drawn either. `measuredSpan` reports `[-1, -1]` for it.
        row.words
      : firstIdx === winFrom && lastIdx === winTo
        ? win
          ? `from ${formatDay(points[win[0]].date)} to ${formatDay(points[win[1]].date)}`
          : row.words
        : firstIdx === lastIdx
          ? `on ${formatDay(points[lastIdx].date)}`
          : `from ${formatDay(points[firstIdx].date)} to ${formatDay(points[lastIdx].date)}`;
  // What the line cannot count, in the same sentences the Insights card prints
  // under its own: an account counted partway through, or one the ledger cannot
  // value. The chart marks both on the canvas and withholds the bar it cannot
  // draw, but a mark is silent at rest — ADR-0045 wants the words as well, and
  // they belong to whichever span is on screen, so a brushed window gets its own.
  const notes = useMemo(
    () => coverageNotes(win ? points.slice(win[0], win[1] + 1) : points),
    [points, win],
  );

  return (
    <section className="rounded-card bg-surface-raised p-4 sm:p-6" data-testid="net-worth" aria-label="Net worth">
      <p className="text-sm text-fg-muted">
        Net worth
        {ownerLabel && ` · ${ownerLabel}`}
      </p>
      <p className="text-3xl font-semibold">
        {netWorth ? formatMoney(netWorth.net_worth, netWorth.base_currency) : "—"}
      </p>
      {change !== null && points.length > 1 && (
        <p
          className={`mt-1 text-sm font-medium ${change >= 0 ? "text-positive" : "text-negative"}`}
          data-testid="net-worth-change"
        >
          <span aria-hidden="true">{change >= 0 ? "▲ " : "▼ "}</span>
          {change >= 0 ? "Up " : "Down "}
          {formatMoney(Math.abs(change), ccy)} {spanWords}
        </p>
      )}

      <div className="mt-4">
        {points.length > 1 ? (
          <Chart
            key={brushReset}
            option={option}
            onEvents={onEvents}
            height={260}
            testid="accounts-net-worth-chart"
            label={
              change === null
                ? "Net worth over time"
                : `Net worth went ${change >= 0 ? "up" : "down"} ${formatMoney(Math.abs(change), ccy)} ${spanWords}, to ${formatMoney(last.net_worth, ccy)}.`
            }
          />
        ) : series.isLoading ? (
          <div className="flex h-64 items-center justify-center text-sm text-fg-muted">
            <Spinner /> Loading your history
          </div>
        ) : (
          <p className="flex h-24 items-center justify-center text-center text-sm text-fg-muted">
            Your net worth line starts once an account has two balances. Add a past balance from any
            account to fill it in.
          </p>
        )}
      </div>

      {points.length > 1 && notes.length > 0 && (
        <div className="mt-2 space-y-1 text-xs text-fg-muted" data-testid="accounts-net-worth-coverage">
          {notes.map((note) => (
            <p key={note}>
              <span aria-hidden="true">⚠ </span>
              {note}
            </p>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-4 text-sm text-fg-muted">
          {netWorth && (
            <>
              <span>Assets {formatMoney(netWorth.assets, netWorth.base_currency)}</span>
              <span>Liabilities {formatMoney(netWorth.liabilities, netWorth.base_currency)}</span>
            </>
          )}
        </div>
        {/* Eight chips, so they wrap on a phone rather than spill: `flex-wrap`
            and a right edge, because the row lives at the end of a row that
            already holds the assets (§5 — nothing scrolls sideways, and chips
            wrap). */}
        <div className="flex flex-wrap justify-end gap-1" role="group" aria-label="Chart range">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-pressed={range === r.id}
              // A range control states a span, so it takes the brush's own span
              // away: the window is cleared, and with it the chart instance that
              // holds it. A new range does both by itself — its option is rebuilt
              // — but clicking the range already selected has to do it here.
              onClick={() => {
                if (brushed) {
                  setBrushed(null);
                  setBrushReset((n) => n + 1);
                }
                setRange(r.id);
                // Written on the press, not on the way out: a reader who picks a
                // window and closes the tab has still chosen it.
                writeRangePref(r.id);
              }}
              className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-control px-2 text-sm ${
                range === r.id ? "bg-accent/20 font-medium text-accent-ink" : "text-fg-muted hover:text-fg"
              }`}
              data-testid={`net-worth-range-${r.id}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {netWorth && netWorth.unconverted_currencies.length > 0 && (
        // The words carry the warning; role="status" is what makes it heard
        // when the query lands and a rate turns out to be missing (§6.4).
        <p className="mt-2 text-sm text-warning" role="status" data-testid="no-rate-warning">
          No FX rate for: {netWorth.unconverted_currencies.join(", ")}
        </p>
      )}
    </section>
  );
}

/** Accounts grouped by kind, each group with its own total, as a bank app does. */
function AccountGroups({
  accounts,
  loaded,
  owners,
  onOpen,
  emptyLabel,
}: {
  accounts: Account[];
  loaded: boolean;
  owners: Map<string, Owner>;
  onOpen: (a: Account) => void;
  emptyLabel: string | null;
}) {
  if (loaded && accounts.length === 0) {
    return (
      <ul className="rounded-card bg-surface-raised" data-testid="account-list">
        <li className="px-4 py-8 text-center text-sm text-fg-muted">
          {emptyLabel
            ? `No ${emptyLabel} accounts yet. Retype an account from its details, or add one.`
            : "No accounts yet. Add one, or connect a bank in Settings."}
        </li>
      </ul>
    );
  }
  const groups = TYPES.map((type) => ({
    type,
    rows: accounts.filter((a) => a.type === type),
  })).filter((g) => g.rows.length > 0);

  return (
    // §9.3's card grid for Accounts, which this page did not have: with no `lg:`
    // class anywhere, the whole page was one 1152 px column at desktop, and a
    // list row that names an account, its owner and its balance has no use for
    // 1152 px. One group per column, two up — the group heading and its total
    // stay attached to the rows they are about, which is the thing a single
    // flat grid of accounts would lose.
    //
    // `items-start` rather than the default stretch: a group is as tall as the
    // accounts in it, and a household with three depository accounts and one
    // loan should see a short card, not a card padded out to match its
    // neighbour.
    <div
      className="space-y-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0"
      data-testid="account-list"
    >
      {groups.map(({ type, rows }) => {
        // A group total only when every row is in one currency: adding rupees to
        // dollars would be a number that is not any amount of money (§6.5).
        const ccys = new Set(rows.map((r) => r.currency));
        const total =
          ccys.size === 1
            ? rows.reduce((sum, r) => sum + Number(r.is_hidden ? 0 : r.current_balance), 0)
            : null;
        const ccy = rows[0].currency;
        return (
          <section key={type} aria-labelledby={`account-group-${type}`}>
            <div className="mb-2 flex items-baseline justify-between px-1">
              <h2 id={`account-group-${type}`} className="text-base font-medium">
                {TYPE_LABEL[type]}
              </h2>
              {total !== null && (
                <span className="text-base font-semibold" data-testid={`account-group-total-${type}`}>
                  {isLiability(type)
                    ? `${formatMoney(Math.abs(total), ccy)} ${total <= 0 ? "owed" : "credit"}`
                    : formatMoney(total, ccy)}
                </span>
              )}
            </div>
            <ul className="divide-y divide-border rounded-card bg-surface-raised">
              {rows.map((a) => (
                <li key={a.id}>
                  {/* The whole row opens the account: a 44 px target the width of
                      the screen, not a small "Edit" button at its end. */}
                  <button
                    type="button"
                    onClick={() => onOpen(a)}
                    className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-inset focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                    data-testid={`account-edit-${a.id}`}
                    aria-label={`${a.name}, open details`}
                  >
                    {/* The institution is the mark (its logo, or initials with
                        the institution in the tooltip) and the owner is the
                        avatar: the row carries one line of words, the name.
                        Institution, currency and type are in the details. */}
                    <AccountMark name={a.name} institution={a.institution} size="lg" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">
                        {a.name}
                        {a.is_hidden && <span className="ml-2 text-xs text-fg-muted">(hidden)</span>}
                      </span>
                      {a.stale_since && (
                        // The bank stopped reporting it; its last balance is still
                        // counted. The one fact worth a second line.
                        <span
                          className="mt-0.5 inline-block rounded bg-warning/20 px-1.5 py-0.5 text-xs text-warning-ink"
                          data-testid={`account-stale-${a.id}`}
                        >
                          Not reported since <Day value={a.stale_since} />
                        </span>
                      )}
                    </span>
                    <OwnerAvatar owner={owners.get(a.owner_id)} testid={`account-owner-${a.id}`} />
                    <span
                      className={`text-base font-semibold ${a.is_asset ? "text-fg" : "text-negative"}`}
                      data-testid={`account-balance-${a.id}`}
                    >
                      {isLiability(a.type) && Number(a.current_balance) > 0
                        ? `${formatMoney(a.current_balance, a.currency)} credit`
                        : isLiability(a.type)
                          ? `${formatMoney(shownBalance(a.type, a.current_balance), a.currency)} owed`
                          : formatMoney(a.current_balance, a.currency)}
                    </span>
                    <ChevronRightIcon className="size-4 shrink-0 text-fg-muted" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function AddAccountForm({
  owners,
  pending,
  error,
  onSubmit,
}: {
  owners: Owner[];
  pending: boolean;
  error: string | null;
  onSubmit: (b: AccountCreate) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("depository");
  const [currency, setCurrency] = useState("USD");
  // Blank, not "0": an opening balance is an observation only when someone gives
  // one, and a zero nobody typed would put a $0 point on the net-worth chart the
  // day the account was created (an account opened for an imported statement
  // takes its balance from the file).
  const [balance, setBalance] = useState("");
  const [balanceDate, setBalanceDate] = useState(today);
  const [institution, setInstitution] = useState("");
  const [owner, setOwner] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const sharedId = owners.find((o) => o.kind === "shared")?.id;

  const ids = {
    name: useFieldId("account-name"),
    type: useFieldId("account-type"),
    currency: useFieldId("account-currency"),
    balance: useFieldId("account-balance"),
    balanceDate: useFieldId("account-balance-date"),
    institution: useFieldId("account-institution"),
  };

  const validate = () => {
    const e = {
      name: requiredText(name),
      currency: validCurrency(currency),
      balance: balance.trim() ? validAmount(balance) : null,
    };
    setErrs(e);
    return !e.name && !e.currency && !e.balance;
  };

  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-card bg-surface-raised p-4 sm:grid-cols-2"
      data-testid="add-account-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validate()) return;
        onSubmit({
          name,
          type,
          currency: currency.toUpperCase(),
          ...(balance.trim()
            ? { current_balance: storedBalance(type, balance), balance_date: balanceDate }
            : {}),
          // Shared is the server's default too, so an unset picker (owners still
          // loading) can just be left off the body.
          owner_id: owner || sharedId || undefined,
          institution: institution || null,
        });
      }}
    >
      <Field label="Name" htmlFor={ids.name} required error={errs.name} className="sm:col-span-2">
        <Input id={ids.name} value={name} onChange={(e) => setName(e.target.value)} data-testid="account-name" />
      </Field>
      <Field label="Type" htmlFor={ids.type}>
        <Select id={ids.type} value={type} onChange={(e) => setType(e.target.value as AccountType)} data-testid="account-type">
          {TYPES.map((t) => (
            <option key={t} value={t}>{TYPE_LABEL[t]}</option>
          ))}
        </Select>
      </Field>
      <Field label="Currency" htmlFor={ids.currency} required error={errs.currency}>
        <Input id={ids.currency} value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value)} data-testid="account-currency" />
      </Field>
      <Field
        label={isLiability(type) ? "Amount owed" : "Starting balance"}
        htmlFor={ids.balance}
        error={errs.balance}
        hint="Leave blank to take it from an imported statement."
      >
        <Input id={ids.balance} value={balance} inputMode="decimal" onChange={(e) => setBalance(e.target.value)} data-testid="account-balance" />
      </Field>
      <Field label="Balance date" htmlFor={ids.balanceDate}>
        <Input id={ids.balanceDate} type="date" value={balanceDate} onChange={(e) => setBalanceDate(e.target.value)} data-testid="account-balance-date" />
      </Field>
      <OwnerSelect value={owner} onChange={setOwner} testid="account-owner" />
      <Field label="Institution" htmlFor={ids.institution}>
        <Input id={ids.institution} value={institution} onChange={(e) => setInstitution(e.target.value)} data-testid="account-institution" />
      </Field>
      {/* role="alert": a failed save is announced without moving focus (WCAG 4.1.3). */}
      {error && <p className="text-sm text-negative sm:col-span-2" role="alert" data-testid="account-form-error">{error}</p>}
      <Button type="submit" disabled={pending} className="sm:col-span-2" data-testid="account-save">
        Save
      </Button>
    </form>
  );
}

function EditAccountDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const update = useUpdateAccount();
  const del = useDeleteAccount();
  const exportCsv = useMutation({ mutationFn: downloadAccountCsv });
  const [name, setName] = useState(account.name);
  const [institution, setInstitution] = useState(account.institution ?? "");
  const [type, setType] = useState<AccountType>(account.type);
  const [balance, setBalance] = useState(shownBalance(account.type, account.current_balance));
  // Today, not the account's last balance date: a balance typed here is what the
  // account holds *now*, and sending the old date back overwrote that day's point
  // in the net-worth history. Picking an earlier date is how a past balance is
  // corrected — the server files it there without moving the current one.
  const [balanceDate, setBalanceDate] = useState(today());
  const [owner, setOwner] = useState<string | null>(account.owner_id);
  const [hidden, setHidden] = useState(account.is_hidden);
  const [confirmDel, setConfirmDel] = useState(false);
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    name: useFieldId("edit-account-name"),
    institution: useFieldId("edit-account-institution"),
    balance: useFieldId("edit-account-balance"),
    type: useFieldId("edit-account-type"),
    balanceDate: useFieldId("edit-account-balance-date"),
  };

  const save = () => {
    const e = { name: requiredText(name), balance: validAmount(balance) };
    setErrs(e);
    if (e.name || e.balance) return;
    update.mutate(
      {
        id: account.id,
        body: {
          name,
          institution: institution || null,
          ...(type !== account.type ? { type } : {}),
          // Only when the reader changed one of them: a rename is not a balance.
          ...(storedBalance(type, balance) !== account.current_balance || balanceDate !== today()
            ? { current_balance: storedBalance(type, balance), balance_date: balanceDate }
            : {}),
          // Required server-side: keep the account's own owner if the picker is
          // somehow empty rather than sending null.
          owner_id: owner || account.owner_id,
          is_hidden: hidden,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={account.name}
      testid="edit-account-dialog"
      footer={
        <>
          <Button
            variant="danger"
            onClick={() => setConfirmDel(true)}
            data-testid="account-delete"
          >
            Delete
          </Button>
          <div className="flex-1" />
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={update.isPending} data-testid="edit-account-save">Save</Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Name" htmlFor={ids.name} required error={errs.name}>
          <Input id={ids.name} value={name} onChange={(e) => setName(e.target.value)} data-testid="edit-account-name" />
        </Field>
        <Field label="Institution" htmlFor={ids.institution}>
          <Input id={ids.institution} value={institution} onChange={(e) => setInstitution(e.target.value)} data-testid="edit-account-institution" />
        </Field>
        <Field label="Type" htmlFor={ids.type}>
          <Select
            id={ids.type}
            value={type}
            onChange={(e) => {
              const next = e.target.value as AccountType;
              // The field shows what the account means by its balance, so crossing
              // between asset and liability re-reads the same stored number.
              if (isLiability(next) !== isLiability(type)) setBalance((b) => negateAmount(b));
              setType(next);
            }}
            data-testid="edit-account-type"
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>{TYPE_LABEL[t]}</option>
            ))}
          </Select>
        </Field>
        <Field
          label={isLiability(type) ? "Amount owed" : "Balance"}
          htmlFor={ids.balance}
          required
          error={errs.balance}
          hint={`In ${account.currency}`}
        >
          <Input id={ids.balance} value={balance} inputMode="decimal" onChange={(e) => setBalance(e.target.value)} data-testid="edit-account-balance" />
        </Field>
        <Field
          label="Balance date"
          htmlFor={ids.balanceDate}
          hint={
            account.balance_date
              ? `Current balance is as of ${account.balance_date}. An earlier date corrects that day's balance.`
              : undefined
          }
        >
          <Input id={ids.balanceDate} type="date" value={balanceDate} onChange={(e) => setBalanceDate(e.target.value)} data-testid="edit-account-balance-date" />
        </Field>
        <OwnerSelect value={owner} onChange={setOwner} testid="edit-account-owner" />
        <Checkbox
          label="Hidden"
          hint="Exclude from net worth views."
          checked={hidden}
          onChange={(e) => setHidden(e.target.checked)}
          data-testid="edit-account-hidden"
        />
        {update.isError && (
          <p className="text-sm text-negative" role="alert">
            {(update.error as Error).message}
          </p>
        )}

        <BalanceHistory account={account} />

        {/* Here rather than in Settings → Data, because this is where someone
            asks the question. The Settings tab can export one account too, but
            nobody looks for this account's file in a list of everybody's
            settings. The two call the same route. */}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            disabled={exportCsv.isPending}
            aria-busy={exportCsv.isPending}
            onClick={() => exportCsv.mutate(account.id)}
            data-testid="account-export-csv"
          >
            {exportCsv.isPending && <Spinner />}
            Export CSV
          </Button>
          <span className="text-xs text-fg-muted">
            This account's transactions, in a file the CSV importer can read back.
          </span>
        </div>
        {exportCsv.isError && (
          <p className="text-sm text-negative" role="alert" data-testid="account-export-error">
            {(exportCsv.error as Error).message}
          </p>
        )}

        {confirmDel && (
          <div className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm" data-testid="account-delete-confirm">
            <p className="text-negative">Delete “{account.name}” and its transactions? This cannot be undone.</p>
            <div className="mt-2 flex gap-2">
              <Button variant="secondary" onClick={() => setConfirmDel(false)}>Keep</Button>
              <Button
                variant="danger"
                disabled={del.isPending}
                onClick={() => del.mutate(account.id, { onSuccess: onClose })}
                data-testid="account-delete-confirm-btn"
              >
                Yes, delete
              </Button>
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/**
 * The balances behind this account's line on the net-worth chart, and a way to
 * add the ones the bank never sent — a statement from before the connection, a
 * year-end figure. A past day is history: adding one never moves the current
 * balance (the server files it through the same rule as every other writer).
 */
function BalanceHistory({ account }: { account: Account }) {
  const history = useBalances(account.id);
  const put = usePutBalance();
  const del = useDeleteBalance();
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const ids = { date: useFieldId("history-date"), amount: useFieldId("history-amount") };
  const owed = isLiability(account.type);

  const add = () => {
    const e = !date ? "Pick the day this balance was on." : validAmount(amount);
    setErr(e);
    if (e) return;
    put.mutate(
      { accountId: account.id, date, balance: storedBalance(account.type, amount) },
      {
        onSuccess: () => {
          setDate("");
          setAmount("");
        },
      },
    );
  };

  const rows = history.data ?? [];

  return (
    <section className="space-y-3 border-t border-border pt-3" data-testid="balance-history" aria-labelledby="balance-history-heading">
      <div>
        <h3 id="balance-history-heading" className="text-base font-medium">Balance history</h3>
        <p className="text-sm text-fg-muted">
          Add balances from old statements to fill in your net worth before this account was connected.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Field label="Date" htmlFor={ids.date}>
          <Input
            id={ids.date}
            type="date"
            max={today()}
            value={date}
            onChange={(e) => setDate(e.target.value)}
            data-testid="history-date"
          />
        </Field>
        <Field label={owed ? "Amount owed" : "Balance"} htmlFor={ids.amount} hint={`In ${account.currency}`}>
          <Input
            id={ids.amount}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            data-testid="history-amount"
          />
        </Field>
        <Button onClick={add} disabled={put.isPending} aria-busy={put.isPending} data-testid="history-add">
          {put.isPending && <Spinner />}
          Save balance
        </Button>
      </div>
      {(err || put.isError) && (
        <p className="text-sm text-negative" role="alert" data-testid="history-error">
          {err ?? (put.error as Error).message}
        </p>
      )}

      {history.isLoading ? (
        <p className="text-sm text-fg-muted"><Spinner /> Loading balances</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-fg-muted" data-testid="history-empty">No balances recorded yet.</p>
      ) : (
        <ul className="max-h-64 divide-y divide-border overflow-y-auto rounded-control bg-surface-inset" data-testid="history-list">
          {rows.map((r) => (
            <li key={r.balance_date} className="flex min-h-11 items-center gap-3 px-3 py-2" data-testid={`history-row-${r.balance_date}`}>
              <span className="flex-1 text-sm"><Day value={r.balance_date} /></span>
              <span className="text-base font-semibold">
                {formatMoney(shownBalance(account.type, r.balance), r.currency)}
                {owed && <span className="ml-1 text-xs font-normal text-fg-muted">owed</span>}
              </span>
              {confirming === r.balance_date ? (
                <span className="flex gap-1">
                  <Button variant="secondary" className="px-2" onClick={() => setConfirming(null)}>Keep</Button>
                  <Button
                    variant="danger"
                    className="px-2"
                    disabled={del.isPending}
                    onClick={() =>
                      del.mutate(
                        { accountId: account.id, date: r.balance_date },
                        { onSuccess: () => setConfirming(null) },
                      )
                    }
                    data-testid={`history-remove-confirm-${r.balance_date}`}
                  >
                    Remove
                  </Button>
                </span>
              ) : (
                <span className="flex gap-1">
                  <Button
                    variant="ghost"
                    className="px-2"
                    onClick={() => {
                      setDate(r.balance_date);
                      setAmount(shownBalance(account.type, r.balance));
                    }}
                    aria-label={`Correct the balance on ${r.balance_date}`}
                    data-testid={`history-edit-${r.balance_date}`}
                  >
                    Correct
                  </Button>
                  <Button
                    variant="ghost"
                    className="px-2"
                    onClick={() => setConfirming(r.balance_date)}
                    aria-label={`Remove the balance on ${r.balance_date}`}
                    data-testid={`history-remove-${r.balance_date}`}
                  >
                    Remove
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {del.isError && (
        <p className="text-sm text-negative" role="alert">{(del.error as Error).message}</p>
      )}
    </section>
  );
}
