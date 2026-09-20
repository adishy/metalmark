import { useMemo, useState } from "react";
import {
  useAccounts,
  useCategories,
  useCreateTransaction,
  useInfiniteTransactions,
  useOwners,
  useTags,
  type TxnFilter,
} from "@/api/hooks";
import type { Account, Category, Owner, Transaction, TransactionCreate } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import { todayIso } from "@/lib/dates";
import {
  Button,
  Field,
  Input,
  Select,
  Spinner,
  requiredText,
  useFieldId,
  validAmount,
} from "@/components/form";
import OwnerSelect from "@/components/OwnerSelect";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import TxnDetailSheet from "@/components/TxnDetailSheet";
import ImportDialog from "@/components/ImportDialog";

/**
 * Whether anything is actually narrowing the list. Used twice — once to offer
 * "Clear filters" in the bar, once to offer it in the empty state — and it has
 * to be one predicate, because the empty state uses it to decide whether the
 * ledger is empty or merely filtered, and those two deserve different sentences.
 *
 * `?.length` rather than the bare truthiness the call sites used to do. The
 * setters today clear to `undefined` rather than `[]`, so the two agree — but
 * an empty array is truthy, so the shorter form is one refactor away from
 * calling a cleared filter "active" and offering a Clear button for nothing.
 */
function isFiltered(f: TxnFilter): boolean {
  return Boolean(
    f.account_id?.length || f.category_id?.length || f.owner_id || f.start || f.end || f.search,
  );
}

export default function Transactions() {
  const accounts = useAccounts();
  const categories = useCategories();
  const tags = useTags();
  const owners = useOwners();
  const [filter, setFilter] = useState<TxnFilter>({});
  const txns = useInfiniteTransactions(filter);
  const create = useCreateTransaction();
  const [open, setOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Transaction | null>(null);

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  const acctName = useMemo(() => {
    const m = new Map<string, string>();
    accounts.data?.forEach((a) => m.set(a.id, a.name));
    return m;
  }, [accounts.data]);

  const tagName = useMemo(() => {
    const m = new Map<string, string>();
    tags.data?.forEach((t) => m.set(t.id, t.name));
    return m;
  }, [tags.data]);

  const items = txns.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = isFiltered(filter);

  return (
    <div className="space-y-4">
      {/* flex-wrap: the title plus two actions do not fit at 360px, and an
          unwrapped header is what pushes the page into horizontal scroll (§5). */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-medium">Transactions</h1>
        <div className="flex items-center gap-2">
          {/* A whole statement at once, next to adding one row by hand. */}
          <Button
            variant="secondary"
            onClick={() => setImporting(true)}
            data-testid="import-csv"
          >
            Import CSV
          </Button>
          <Button onClick={() => setOpen((v) => !v)} data-testid="add-transaction">
            Add transaction
          </Button>
        </div>
      </div>

      {open && (
        <AddTxnForm
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          owners={owners.data ?? []}
          pending={create.isPending}
          error={create.isError ? (create.error as Error).message : null}
          onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
        />
      )}

      <FilterBar
        accounts={accounts.data ?? []}
        categories={categories.data ?? []}
        owners={owners.data ?? []}
        filter={filter}
        onChange={setFilter}
      />

      <ul className="divide-y divide-border rounded-card bg-surface-raised" data-testid="txn-list">
        {items.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-surface-inset/60"
              onClick={() => setSelected(t)}
              data-testid={`txn-row-${t.id}`}
            >
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-2">
                  <p className="truncate font-medium">
                    {t.merchant || t.description || "(no description)"}
                  </p>
                  {t.is_split_parent && (
                    // A sibling of the truncating text, not part of it: a badge
                    // clipped to "spl…" is not a label.
                    <span className="shrink-0 rounded bg-surface-inset px-1.5 py-0.5 text-xs text-fg">
                      split
                    </span>
                  )}
                </div>
                <p className="truncate text-xs text-fg-muted">
                  {formatDate(t.transacted_at)}
                  {t.category_id && ` · ${catName.get(t.category_id) ?? ""}`}
                  {/* The effective owner is what reports actually bucket by, so
                      that is what the row shows; a muted style marks the ones
                      that only inherit it from their account. */}
                  <span
                    className={t.owner_id ? "text-fg" : "italic text-fg-muted"}
                    title={
                      t.owner_id
                        ? "Owner set on this transaction"
                        : `Inherited from ${acctName.get(t.account_id) ?? "the account"}`
                    }
                    data-testid={`txn-owner-${t.id}`}
                  >
                    {` · ${ownerName.get(t.effective_owner_id) ?? "Shared"}`}
                    {!t.owner_id && (
                      <>
                        {/* Inherited needs a marker that survives touch and
                            greyscale: the glyph is decorative, the sentence
                            behind it is the accessible name (§7.8). */}
                        <span aria-hidden="true"> ↳</span>
                        <span className="sr-only">
                          {` (inherited from ${acctName.get(t.account_id) ?? "the account"})`}
                        </span>
                      </>
                    )}
                  </span>
                  {t.review_status === "needs_review" && " · needs review"}
                </p>
                {t.tag_ids.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {t.tag_ids.map((id) => (
                      <span key={id} className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">
                        {tagName.get(id) ?? "tag"}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {/* shrink-0 and text-right: the merchant gives way, the number
                  never does (§6.5). */}
              <span
                className={`shrink-0 text-right text-base font-semibold tabular-nums ${
                  Number(t.amount) < 0 ? "text-fg" : "text-positive"
                }`}
              >
                {formatMoney(t.amount, t.currency)}
              </span>
            </button>
          </li>
        ))}
        {items.length === 0 && !txns.isLoading && (
          <li className="px-4">
            {/* The message *about* the results, not the list itself: a filter
                change that empties the ledger is otherwise silent (§7.6).

                Two sentences, not one: "no transactions match" is a claim about
                the filters, and on a genuinely empty ledger nothing was
                filtered, so it would be describing a cause that isn't there. */}
            <p role="status" aria-atomic="true" className="pt-6 text-center text-sm text-fg-muted">
              {filtered ? "No transactions match these filters." : "No transactions yet."}
            </p>
            {/* §4.10's escape hatch, at the point of failure. The filter bar has
                its own Clear, but it is off-screen above by the time the user
                has scrolled here to find out why the list is empty. */}
            {filtered && (
              <div className="flex justify-center pb-6 pt-3">
                <Button variant="secondary" onClick={() => setFilter({})} data-testid="empty-clear-filters">
                  Clear filters
                </Button>
              </div>
            )}
          </li>
        )}
      </ul>

      {txns.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            disabled={txns.isFetchingNextPage}
            aria-busy={txns.isFetchingNextPage}
            onClick={() => txns.fetchNextPage()}
            data-testid="txn-load-more"
          >
            {txns.isFetchingNextPage && <Spinner />}
            Load more
          </Button>
        </div>
      )}

      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
        />
      )}

      {selected && (
        <TxnDetailSheet
          txn={selected}
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          tags={tags.data ?? []}
          onClose={() => setSelected(null)}
          onReplaced={(updated) => setSelected(updated)}
        />
      )}
    </div>
  );
}

function FilterBar({
  accounts,
  categories,
  owners,
  filter,
  onChange,
}: {
  accounts: Account[];
  categories: Category[];
  owners: Owner[];
  filter: TxnFilter;
  onChange: (f: TxnFilter) => void;
}) {
  const catId = useFieldId("filter-category");
  const startId = useFieldId("filter-start");
  const endId = useFieldId("filter-end");
  const searchId = useFieldId("filter-search");

  const selectedAccounts = new Set(filter.account_id ?? []);
  const toggleAccount = (id: string) => {
    const next = new Set(selectedAccounts);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange({ ...filter, account_id: next.size ? Array.from(next) : undefined });
  };

  return (
    <div className="space-y-3 rounded-card bg-surface-raised p-4" data-testid="txn-filter-bar">
      <OwnerFilterChips
        owners={owners}
        value={filter.owner_id ?? null}
        onChange={(id) => onChange({ ...filter, owner_id: id ?? undefined })}
      />

      <div>
        <p className="mb-1 text-xs font-medium text-fg-muted">Accounts</p>
        <div className="flex flex-wrap gap-2" data-testid="filter-accounts">
          {accounts.map((a) => {
            const on = selectedAccounts.has(a.id);
            // Same geometry as OwnerFilterChips: `px-3 py-1 text-xs` computes to
            // 24px, and a chip is a thumb target (§4.5).
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => toggleAccount(a.id)}
                aria-pressed={on}
                className={`inline-flex min-h-11 items-center rounded-full border px-4 text-sm ${
                  on
                    ? "border-accent bg-accent/20 font-medium text-accent"
                    : "border-border-strong text-fg-muted hover:text-fg"
                }`}
                data-testid={`filter-account-${a.id}`}
              >
                {a.name}
              </button>
            );
          })}
          {accounts.length === 0 && <span className="text-xs text-fg-muted">No accounts</span>}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Category" htmlFor={catId}>
          <Select
            id={catId}
            value={filter.category_id?.[0] ?? ""}
            onChange={(e) =>
              onChange({ ...filter, category_id: e.target.value ? [e.target.value] : undefined })
            }
            data-testid="filter-category"
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="From" htmlFor={startId}>
          <Input
            id={startId}
            type="date"
            value={filter.start ? filter.start.slice(0, 10) : ""}
            onChange={(e) =>
              onChange({
                ...filter,
                start: e.target.value ? new Date(e.target.value + "T00:00:00Z").toISOString() : undefined,
              })
            }
            data-testid="filter-start"
          />
        </Field>
        <Field label="To" htmlFor={endId}>
          <Input
            id={endId}
            type="date"
            value={filter.end ? filter.end.slice(0, 10) : ""}
            onChange={(e) =>
              onChange({
                ...filter,
                end: e.target.value ? new Date(e.target.value + "T23:59:59Z").toISOString() : undefined,
              })
            }
            data-testid="filter-end"
          />
        </Field>
        <Field label="Search" htmlFor={searchId}>
          <Input
            id={searchId}
            type="search"
            placeholder="Merchant or notes"
            value={filter.search ?? ""}
            onChange={(e) => onChange({ ...filter, search: e.target.value || undefined })}
            data-testid="filter-search"
          />
        </Field>
      </div>

      {isFiltered(filter) && (
        <Button
          variant="ghost"
          onClick={() => onChange({})}
          data-testid="filter-clear"
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}

function AddTxnForm({
  accounts,
  categories,
  owners,
  pending,
  error,
  onSubmit,
}: {
  accounts: Account[];
  categories: Category[];
  owners: Owner[];
  pending: boolean;
  error: string | null;
  onSubmit: (b: TransactionCreate) => void;
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [amount, setAmount] = useState("-0.00");
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [owner, setOwner] = useState<string | null>(null);
  const [date, setDate] = useState(() => todayIso());
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  // What an unset owner inherits: the owning account's own owner.
  const accountOwnerId = accounts.find((a) => a.id === accountId)?.owner_id;
  const inheritFrom = owners.find((o) => o.id === accountOwnerId)?.name;

  const ids = {
    account: useFieldId("txn-account"),
    date: useFieldId("txn-date"),
    merchant: useFieldId("txn-merchant"),
    amount: useFieldId("txn-amount"),
    category: useFieldId("txn-category"),
  };

  const validate = () => {
    const e = { account: requiredText(accountId), amount: validAmount(amount) };
    setErrs(e);
    return !e.account && !e.amount;
  };

  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-card bg-surface-raised p-4 sm:grid-cols-2"
      data-testid="add-txn-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validate()) return;
        onSubmit({
          account_id: accountId,
          amount,
          merchant: merchant || null,
          category_id: categoryId || null,
          owner_id: owner,
          transacted_at: new Date(date + "T12:00:00Z").toISOString(),
        });
      }}
    >
      <Field label="Account" htmlFor={ids.account} required error={errs.account}>
        <Select id={ids.account} value={accountId} onChange={(e) => setAccountId(e.target.value)} data-testid="txn-account">
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </Select>
      </Field>
      <Field label="Date" htmlFor={ids.date}>
        <Input id={ids.date} type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="txn-date" />
      </Field>
      <Field label="Merchant" htmlFor={ids.merchant}>
        <Input id={ids.merchant} value={merchant} onChange={(e) => setMerchant(e.target.value)} data-testid="txn-merchant" />
      </Field>
      <Field label="Amount" htmlFor={ids.amount} required error={errs.amount} hint="Negative = spend">
        <Input id={ids.amount} value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} data-testid="txn-amount" />
      </Field>
      <Field label="Category" htmlFor={ids.category}>
        <Select id={ids.category} value={categoryId} onChange={(e) => setCategoryId(e.target.value)} data-testid="txn-category">
          <option value="">Uncategorized</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </Select>
      </Field>
      <OwnerSelect
        value={owner}
        onChange={setOwner}
        nullable
        inheritFrom={inheritFrom}
        testid="txn-owner"
      />
      {/* role="alert": a failed save is announced without moving focus (WCAG 4.1.3). */}
      {error && <p className="text-sm text-negative sm:col-span-2" role="alert">{error}</p>}
      <Button type="submit" disabled={pending} className="sm:col-span-2" data-testid="txn-save">
        Save
      </Button>
    </form>
  );
}
