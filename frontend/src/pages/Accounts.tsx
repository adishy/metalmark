import { useMemo, useState } from "react";
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
import { isStalled } from "@/lib/bankFreshness";
import type { Account, AccountCreate, AccountType, Owner } from "@/api/types";
import { formatMoney, negateAmount } from "@/lib/format";
import { isoDay, todayIso } from "@/lib/dates";
import { netWorthOption } from "@/lib/netWorthChart";
import { useChartTokens } from "@/theme/chartTokens";
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

/** The hero chart's windows. Calendar-free on purpose: "the last three months"
 *  is the question this chart answers, unlike the reports' calendar presets. */
type RangeId = "1m" | "3m" | "6m" | "1y" | "all";
const RANGES: { id: RangeId; label: string; months: number | null }[] = [
  { id: "1m", label: "1M", months: 1 },
  { id: "3m", label: "3M", months: 3 },
  { id: "6m", label: "6M", months: 6 },
  { id: "1y", label: "1Y", months: 12 },
  { id: "all", label: "All", months: null },
];

function rangeStart(months: number | null): string | null {
  if (months === null) return null;
  const d = new Date();
  d.setMonth(d.getMonth() - months);
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
            ? `${stalled[0].org_name ?? "A bank"} hasn’t sent new transactions for a while, though its syncs succeed.`
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
  const [range, setRange] = useState<RangeId>("3m");
  const months = RANGES.find((r) => r.id === range)?.months ?? null;
  const start = useMemo(() => rangeStart(months), [months]);
  const series = useNetWorthSeries(start, today(), ownerFilter);
  const t = useChartTokens();
  const option: EChartsOption = useMemo(() => netWorthOption(series.data, t), [series.data, t]);

  const points = series.data?.points ?? [];
  const first = points[0];
  const last = points[points.length - 1];
  const change = first && last ? Number(last.net_worth) - Number(first.net_worth) : null;
  const ccy = netWorth?.base_currency ?? series.data?.base_currency ?? "USD";
  const rangeWords: Record<RangeId, string> = {
    "1m": "this past month",
    "3m": "over 3 months",
    "6m": "over 6 months",
    "1y": "over the year",
    all: "since you started",
  };

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
          {formatMoney(Math.abs(change), ccy)} {rangeWords[range]}
        </p>
      )}

      <div className="mt-4">
        {points.length > 1 ? (
          <Chart
            option={option}
            height={220}
            testid="accounts-net-worth-chart"
            label={
              change === null
                ? "Net worth over time"
                : `Net worth went ${change >= 0 ? "up" : "down"} ${formatMoney(Math.abs(change), ccy)} ${rangeWords[range]}, to ${formatMoney(last.net_worth, ccy)}.`
            }
          />
        ) : series.isLoading ? (
          <div className="flex h-56 items-center justify-center text-sm text-fg-muted">
            <Spinner /> Loading your history
          </div>
        ) : (
          <p className="flex h-24 items-center justify-center text-center text-sm text-fg-muted">
            Your net worth line starts once an account has two balances. Add a past balance from any
            account to fill it in.
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-4 text-sm text-fg-muted">
          {netWorth && (
            <>
              <span>Assets {formatMoney(netWorth.assets, netWorth.base_currency)}</span>
              <span>Liabilities {formatMoney(netWorth.liabilities, netWorth.base_currency)}</span>
            </>
          )}
        </div>
        <div className="flex gap-1" role="group" aria-label="Chart range">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-pressed={range === r.id}
              onClick={() => setRange(r.id)}
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
    <div className="space-y-6" data-testid="account-list">
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
