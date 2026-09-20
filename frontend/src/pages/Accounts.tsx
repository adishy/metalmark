import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAccounts, useInvalidateLedger, useNetWorth } from "@/api/hooks";
import type { Account, AccountType } from "@/api/types";
import { formatMoney } from "@/lib/format";

const TYPES: AccountType[] = ["depository", "credit", "investment", "loan", "other"];

export default function Accounts() {
  const netWorth = useNetWorth();
  const accounts = useAccounts();
  const invalidate = useInvalidateLedger();
  const [open, setOpen] = useState(false);

  const create = useMutation({
    mutationFn: (body: Partial<Account>) => api.post<Account>("/accounts", body),
    onSuccess: () => {
      invalidate();
      setOpen(false);
    },
  });

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-slate-900 p-6" data-testid="net-worth">
        <p className="text-sm text-slate-400">Net worth</p>
        <p className="text-3xl font-semibold">
          {netWorth.data
            ? formatMoney(netWorth.data.net_worth, netWorth.data.base_currency)
            : "—"}
        </p>
        {netWorth.data && (
          <div className="mt-2 flex gap-6 text-sm text-slate-400">
            <span>Assets {formatMoney(netWorth.data.assets, netWorth.data.base_currency)}</span>
            <span>
              Liabilities {formatMoney(netWorth.data.liabilities, netWorth.data.base_currency)}
            </span>
          </div>
        )}
        {netWorth.data && netWorth.data.unconverted_currencies.length > 0 && (
          <p className="mt-2 text-sm text-amber-400" data-testid="no-rate-warning">
            No FX rate for: {netWorth.data.unconverted_currencies.join(", ")}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium">Accounts</h2>
          <button
            onClick={() => setOpen((v) => !v)}
            className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-slate-950"
            data-testid="add-account"
          >
            Add account
          </button>
        </div>

        {open && <AddAccountForm onSubmit={(b) => create.mutate(b)} pending={create.isPending} />}

        <ul className="divide-y divide-slate-800 rounded-2xl bg-slate-900" data-testid="account-list">
          {accounts.data?.map((a) => (
            <li key={a.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="font-medium">{a.name}</p>
                <p className="text-xs text-slate-500">
                  {a.type} · {a.currency}
                  {!a.is_asset && " · liability"}
                </p>
              </div>
              <span className={a.is_asset ? "text-slate-100" : "text-red-400"}>
                {formatMoney(a.current_balance, a.currency)}
              </span>
            </li>
          ))}
          {accounts.data?.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-slate-500">No accounts yet.</li>
          )}
        </ul>
      </section>
    </div>
  );
}

function AddAccountForm({
  onSubmit,
  pending,
}: {
  onSubmit: (b: Partial<Account>) => void;
  pending: boolean;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("depository");
  const [currency, setCurrency] = useState("USD");
  const [balance, setBalance] = useState("0");

  return (
    <form
      className="grid grid-cols-2 gap-3 rounded-2xl bg-slate-900 p-4"
      data-testid="add-account-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ name, type, currency: currency.toUpperCase(), current_balance: balance });
      }}
    >
      <input
        placeholder="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="col-span-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="account-name"
        required
      />
      <select
        value={type}
        onChange={(e) => setType(e.target.value as AccountType)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="account-type"
      >
        {TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <input
        placeholder="Currency"
        value={currency}
        onChange={(e) => setCurrency(e.target.value)}
        className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="account-currency"
        maxLength={3}
      />
      <input
        placeholder="Balance"
        value={balance}
        onChange={(e) => setBalance(e.target.value)}
        className="col-span-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
        data-testid="account-balance"
        inputMode="decimal"
      />
      <button
        type="submit"
        disabled={pending}
        className="col-span-2 rounded-lg bg-brand py-2 font-medium text-slate-950 disabled:opacity-50"
        data-testid="account-save"
      >
        Save
      </button>
    </form>
  );
}
