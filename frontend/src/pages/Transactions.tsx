import { useMemo, useState } from "react";
import {
  useAccounts,
  useCategories,
  useCreateTransaction,
  useInfiniteTransactions,
  useMembers,
  useTags,
  type TxnFilter,
} from "@/api/hooks";
import type { Account, Category, Member, Transaction } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import {
  Button,
  Field,
  Input,
  Select,
  requiredText,
  useFieldId,
  validAmount,
} from "@/components/form";
import TxnDetailSheet from "@/components/TxnDetailSheet";

export default function Transactions() {
  const accounts = useAccounts();
  const categories = useCategories();
  const tags = useTags();
  const members = useMembers();
  const [filter, setFilter] = useState<TxnFilter>({});
  const txns = useInfiniteTransactions(filter);
  const create = useCreateTransaction();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Transaction | null>(null);

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    members.data?.forEach((x) => m.set(x.user_id, x.display_name));
    return m;
  }, [members.data]);

  const tagName = useMemo(() => {
    const m = new Map<string, string>();
    tags.data?.forEach((t) => m.set(t.id, t.name));
    return m;
  }, [tags.data]);

  const items = txns.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Transactions</h2>
        <Button onClick={() => setOpen((v) => !v)} data-testid="add-transaction">
          Add transaction
        </Button>
      </div>

      {open && (
        <AddTxnForm
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          members={members.data ?? []}
          pending={create.isPending}
          error={create.isError ? (create.error as Error).message : null}
          onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
        />
      )}

      <FilterBar
        accounts={accounts.data ?? []}
        categories={categories.data ?? []}
        filter={filter}
        onChange={setFilter}
      />

      <ul className="divide-y divide-slate-800 rounded-2xl bg-slate-900" data-testid="txn-list">
        {items.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-800/60"
              onClick={() => setSelected(t)}
              data-testid={`txn-row-${t.id}`}
            >
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {t.merchant || t.description || "(no description)"}
                  {t.is_split_parent && (
                    <span className="ml-2 rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-300">
                      split
                    </span>
                  )}
                </p>
                <p className="truncate text-xs text-slate-500">
                  {formatDate(t.transacted_at)}
                  {t.category_id && ` · ${catName.get(t.category_id) ?? ""}`}
                  {t.owner_user_id && ` · ${ownerName.get(t.owner_user_id) ?? ""}`}
                  {t.review_status === "needs_review" && " · needs review"}
                </p>
                {t.tag_ids.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {t.tag_ids.map((id) => (
                      <span key={id} className="rounded bg-brand/15 px-1.5 py-0.5 text-xs text-brand">
                        {tagName.get(id) ?? "tag"}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <span className={Number(t.amount) < 0 ? "text-slate-100" : "text-emerald-400"}>
                {formatMoney(t.amount, t.currency)}
              </span>
            </button>
          </li>
        ))}
        {items.length === 0 && !txns.isLoading && (
          <li className="px-4 py-6 text-center text-sm text-slate-500">No transactions match.</li>
        )}
      </ul>

      {txns.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            disabled={txns.isFetchingNextPage}
            onClick={() => txns.fetchNextPage()}
            data-testid="txn-load-more"
          >
            {txns.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}

      {selected && (
        <TxnDetailSheet
          txn={selected}
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          members={members.data ?? []}
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
  filter,
  onChange,
}: {
  accounts: Account[];
  categories: Category[];
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
    <div className="space-y-3 rounded-2xl bg-slate-900 p-4" data-testid="txn-filter-bar">
      <div>
        <p className="mb-1 text-xs font-medium text-slate-400">Accounts</p>
        <div className="flex flex-wrap gap-2" data-testid="filter-accounts">
          {accounts.map((a) => {
            const on = selectedAccounts.has(a.id);
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => toggleAccount(a.id)}
                aria-pressed={on}
                className={`rounded-full border px-3 py-1 text-xs ${
                  on
                    ? "border-brand bg-brand/20 text-brand"
                    : "border-slate-700 text-slate-400 hover:text-slate-200"
                }`}
                data-testid={`filter-account-${a.id}`}
              >
                {a.name}
              </button>
            );
          })}
          {accounts.length === 0 && <span className="text-xs text-slate-500">No accounts</span>}
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

      {(filter.account_id || filter.category_id || filter.start || filter.end || filter.search) && (
        <button
          type="button"
          className="text-xs text-slate-400 underline hover:text-slate-200"
          onClick={() => onChange({})}
          data-testid="filter-clear"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}

function AddTxnForm({
  accounts,
  categories,
  members,
  pending,
  error,
  onSubmit,
}: {
  accounts: Account[];
  categories: Category[];
  members: Member[];
  pending: boolean;
  error: string | null;
  onSubmit: (b: {
    account_id: string;
    amount: string;
    merchant: string | null;
    category_id: string | null;
    owner_user_id: string | null;
    transacted_at: string;
  }) => void;
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [amount, setAmount] = useState("-0.00");
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [owner, setOwner] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    account: useFieldId("txn-account"),
    date: useFieldId("txn-date"),
    merchant: useFieldId("txn-merchant"),
    amount: useFieldId("txn-amount"),
    category: useFieldId("txn-category"),
    owner: useFieldId("txn-owner"),
  };

  const validate = () => {
    const e = { account: requiredText(accountId), amount: validAmount(amount) };
    setErrs(e);
    return !e.account && !e.amount;
  };

  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-2xl bg-slate-900 p-4 sm:grid-cols-2"
      data-testid="add-txn-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validate()) return;
        onSubmit({
          account_id: accountId,
          amount,
          merchant: merchant || null,
          category_id: categoryId || null,
          owner_user_id: owner || null,
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
      <Field label="Owner" htmlFor={ids.owner}>
        <Select id={ids.owner} value={owner} onChange={(e) => setOwner(e.target.value)} data-testid="txn-owner">
          <option value="">Unassigned</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>{m.display_name}</option>
          ))}
        </Select>
      </Field>
      {error && <p className="text-sm text-red-400 sm:col-span-2">{error}</p>}
      <Button type="submit" disabled={pending} className="sm:col-span-2" data-testid="txn-save">
        Save
      </Button>
    </form>
  );
}
