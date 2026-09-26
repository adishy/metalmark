// Insights → Recurring (ADR-0053).
//
// Two halves, and the difference between them is the feature: the **list** is
// what the household has said happens regularly — theirs to add, rename, pause
// and delete — and the **suggestions** are what its own transactions already
// look like they repeat, recomputed on every read and gone the moment a series
// covers them. Nothing here writes an opinion about the future into the ledger:
// a series describes transactions, it never owns them, so deleting one leaves
// every charge where it was.
//
// The occurrence count each row shows is the honest part of that: it is derived
// from the ledger on every read, so a series that has stopped matching anything
// says "0 seen" rather than claiming a subscription that was cancelled months
// ago.
import { useMemo, useState } from "react";
import { useAccounts, useCategories, useCategoryGroups } from "@/api/hooks";
import {
  useCreateRecurring,
  useDeleteRecurring,
  useRecurring,
  useRecurringSuggestions,
  useUpdateRecurring,
} from "@/api/recurring";
import type {
  Account,
  Cadence,
  Category,
  RecurringFilter,
  RecurringSeries,
  RecurringSuggestion,
  RecurringTotals,
} from "@/api/types";
import CategoryPicker, { categoryLabel } from "@/components/CategoryPicker";
import Dialog from "@/components/Dialog";
import { Day } from "@/components/datetime";
import {
  Button,
  Checkbox,
  Field,
  Input,
  Select,
  Spinner,
  useFieldId,
  validAmount,
} from "@/components/form";
import { SearchIcon } from "@/components/icons";
import { QueryError, SkeletonRows } from "@/components/QueryStates";
import SheetSelect from "@/components/SheetSelect";
import { formatMoney } from "@/lib/format";

/** How often something is expected. The words a person uses, not the wire
 *  values — "semimonthly" is a payroll term, "twice a month" is what a bill
 *  is (see the backend's own note on the two cadences a paycheque cannot keep). */
const CADENCES: { value: Cadence; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "biweekly", label: "Every 2 weeks" },
  { value: "semimonthly", label: "Twice a month" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "semiannual", label: "Twice a year" },
  { value: "annual", label: "Yearly" },
];

const CADENCE_LABEL = Object.fromEntries(CADENCES.map((c) => [c.value, c.label])) as Record<
  Cadence,
  string
>;

const ALL = "all";

type Kind = "all" | "out" | "in";
type Status = "all" | "active" | "paused";

const KIND_OPTIONS = [
  { id: "all", label: "Any kind" },
  { id: "out", label: "Money out" },
  { id: "in", label: "Money in" },
] as const;

const STATUS_OPTIONS = [
  { id: "all", label: "Any" },
  { id: "active", label: "Active" },
  { id: "paused", label: "Paused" },
] as const;

export default function Recurring() {
  const [accountId, setAccountId] = useState<string>(ALL);
  const [categoryId, setCategoryId] = useState<string>(ALL);
  const [kind, setKind] = useState<Kind>("all");
  const [status, setStatus] = useState<Status>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<RecurringSeries | "new" | null>(null);
  const [seeding, setSeeding] = useState<RecurringSuggestion | null>(null);

  const accounts = useAccounts();
  const categories = useCategories();

  // Every control here narrows the same request rather than filtering a cached
  // list: `q` is matched by the server against the name and the merchant text,
  // and the totals below are the filtered set's — a total that ignored the
  // filter would be the one number on the page nobody could explain.
  const filter: RecurringFilter = {
    account_id: accountId === ALL ? null : accountId,
    category_id: categoryId === ALL ? null : categoryId,
    is_active: status === "all" ? null : status === "active",
    direction: kind === "all" ? null : kind,
    q: search.trim() || null,
  };
  const filtered = accountId !== ALL || categoryId !== ALL || kind !== "all"
    || status !== "all" || search.trim() !== "";

  const list = useRecurring(filter);
  const suggestions = useRecurringSuggestions();

  const accountOptions = useMemo(
    () => [
      { id: ALL, label: "All accounts" },
      ...(accounts.data ?? []).map((a) => ({ id: a.id, label: a.name })),
    ],
    [accounts.data],
  );
  const categoryOptions = useMemo(
    () => [
      { id: ALL, label: "All categories" },
      ...(categories.data ?? []).map((c) => ({ id: c.id, label: categoryLabel(c) })),
    ],
    [categories.data],
  );

  const openNew = () => {
    setSeeding(null);
    setEditing("new");
  };
  const openFromSuggestion = (s: RecurringSuggestion) => {
    setSeeding(s);
    setEditing("new");
  };

  const money = list.data?.totals ?? [];
  const items = list.data?.items ?? [];

  return (
    <div className="space-y-4">
      {money.length > 0 && <TotalsStrip totals={money} />}

      {/* The ledger's own answer, above the list because it is the thing a
          person who has never opened this tab came for. Deliberately hidden
          while a filter is on: these are the whole ledger's patterns, and
          showing them beside a narrowed list would read as if they matched it. */}
      {!filtered && suggestions.data && suggestions.data.length > 0 && (
        <section
          className="space-y-2 rounded-card bg-surface-raised p-4"
          data-testid="recurring-suggestions"
        >
          <h2 className="text-sm font-semibold text-fg">Looks like it repeats</h2>
          <p className="text-sm text-fg-muted">
            Charges this household has made on a regular interval. Nothing is tracked until you
            say so, and anything you already track is not offered again.
          </p>
          <ul className="divide-y divide-border">
            {suggestions.data.map((s) => (
              <SuggestionRow key={s.transaction_id} suggestion={s} onPick={() => openFromSuggestion(s)} />
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative block min-w-0 flex-1 sm:max-w-xs">
          <span className="sr-only">Search recurring</span>
          <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-5 -translate-y-1/2 text-fg-muted" />
          <Input
            type="search"
            placeholder="Search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
            data-testid="recurring-search"
          />
        </label>
        <SheetSelect
          label="Account"
          value={accountId}
          options={accountOptions}
          onChange={setAccountId}
          testid="recurring-account"
        />
        <SheetSelect
          label="Category"
          value={categoryId}
          options={categoryOptions}
          onChange={setCategoryId}
          testid="recurring-category"
        />
        <SheetSelect
          label="Kind"
          value={kind}
          options={KIND_OPTIONS}
          onChange={(v) => setKind(v as Kind)}
          testid="recurring-kind"
        />
        <SheetSelect
          label="Show"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(v) => setStatus(v as Status)}
          testid="recurring-status"
        />
        <Button className="ml-auto" onClick={openNew} data-testid="add-recurring">
          Add
        </Button>
      </div>

      {list.isPending && <SkeletonRows rows={4} what="your recurring" testid="recurring-loading" />}
      {list.isError && (
        <QueryError
          what="your recurring transactions"
          error={list.error}
          onRetry={() => list.refetch()}
          testid="recurring-error"
        />
      )}
      {list.data && items.length === 0 && (
        <div className="rounded-card bg-surface-raised p-6 text-center" data-testid="recurring-empty">
          <p className="text-sm text-fg-muted">
            {filtered
              ? "Nothing matches these filters."
              : "Nothing tracked yet. Add the bills and subscriptions you expect, and they will be counted here."}
          </p>
        </div>
      )}
      {items.length > 0 && (
        <ul
          className="divide-y divide-border rounded-card bg-surface-raised"
          data-testid="recurring-list"
        >
          {items.map((s) => (
            <SeriesRow
              key={s.id}
              series={s}
              accountName={(accounts.data ?? []).find((a) => a.id === s.account_id)?.name ?? null}
              category={(categories.data ?? []).find((c) => c.id === s.category_id)}
              onEdit={() => {
                setSeeding(null);
                setEditing(s);
              }}
            />
          ))}
        </ul>
      )}

      {editing !== null && (
        <SeriesDialog
          series={editing === "new" ? null : editing}
          seed={seeding}
          accounts={accounts.data ?? []}
          onClose={() => {
            setEditing(null);
            setSeeding(null);
          }}
        />
      )}
    </div>
  );
}

/**
 * What the tracked series add up to per month, one line per currency.
 *
 * Paused series are absent from these figures on purpose — pausing is how a
 * person stops counting something — and a currency that has only paused series
 * is absent entirely rather than shown as zero.
 */
function TotalsStrip({ totals }: { totals: RecurringTotals[] }) {
  return (
    <section
      className="grid gap-3 rounded-card bg-surface-raised p-4 sm:grid-cols-3"
      data-testid="recurring-totals"
    >
      {totals.map((t) => (
        <div key={t.currency} className="space-y-1">
          <h2 className="text-xs font-medium text-fg-muted">Monthly · {t.currency}</h2>
          <dl className="grid grid-cols-3 gap-2 sm:grid-cols-1">
            <div>
              <dt className="text-xs text-fg-muted">In</dt>
              <dd className="text-base font-semibold text-fg" data-testid={`totals-in-${t.currency}`}>
                {formatMoney(t.monthly_in, t.currency)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-fg-muted">Out</dt>
              <dd className="text-base font-semibold text-fg" data-testid={`totals-out-${t.currency}`}>
                {formatMoney(t.monthly_out, t.currency)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-fg-muted">Net</dt>
              <dd className="text-base font-semibold text-fg" data-testid={`totals-net-${t.currency}`}>
                {formatMoney(t.net_monthly, t.currency)}
              </dd>
            </div>
          </dl>
        </div>
      ))}
    </section>
  );
}

function SuggestionRow({
  suggestion,
  onPick,
}: {
  suggestion: RecurringSuggestion;
  onPick: () => void;
}) {
  const create = useCreateRecurring();
  return (
    // §5's phone row with two actions and an amount in it: the name and its one
    // meta line take the full width, so a long merchant wraps as words rather
    // than one per line, and the amount and the Review/Track it pair follow on
    // the next line — the pair right-aligned like every amount in every list
    // (§6.1), with a wider gutter on the phone so the money and the button
    // beside it never read as one string. From `sm:` up the block takes the free
    // space instead and this is the one-line row it always was, gaps included.
    <li
      className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 sm:gap-x-2"
      data-testid={`suggestion-${suggestion.transaction_id}`}
    >
      <div className="w-full min-w-0 sm:w-auto sm:flex-1">
        <p className="text-sm font-medium text-fg">{suggestion.name}</p>
        <p className="whitespace-nowrap text-xs text-fg-muted">
          {CADENCE_LABEL[suggestion.cadence]} · {suggestion.occurrences} seen, last{" "}
          <Day value={suggestion.last_date} style="compact" />
        </p>
      </div>
      <span className="ml-auto shrink-0 text-right text-base font-semibold text-fg">
        {formatMoney(suggestion.amount, suggestion.currency)}
      </span>
      <Button
        variant="secondary"
        className="shrink-0"
        onClick={onPick}
        data-testid={`suggestion-edit-${suggestion.transaction_id}`}
      >
        Review
      </Button>
      <Button
        className="shrink-0"
        disabled={create.isPending}
        onClick={() =>
          create.mutate({
            transaction_id: suggestion.transaction_id,
            cadence: suggestion.cadence,
          })
        }
        data-testid={`suggestion-add-${suggestion.transaction_id}`}
      >
        Track it
      </Button>
    </li>
  );
}

function SeriesRow({
  series,
  accountName,
  category,
  onEdit,
}: {
  series: RecurringSeries;
  accountName: string | null;
  category: Category | undefined;
  onEdit: () => void;
}) {
  const update = useUpdateRecurring();
  const del = useDeleteRecurring();
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="space-y-2 p-3" data-testid={`recurring-row-${series.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onEdit}
          className="min-h-11 text-left text-sm font-medium text-fg underline-offset-2 hover:underline"
          data-testid={`recurring-edit-${series.id}`}
        >
          {series.name}
          {!series.is_active && (
            <span className="ml-2 inline-flex items-center rounded-full bg-surface-inset px-2 py-0.5 text-xs text-fg-muted">
              Paused
            </span>
          )}
        </button>
        <span className="text-base font-semibold text-fg">
          {formatMoney(series.amount, series.currency)}
        </span>
      </div>
      <p className="text-xs text-fg-muted">
        {CADENCE_LABEL[series.cadence]} · {formatMoney(series.monthly_amount, series.currency)} a
        month · {series.occurrences} seen
        {series.last_seen_date && (
          <>
            , last <Day value={series.last_seen_date} style="compact" />
          </>
        )}
      </p>
      <p className="text-xs text-fg-muted">
        {accountName ?? "Any account"}
        {category ? ` · ${categoryLabel(category)}` : ""}
        {series.merchant ? ` · matches “${series.merchant}”` : ""}
        {series.next_due_date && (
          <>
            {" · next "}
            <Day value={series.next_due_date} style="compact" />
          </>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" className="px-2 py-1 text-xs" onClick={onEdit}>
          Edit
        </Button>
        <Button
          variant="ghost"
          className="px-2 py-1 text-xs"
          disabled={update.isPending}
          onClick={() => update.mutate({ id: series.id, body: { is_active: !series.is_active } })}
          data-testid={`recurring-toggle-${series.id}`}
        >
          {series.is_active ? "Pause" : "Resume"}
        </Button>
        <Button
          variant="ghost"
          className="px-2 py-1 text-xs"
          onClick={() => setConfirming(true)}
          data-testid={`recurring-delete-${series.id}`}
        >
          Delete
        </Button>
      </div>
      {confirming && (
        <div className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm">
          <p className="text-negative">
            Delete “{series.name}”? The transactions it matched are not touched — only the tracking
            goes.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              disabled={del.isPending}
              onClick={() => del.mutate(series.id, { onSuccess: () => setConfirming(false) })}
              data-testid={`recurring-delete-confirm-${series.id}`}
            >
              Yes, delete
            </Button>
          </div>
        </div>
      )}
      {(update.isError || del.isError) && (
        <p className="text-sm text-negative" role="alert">
          {((update.error ?? del.error) as Error).message}
        </p>
      )}
    </li>
  );
}

// ---- the editor ---------------------------------------------------------------

/**
 * The series form, used for both a new series and an existing one.
 *
 * The amount is *shown* as a magnitude with a direction beside it and stored
 * signed (ADR-0043's rule, and the same crossing Accounts.tsx makes for a
 * card's balance): nobody types a minus sign to say "this is a bill", and the
 * sign the ledger means by it is not the person's job to remember.
 *
 * `seed` is a suggestion being reviewed. Everything the body sends explicitly
 * wins over the seed server-side, which is what makes "Review" work: the dialog
 * sends the whole edited series, not just the id.
 */
function SeriesDialog({
  series,
  seed,
  accounts,
  onClose,
}: {
  series: RecurringSeries | null;
  seed: RecurringSuggestion | null;
  accounts: Account[];
  onClose: () => void;
}) {
  const create = useCreateRecurring();
  const update = useUpdateRecurring();
  const categories = useCategories();
  const groups = useCategoryGroups();

  const starting = series ?? seed;
  const [name, setName] = useState(starting?.name ?? "");
  const [direction, setDirection] = useState<"out" | "in">(
    starting && Number(starting.amount) > 0 ? "in" : "out",
  );
  const [amount, setAmount] = useState(
    starting ? starting.amount.replace(/^[+-]/, "") : "",
  );
  const [cadence, setCadence] = useState<Cadence>(starting?.cadence ?? "monthly");
  const [accountId, setAccountId] = useState(series?.account_id ?? seed?.account_id ?? "");
  const [categoryId, setCategoryId] = useState<string | null>(
    series?.category_id ?? seed?.category_id ?? null,
  );
  const [merchant, setMerchant] = useState(starting?.merchant ?? "");
  const [nextDue, setNextDue] = useState(
    series?.next_due_date ?? seed?.next_due_date ?? "",
  );
  const [active, setActive] = useState(series?.is_active ?? true);
  const [pickerOpen, setPickerOpen] = useState(false);

  const nameId = useFieldId("recurring-name");
  const directionId = useFieldId("recurring-direction");
  const amountId = useFieldId("recurring-amount");
  const cadenceId = useFieldId("recurring-cadence");
  const accountFieldId = useFieldId("recurring-account-field");
  const merchantId = useFieldId("recurring-merchant");
  const dueId = useFieldId("recurring-due");

  const nameError = name.trim() ? null : "Give it a name — “Rent”, “Netflix”.";
  const amountError = validAmount(amount);
  const invalid = nameError !== null || amountError !== null;
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;

  const chosenCategory = (categories.data ?? []).find((c) => c.id === categoryId);

  function submit() {
    const magnitude = amount.trim().replace(/^[+-]/, "");
    const signed = direction === "out" ? `-${magnitude}` : magnitude;
    const body = {
      name: name.trim(),
      merchant: merchant.trim() || null,
      account_id: accountId || null,
      category_id: categoryId,
      amount: signed,
      cadence,
      next_due_date: nextDue || null,
      is_active: active,
    };
    if (series) update.mutate({ id: series.id, body }, { onSuccess: onClose });
    else
      create.mutate(
        { ...body, transaction_id: seed?.transaction_id ?? null },
        { onSuccess: onClose },
      );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={series ? `Edit — ${series.name}` : "Track something regular"}
      testid="recurring-dialog"
      footer={
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="recurring-form"
            disabled={invalid || pending}
            data-testid="recurring-save"
          >
            {pending && <Spinner />}
            {series ? "Save" : "Track it"}
          </Button>
        </div>
      }
    >
      <form
        id="recurring-form"
        className="grid grid-cols-1 gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!invalid) submit();
        }}
        data-testid="recurring-form"
      >
        <Field label="Name" htmlFor={nameId} error={nameError} className="sm:col-span-2">
          <Input
            id={nameId}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Rent"
            maxLength={120}
            data-testid="recurring-name"
          />
        </Field>

        <Field label="Money" htmlFor={directionId}>
          <Select
            id={directionId}
            value={direction}
            onChange={(e) => setDirection(e.target.value as "out" | "in")}
            data-testid="recurring-direction"
          >
            <option value="out">Money out</option>
            <option value="in">Money in</option>
          </Select>
        </Field>
        <Field label="Amount" htmlFor={amountId} error={amountError}>
          <Input
            id={amountId}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="12.99"
            data-testid="recurring-amount"
          />
        </Field>

        <Field label="How often" htmlFor={cadenceId}>
          <Select
            id={cadenceId}
            value={cadence}
            onChange={(e) => setCadence(e.target.value as Cadence)}
            data-testid="recurring-cadence"
          >
            {CADENCES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Account" htmlFor={accountFieldId}>
          <Select
            id={accountFieldId}
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            data-testid="recurring-account-field"
          >
            <option value="">Any account</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
        </Field>

        <div className="sm:col-span-2">
          <span className="mb-1 block text-sm text-fg-muted">Category</span>
          <Button
            variant="secondary"
            onClick={() => setPickerOpen(true)}
            data-testid="recurring-category-field"
          >
            {categoryId ? categoryLabel(chosenCategory) : "Any category"}
          </Button>
          <p className="mt-1 text-xs text-fg-muted">
            What the tracked charges are — the series does not set it on any transaction.
          </p>
        </div>

        <Field
          label="Matches text (optional)"
          htmlFor={merchantId}
          hint="Only transactions whose merchant or description contains this."
        >
          <Input
            id={merchantId}
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
            placeholder="NETFLIX"
            maxLength={300}
            data-testid="recurring-merchant"
          />
        </Field>
        <Field label="Next due (optional)" htmlFor={dueId}>
          <Input
            id={dueId}
            type="date"
            value={nextDue}
            onChange={(e) => setNextDue(e.target.value)}
            data-testid="recurring-next-due"
          />
        </Field>

        <div className="sm:col-span-2">
          <Checkbox
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
            label="Active — count it in the monthly totals"
            data-testid="recurring-active"
          />
        </div>

        {error && (
          <p role="alert" className="text-sm text-negative sm:col-span-2">
            {(error as Error).message}
          </p>
        )}
      </form>

      <CategoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        categories={categories.data ?? []}
        groups={groups.data ?? []}
        value={categoryId}
        amount={direction === "out" ? "-1" : "1"}
        onPick={setCategoryId}
        testid="recurring-category-picker"
      />
    </Dialog>
  );
}
