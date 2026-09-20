import { useMemo, useState } from "react";
import {
  useAccounts,
  useCreateAccount,
  useDeleteAccount,
  useMembers,
  useNetWorth,
  useUpdateAccount,
} from "@/api/hooks";
import type { Account, AccountType, Member } from "@/api/types";
import { formatMoney } from "@/lib/format";
import { Button, Field, Input, Select, useFieldId, validAmount, validCurrency, requiredText } from "@/components/form";
import Dialog from "@/components/Dialog";

const TYPES: AccountType[] = ["depository", "credit", "investment", "loan", "other"];
const today = () => new Date().toISOString().slice(0, 10);

export default function Accounts() {
  const netWorth = useNetWorth();
  const accounts = useAccounts();
  const members = useMembers();
  const create = useCreateAccount();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    members.data?.forEach((x) => m.set(x.user_id, x.display_name));
    return m;
  }, [members.data]);

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

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium">Accounts</h2>
          <Button onClick={() => setOpen((v) => !v)} data-testid="add-account">
            Add account
          </Button>
        </div>

        {open && (
          <AddAccountForm
            members={members.data ?? []}
            pending={create.isPending}
            error={create.isError ? (create.error as Error).message : null}
            onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
          />
        )}

        <ul className="divide-y divide-slate-800 rounded-2xl bg-slate-900" data-testid="account-list">
          {accounts.data?.map((a) => (
            <li key={a.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="font-medium">
                  {a.name}
                  {a.is_hidden && <span className="ml-2 text-xs text-slate-500">(hidden)</span>}
                </p>
                <p className="text-xs text-slate-500">
                  {a.type} · {a.currency}
                  {a.institution && ` · ${a.institution}`}
                  {a.owner_user_id && ` · ${ownerName.get(a.owner_user_id) ?? "owner"}`}
                  {!a.is_asset && (
                    <span className="ml-1 rounded bg-red-500/20 px-1.5 py-0.5 text-red-300">
                      liability
                    </span>
                  )}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span className={a.is_asset ? "text-slate-100" : "text-red-400"}>
                  {formatMoney(a.current_balance, a.currency)}
                </span>
                <Button
                  variant="ghost"
                  className="px-2 py-1"
                  onClick={() => setEditing(a)}
                  data-testid={`account-edit-${a.id}`}
                >
                  Edit
                </Button>
              </div>
            </li>
          ))}
          {accounts.data?.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-slate-500">No accounts yet.</li>
          )}
        </ul>
      </section>

      {editing && (
        <EditAccountDialog
          account={editing}
          members={members.data ?? []}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function OwnerSelect({
  id,
  members,
  value,
  onChange,
  testid,
}: {
  id: string;
  members: Member[];
  value: string;
  onChange: (v: string) => void;
  testid: string;
}) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid}>
      <option value="">Unassigned</option>
      {members.map((m) => (
        <option key={m.user_id} value={m.user_id}>
          {m.display_name}
        </option>
      ))}
    </Select>
  );
}

function AddAccountForm({
  members,
  pending,
  error,
  onSubmit,
}: {
  members: Member[];
  pending: boolean;
  error: string | null;
  onSubmit: (b: {
    name: string;
    type: AccountType;
    currency: string;
    current_balance: string;
    balance_date: string;
    owner_user_id: string | null;
    institution: string | null;
  }) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("depository");
  const [currency, setCurrency] = useState("USD");
  const [balance, setBalance] = useState("0");
  const [balanceDate, setBalanceDate] = useState(today);
  const [institution, setInstitution] = useState("");
  const [owner, setOwner] = useState("");
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    name: useFieldId("account-name"),
    type: useFieldId("account-type"),
    currency: useFieldId("account-currency"),
    balance: useFieldId("account-balance"),
    balanceDate: useFieldId("account-balance-date"),
    institution: useFieldId("account-institution"),
    owner: useFieldId("account-owner"),
  };

  const validate = () => {
    const e = {
      name: requiredText(name),
      currency: validCurrency(currency),
      balance: validAmount(balance),
    };
    setErrs(e);
    return !e.name && !e.currency && !e.balance;
  };

  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-2xl bg-slate-900 p-4 sm:grid-cols-2"
      data-testid="add-account-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validate()) return;
        onSubmit({
          name,
          type,
          currency: currency.toUpperCase(),
          current_balance: balance,
          balance_date: balanceDate,
          owner_user_id: owner || null,
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
            <option key={t} value={t}>{t}</option>
          ))}
        </Select>
      </Field>
      <Field label="Currency" htmlFor={ids.currency} required error={errs.currency}>
        <Input id={ids.currency} value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value)} data-testid="account-currency" />
      </Field>
      <Field label="Starting balance" htmlFor={ids.balance} required error={errs.balance}>
        <Input id={ids.balance} value={balance} inputMode="decimal" onChange={(e) => setBalance(e.target.value)} data-testid="account-balance" />
      </Field>
      <Field label="Balance date" htmlFor={ids.balanceDate}>
        <Input id={ids.balanceDate} type="date" value={balanceDate} onChange={(e) => setBalanceDate(e.target.value)} data-testid="account-balance-date" />
      </Field>
      <Field label="Owner" htmlFor={ids.owner}>
        <OwnerSelect id={ids.owner} members={members} value={owner} onChange={setOwner} testid="account-owner" />
      </Field>
      <Field label="Institution" htmlFor={ids.institution}>
        <Input id={ids.institution} value={institution} onChange={(e) => setInstitution(e.target.value)} data-testid="account-institution" />
      </Field>
      {error && <p className="text-sm text-red-400 sm:col-span-2" data-testid="account-form-error">{error}</p>}
      <Button type="submit" disabled={pending} className="sm:col-span-2" data-testid="account-save">
        Save
      </Button>
    </form>
  );
}

function EditAccountDialog({
  account,
  members,
  onClose,
}: {
  account: Account;
  members: Member[];
  onClose: () => void;
}) {
  const update = useUpdateAccount();
  const del = useDeleteAccount();
  const [name, setName] = useState(account.name);
  const [institution, setInstitution] = useState(account.institution ?? "");
  const [balance, setBalance] = useState(account.current_balance);
  const [balanceDate, setBalanceDate] = useState(account.balance_date ?? today());
  const [owner, setOwner] = useState(account.owner_user_id ?? "");
  const [hidden, setHidden] = useState(account.is_hidden);
  const [confirmDel, setConfirmDel] = useState(false);
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    name: useFieldId("edit-account-name"),
    institution: useFieldId("edit-account-institution"),
    balance: useFieldId("edit-account-balance"),
    balanceDate: useFieldId("edit-account-balance-date"),
    owner: useFieldId("edit-account-owner"),
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
          current_balance: balance,
          balance_date: balanceDate,
          owner_user_id: owner || null,
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
      title="Edit account"
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
        <Field label="Balance" htmlFor={ids.balance} required error={errs.balance} hint={`In ${account.currency}`}>
          <Input id={ids.balance} value={balance} inputMode="decimal" onChange={(e) => setBalance(e.target.value)} data-testid="edit-account-balance" />
        </Field>
        <Field label="Balance date" htmlFor={ids.balanceDate}>
          <Input id={ids.balanceDate} type="date" value={balanceDate} onChange={(e) => setBalanceDate(e.target.value)} data-testid="edit-account-balance-date" />
        </Field>
        <Field label="Owner" htmlFor={ids.owner}>
          <OwnerSelect id={ids.owner} members={members} value={owner} onChange={setOwner} testid="edit-account-owner" />
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} data-testid="edit-account-hidden" />
          Hidden (exclude from net worth views)
        </label>
        {update.isError && <p className="text-sm text-red-400">{(update.error as Error).message}</p>}

        {confirmDel && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm" data-testid="account-delete-confirm">
            <p className="text-red-200">Delete “{account.name}” and its transactions? This cannot be undone.</p>
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
