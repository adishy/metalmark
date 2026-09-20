import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import {
  useAccounts,
  useCategories,
  useInvalidateLedger,
  useTransactions,
} from "@/api/hooks";
import type { Transaction } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";

export default function Transactions() {
  const accounts = useAccounts();
  const categories = useCategories();
  const txns = useTransactions();
  const invalidate = useInvalidateLedger();
  const [open, setOpen] = useState(false);

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<Transaction>("/transactions", body),
    onSuccess: () => {
      invalidate();
      setOpen(false);
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Transactions</h2>
        <button
          onClick={() => setOpen((v) => !v)}
          className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-slate-950"
          data-testid="add-transaction"
        >
          Add transaction
        </button>
      </div>

      {open && (
        <AddTxnForm
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          pending={create.isPending}
          onSubmit={(b) => create.mutate(b)}
        />
      )}

      <ul className="divide-y divide-slate-800 rounded-2xl bg-slate-900" data-testid="txn-list">
        {txns.data?.items.map((t) => (
          <li key={t.id} className="flex items-center justify-between px-4 py-3">
            <div>
              <p className="font-medium">{t.merchant || t.description || "(no description)"}</p>
              <p className="text-xs text-slate-500">
                {formatDate(t.transacted_at)}
                {t.category_id && ` · ${catName.get(t.category_id) ?? ""}`}
                {t.review_status === "needs_review" && " · needs review"}
              </p>
            </div>
            <span className={Number(t.amount) < 0 ? "text-slate-100" : "text-emerald-400"}>
              {formatMoney(t.amount, t.currency)}
            </span>
          </li>
        ))}
        {txns.data?.items.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-slate-500">No transactions yet.</li>
        )}
      </ul>
    </div>
  );
}

function AddTxnForm({
  accounts,
  categories,
  pending,
  onSubmit,
}: {
  accounts: { id: string; name: string; currency: string }[];
  categories: { id: string; name: string }[];
  pending: boolean;
  onSubmit: (b: Record<string, unknown>) => void;
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [amount, setAmount] = useState("-0.00");
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));

  return (
    <form
      className="grid grid-cols-2 gap-3 rounded-2xl bg-slate-900 p-4"
      data-testid="add-txn-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          account_id: accountId,
          amount,
          merchant: merchant || null,
          category_id: categoryId || null,
          transacted_at: new Date(date + "T12:00:00Z").toISOString(),
        });
      }}
    >
      <select
        value={accountId}
        onChange={(e) => setAccountId(e.target.value)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="txn-account"
        required
      >
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
      <input
        type="date"
        value={date}
        onChange={(e) => setDate(e.target.value)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="txn-date"
      />
      <input
        placeholder="Merchant"
        value={merchant}
        onChange={(e) => setMerchant(e.target.value)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="txn-merchant"
      />
      <input
        placeholder="Amount (- = spend)"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="txn-amount"
        inputMode="decimal"
      />
      <select
        value={categoryId}
        onChange={(e) => setCategoryId(e.target.value)}
        className="col-span-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="txn-category"
      >
        <option value="">Uncategorized</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <button
        type="submit"
        disabled={pending}
        className="col-span-2 rounded-lg bg-brand py-2 font-medium text-slate-950 disabled:opacity-50"
        data-testid="txn-save"
      >
        Save
      </button>
    </form>
  );
}
