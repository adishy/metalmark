import { useMemo, useState } from "react";
import {
  useAccounts,
  useCreateAccount,
  useDeleteAccount,
  useNetWorth,
  useOwners,
  useUpdateAccount,
} from "@/api/hooks";
import type { Account, AccountCreate, AccountType, Owner } from "@/api/types";
import { formatMoney } from "@/lib/format";
import { todayIso } from "@/lib/dates";
import { Button, Checkbox, Field, Input, Select, useFieldId, validAmount, validCurrency, requiredText } from "@/components/form";
import Dialog from "@/components/Dialog";
import OwnerSelect from "@/components/OwnerSelect";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import InvestmentsView from "@/components/InvestmentsView";
import SegmentedControl, { segmentPanelId, segmentTabId, type Segment } from "@/components/SegmentedControl";

const TYPES: AccountType[] = ["depository", "credit", "investment", "loan", "other"];
// A balance date is a calendar day where the user is, so it comes from the local
// clock — ``toISOString()`` would roll it back a day for anyone east of UTC.
const today = () => todayIso();

/*
 * Two views of one destination.
 *
 * The investments view is a *second view* rather than a sixth tab because the
 * phone tab bar is capped at five and the cap is real (DESIGN.md §4.13): a sixth
 * target is no longer thumb-reachable, and a destination with no room in the bar
 * becomes a section inside an existing one. Accounts already owns the
 * household's balances; a portfolio is the same subject at a different depth.
 *
 * Balances is the default, so every existing path — the net-worth header, the
 * owner filter, "Add account" — is exactly where it was.
 */
const VIEWS = [
  { id: "balances", label: "Balances" },
  { id: "investments", label: "Investments" },
] as const;

type ViewId = (typeof VIEWS)[number]["id"];
const VIEW_TESTID = "accounts-view";

export default function Accounts() {
  const owners = useOwners();
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [view, setView] = useState<ViewId>("balances");
  // The filter scopes the list and the header together: an owner's net worth is
  // the sum of that owner's accounts, so a filtered list with an unfiltered
  // total would read as a bug.
  const netWorth = useNetWorth(ownerFilter);
  const accounts = useAccounts(ownerFilter);
  const create = useCreateAccount();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  const panelId = segmentPanelId(VIEW_TESTID, view);

  return (
    <div className="space-y-6">
      {/* The page's one <h1> sits above both views: the tabs select what is
          below them, so a heading inside a panel would leave the investments
          view without one (§7.5). */}
      <h1 className="text-lg font-medium">Accounts</h1>

      <SegmentedControl
        label="Accounts view"
        segments={VIEWS as readonly Segment<ViewId>[]}
        value={view}
        onChange={setView}
        testid={VIEW_TESTID}
      />

      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={segmentTabId(VIEW_TESTID, view)}
        data-testid={panelId}
        className="space-y-6"
      >
        {view === "investments" ? (
          <InvestmentsView
            ownerName={ownerFilter ? (ownerName.get(ownerFilter) ?? "that owner") : null}
          />
        ) : (
          <>
            <section className="rounded-card bg-surface-raised p-6" data-testid="net-worth">
              <p className="text-sm text-fg-muted">
                Net worth
                {ownerFilter && ` · ${ownerName.get(ownerFilter) ?? "owner"}`}
              </p>
              <p className="text-3xl font-semibold">
                {netWorth.data
                  ? formatMoney(netWorth.data.net_worth, netWorth.data.base_currency)
                  : "—"}
              </p>
              {netWorth.data && (
                <div className="mt-2 flex gap-6 text-sm text-fg-muted">
                  <span>Assets {formatMoney(netWorth.data.assets, netWorth.data.base_currency)}</span>
                  <span>
                    Liabilities {formatMoney(netWorth.data.liabilities, netWorth.data.base_currency)}
                  </span>
                </div>
              )}
              {netWorth.data && netWorth.data.unconverted_currencies.length > 0 && (
                // The words carry the warning; role="status" is what makes it heard
                // when the query lands and a rate turns out to be missing (§6.4).
                <p className="mt-2 text-sm text-warning" role="status" data-testid="no-rate-warning">
                  No FX rate for: {netWorth.data.unconverted_currencies.join(", ")}
                </p>
              )}
            </section>

            <section className="space-y-3">
              <div className="flex items-center justify-between">
                {/* `<h2>`, not the page's `<h1>`: the heading for "Accounts" is above
                    the view switcher now, and a second `<h1>` on one page is the
                    heading-order failure §7.5 names. */}
                <h2 className="text-lg font-medium">Balances</h2>
                <Button onClick={() => setOpen((v) => !v)} data-testid="add-account">
                  Add account
                </Button>
              </div>

              <OwnerFilterChips
                owners={owners.data ?? []}
                value={ownerFilter}
                onChange={setOwnerFilter}
              />

              {open && (
                <AddAccountForm
                  owners={owners.data ?? []}
                  pending={create.isPending}
                  error={create.isError ? (create.error as Error).message : null}
                  onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
                />
              )}

              <ul className="divide-y divide-border rounded-card bg-surface-raised" data-testid="account-list">
                {accounts.data?.map((a) => (
                  <li key={a.id} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="font-medium">
                        {a.name}
                        {a.is_hidden && <span className="ml-2 text-xs text-fg-muted">(hidden)</span>}
                      </p>
                      <p className="text-xs text-fg-muted">
                        {a.type} · {a.currency}
                        {a.institution && ` · ${a.institution}`}
                        <span data-testid={`account-owner-${a.id}`}>
                          {` · ${ownerName.get(a.owner_id) ?? "owner"}`}
                        </span>
                        {!a.is_asset && (
                          <span className="ml-1 rounded bg-negative/20 px-1.5 py-0.5 text-negative">
                            liability
                          </span>
                        )}
                      </p>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className={a.is_asset ? "text-fg" : "text-negative"}>
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
                  <li className="px-4 py-6 text-center text-sm text-fg-muted">No accounts yet.</li>
                )}
              </ul>
            </section>
          </>
        )}
      </div>

      {editing && <EditAccountDialog account={editing} onClose={() => setEditing(null)} />}
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
  const [balance, setBalance] = useState("0");
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
      balance: validAmount(balance),
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
          current_balance: balance,
          balance_date: balanceDate,
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
  const [name, setName] = useState(account.name);
  const [institution, setInstitution] = useState(account.institution ?? "");
  const [balance, setBalance] = useState(account.current_balance);
  const [balanceDate, setBalanceDate] = useState(account.balance_date ?? today());
  const [owner, setOwner] = useState<string | null>(account.owner_id);
  const [hidden, setHidden] = useState(account.is_hidden);
  const [confirmDel, setConfirmDel] = useState(false);
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    name: useFieldId("edit-account-name"),
    institution: useFieldId("edit-account-institution"),
    balance: useFieldId("edit-account-balance"),
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
          current_balance: balance,
          balance_date: balanceDate,
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
