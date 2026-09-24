// Slide-over sheet to edit a single transaction: core fields, owner, category,
// tags, notes, review status, plus an inline split editor (by $ or %) that saves
// through the dedicated splits endpoint. Delete lives here too (with confirm).
// The transfer panel at the bottom is where a leg is matched to its counterpart,
// unlinked, or shown the FX cost it came with (ADR-0008/0018).
import { useMemo, useState } from "react";
import { categoryLabel } from "@/components/CategoryPicker";
import {
  useDeleteTransaction,
  useHousehold,
  useOwners,
  useReplaceSplits,
  useUpdateTransaction,
} from "@/api/hooks";
import { useLinkTransfer, useTransfer, useTransferCandidates, useUnlinkTransfer } from "@/api/transfers";
import type { Account, Category, Money, SplitIn, Tag, Transaction } from "@/api/types";
import { formatMoney } from "@/lib/format";
import AccountMark from "@/components/AccountMark";
import { Day } from "@/components/datetime";
import Dialog from "@/components/Dialog";
import OwnerSelect from "@/components/OwnerSelect";
import { useIsDesktop } from "@/lib/media";
import { Button, Field, Input, Select, Textarea, useFieldId, validAmount } from "@/components/form";

interface TxnDetailSheetProps {
  txn: Transaction;
  accounts: Account[];
  categories: Category[];
  tags: Tag[];
  onClose: () => void;
  onReplaced: (updated: Transaction) => void;
  /**
   * Which of §9.3's two detail presentations to use, or `auto` to let the width
   * decide.
   *
   * The pane exists to sit *beside* a list — §9.3 calls the Transactions list
   * keeping its two thirds "the single biggest win" — so a page with no list has
   * no second column to put a pane in. Review is that page: its whole desktop
   * shape is one centred card, and a pane there would either crush this form
   * into the deck's column or widen the page past what §9.3 says it does. So
   * Review asks for the overlay by name rather than by width.
   */
  presentation?: "auto" | "overlay";
}

export default function TxnDetailSheet(props: TxnDetailSheetProps) {
  // Keyed by transaction id, so the form below re-initializes whenever the row it
  // is editing changes — whether that is a new pick from the list behind the
  // sheet, or the sheet navigating to the counterpart leg of a transfer. Without
  // the key the fields would keep the previous row's half-edited values, which is
  // exactly the kind of silent wrong-write the ledger does not need.
  return <TxnDetailForm key={props.txn.id} {...props} />;
}

function TxnDetailForm({
  txn,
  accounts,
  categories,
  tags,
  onClose,
  onReplaced,
  presentation = "auto",
}: TxnDetailSheetProps) {
  const update = useUpdateTransaction();
  const del = useDeleteTransaction();
  const owners = useOwners();
  // Which of the two presentations this is (§9.3): beside the list in the
  // page's second column at `lg:`, or over the page as the right slide-over.
  // Decided here rather than by the caller so there is one component and one
  // form — a caller that rendered both and hid one would give the household
  // two copies of every edit in flight.
  //
  // The hook runs either way and only its answer is overridden — it is not
  // `presentation === "overlay" ? false : useIsDesktop()`, which would call a
  // hook conditionally. `overlay` is a caller saying it has nowhere to put a
  // pane, not a second layout.
  const desktop = useIsDesktop();
  const pane = presentation === "overlay" ? false : desktop;

  const account = accounts.find((a) => a.id === txn.account_id);
  const [amount, setAmount] = useState(txn.amount);
  const [date, setDate] = useState(txn.transacted_at.slice(0, 10));
  const [merchant, setMerchant] = useState(txn.merchant ?? "");
  const [description, setDescription] = useState(txn.description ?? "");
  const [categoryId, setCategoryId] = useState(txn.category_id ?? "");
  const [owner, setOwner] = useState(txn.owner_id);
  const [tagIds, setTagIds] = useState<string[]>(txn.tag_ids);
  const [notes, setNotes] = useState(txn.notes ?? "");
  const [reviewStatus, setReviewStatus] = useState(txn.review_status);
  const [confirmDel, setConfirmDel] = useState(false);
  const [amountErr, setAmountErr] = useState<string | null>(null);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  const ids = {
    amount: useFieldId("detail-amount"),
    date: useFieldId("detail-date"),
    merchant: useFieldId("detail-merchant"),
    description: useFieldId("detail-description"),
    category: useFieldId("detail-category"),
    notes: useFieldId("detail-notes"),
    review: useFieldId("detail-review"),
  };

  const toggleTag = (id: string) =>
    setTagIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const save = () => {
    const err = validAmount(amount);
    setAmountErr(err);
    if (err) return;
    update.mutate(
      {
        id: txn.id,
        body: {
          amount,
          transacted_at: new Date(date + "T12:00:00Z").toISOString(),
          merchant: merchant || null,
          description: description || null,
          category_id: categoryId || null,
          owner_id: owner,
          tag_ids: tagIds,
          notes: notes || null,
          review_status: reviewStatus,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog
      open
      side
      inline={pane}
      onClose={onClose}
      title="Transaction"
      testid="txn-detail"
      footer={
        <>
          <Button variant="danger" onClick={() => setConfirmDel(true)} data-testid="txn-delete">
            Delete
          </Button>
          <div className="flex-1" />
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={update.isPending} data-testid="txn-detail-save">Save</Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Which account, at the top, where a transfer's other leg is not: the
            sheet edits one row, and "which account is this credit or debit
            from?" is the first thing about that row after the amount. The Mark
            carries it as a shape and a name rather than as a form field — the
            account is not editable here, so it should not look like it is. */}
        {account && (
          <div className="flex min-w-0 items-center gap-2" data-testid="detail-account">
            <AccountMark name={account.name} institution={account.institution} size="md" />
            <span className="truncate text-sm text-fg">{account.name}</span>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label={`Amount (${txn.currency})`} htmlFor={ids.amount} required error={amountErr}>
            <Input id={ids.amount} value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} data-testid="detail-amount" />
          </Field>
          <Field label="Date" htmlFor={ids.date}>
            <Input id={ids.date} type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="detail-date" />
          </Field>
        </div>
        <Field label="Merchant" htmlFor={ids.merchant}>
          <Input id={ids.merchant} value={merchant} onChange={(e) => setMerchant(e.target.value)} data-testid="detail-merchant" />
        </Field>
        <Field label="Description" htmlFor={ids.description}>
          <Input id={ids.description} value={description} onChange={(e) => setDescription(e.target.value)} data-testid="detail-description" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Category" htmlFor={ids.category}>
            <Select id={ids.category} value={categoryId} onChange={(e) => setCategoryId(e.target.value)} data-testid="detail-category">
              <option value="">Uncategorized</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
              ))}
            </Select>
          </Field>
          <OwnerSelect
            value={owner}
            onChange={setOwner}
            nullable
            inheritFrom={
              account ? (ownerName.get(account.owner_id) ?? "account owner") : undefined
            }
            testid="detail-owner"
          />
        </div>

        {/* An inheriting row's owner comes from its account, so name the owner
            the user is actually looking at rather than leaving it implied. */}
        {!owner && (
          <p className="text-xs text-fg-muted" data-testid="detail-owner-effective">
            Effective owner: {ownerName.get(account?.owner_id ?? "") ?? "—"}
            {account && ` (from ${account.name})`}
          </p>
        )}

        <div>
          <p className="mb-1 text-xs font-medium text-fg-muted">Tags</p>
          <div className="flex flex-wrap gap-2" data-testid="detail-tags">
            {tags.map((t) => {
              const on = tagIds.includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => toggleTag(t.id)}
                  aria-pressed={on}
                  // The canonical chip geometry (§4.5), because a chip is a
                  // thumb target: the `px-3 py-1 text-xs` this used to be
                  // computes to 26px, which §4.5 names as forbidden. It went
                  // unnoticed until the demo ledger grew tags — with none
                  // seeded, none of these buttons ever rendered for the
                  // target-size sweep to measure.
                  className={`inline-flex min-h-11 items-center rounded-full border px-4 text-sm ${
                    on ? "border-accent bg-accent/20 font-medium text-accent" : "border-border-strong text-fg-muted hover:text-fg"
                  }`}
                  data-testid={`detail-tag-${t.id}`}
                >
                  {t.name}
                </button>
              );
            })}
            {tags.length === 0 && <span className="text-xs text-fg-muted">No tags yet.</span>}
          </div>
        </div>

        <Field label="Review status" htmlFor={ids.review}>
          <Select id={ids.review} value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value as Transaction["review_status"])} data-testid="detail-review">
            <option value="needs_review">Needs review</option>
            <option value="reviewed">Reviewed</option>
            <option value="ignored">Ignored</option>
          </Select>
        </Field>

        <Field label="Notes" htmlFor={ids.notes}>
          <Textarea id={ids.notes} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="detail-notes" />
        </Field>

        {update.isError && <p className="text-sm text-negative">{(update.error as Error).message}</p>}

        <TransferSection
          txn={txn}
          accounts={accounts}
          // Navigating to the counterpart is the same operation as the list
          // selecting another row: the sheet now shows a different transaction.
          onNavigate={onReplaced}
          onReplaced={onReplaced}
        />

        <SplitEditor
          txn={txn}
          currency={account?.currency ?? txn.currency}
          categories={categories}
          inheritFrom={ownerName.get(txn.effective_owner_id) ?? "transaction"}
          onReplaced={onReplaced}
        />

        {confirmDel && (
          <div className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm" data-testid="txn-delete-confirm">
            <p className="text-negative">Delete this transaction? This cannot be undone.</p>
            <div className="mt-2 flex gap-2">
              <Button variant="secondary" onClick={() => setConfirmDel(false)}>Keep</Button>
              <Button
                variant="danger"
                disabled={del.isPending}
                onClick={() => del.mutate(txn.id, { onSuccess: onClose })}
                data-testid="txn-delete-confirm-btn"
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

// ---- Transfers (ADR-0008/0018) --------------------------------------------

/** How a candidate's (or a linked pair's) residual reads to a person.
 *
 * The three cases are kept apart on purpose. A `null` residual is not "free":
 * same-currency legs really do cancel, but a cross-currency pair with `null` has
 * no rate behind it, and saying "no FX cost" there would be the silent zero the
 * FX module exists to prevent (ADR-0017). Never hides it (ADR-0018).
 */
function residualText(crossCurrency: boolean, cost: Money | null, base: string): string {
  if (cost !== null) return `FX cost ${formatMoney(cost, base)}`;
  if (!crossCurrency) return "No FX cost — same currency";
  return "FX cost unknown — no exchange rate for one of the legs yet";
}

function legsApart(days: number): string {
  if (days === 0) return "same day";
  return `${days} day${days === 1 ? "" : "s"} apart`;
}

function TransferSection({
  txn,
  accounts,
  onNavigate,
  onReplaced,
}: {
  txn: Transaction;
  accounts: Account[];
  /** Point the sheet at another transaction (the counterpart leg). */
  onNavigate: (t: Transaction) => void;
  /** This transaction changed in place — it was just linked or unlinked. */
  onReplaced: (t: Transaction) => void;
}) {
  // The residual is in the household's base currency, whatever the legs' own
  // currencies are, so it is formatted with the household's, not the leg's.
  const household = useHousehold();
  const base = household.data?.base_currency ?? txn.currency;
  const groupId = txn.transfer_group_id;

  const transfer = useTransfer(groupId);
  const unlink = useUnlinkTransfer();
  const link = useLinkTransfer();
  const [matching, setMatching] = useState(false);
  // Only fetched once the user asks: this is a picker, not something to run on
  // every row the sheet opens.
  const candidates = useTransferCandidates(matching ? txn.id : null);

  // Whole accounts, because the mark beside each leg needs the institution as
  // well as the name — a transfer between two accounts at one bank is exactly
  // where a colour alone would fail to separate them.
  const accountOf = (id: string) => accounts.find((a) => a.id === id);
  const accountName = (id: string) => accountOf(id)?.name ?? "another account";

  const label = (t: Transaction) => t.merchant || t.description || "(no description)";

  if (groupId) {
    const legs = transfer.data?.legs ?? [];
    const counterpart = legs.find((l) => l.id !== txn.id);
    const cost = transfer.data?.fx_cost_base ?? null;
    const crossCurrency =
      counterpart !== undefined && counterpart.currency !== txn.currency;
    return (
      <div className="rounded-control border border-accent/40 bg-accent/5 p-3" data-testid="transfer-section">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-fg">
            Transfer
            <span className="ml-2 text-xs text-fg-muted">
              excluded from cash flow and spending
            </span>
          </p>
          <Button
            variant="secondary"
            className="px-2 py-1 text-xs"
            disabled={unlink.isPending}
            onClick={() =>
              unlink.mutate(groupId, {
                // The server has cleared the link by now; mirror that here so the
                // sheet immediately offers to match again instead of showing a
                // dead link until the list refetches.
                onSuccess: () => onReplaced({ ...txn, transfer_group_id: null }),
              })
            }
            data-testid="transfer-unlink"
          >
            Unlink
          </Button>
        </div>

        {transfer.isLoading && (
          <p className="mt-2 text-xs text-fg-muted">Loading the other leg…</p>
        )}
        {transfer.isError && (
          <p className="mt-2 text-sm text-negative">{(transfer.error as Error).message}</p>
        )}

        {counterpart && (
          <button
            type="button"
            onClick={() => onNavigate(counterpart)}
            className="mt-2 w-full rounded-control bg-surface-inset/60 p-2 text-left hover:bg-surface-inset"
            data-testid="transfer-counterpart"
          >
            <p className="text-sm text-fg">{label(counterpart)}</p>
            <p className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted">
              <AccountMark
                name={accountName(counterpart.account_id)}
                institution={accountOf(counterpart.account_id)?.institution}
              />
              <Day value={counterpart.transacted_at} style="compact" />
              {` · ${accountName(counterpart.account_id)}`}
            </p>
            <p className="mt-1 text-sm text-fg">
              {formatMoney(counterpart.amount, counterpart.currency)}
            </p>
          </button>
        )}
        {!transfer.isLoading && !transfer.isError && !counterpart && (
          <p className="mt-2 text-xs text-fg-muted">The other leg of this transfer is missing.</p>
        )}

        {counterpart && (
          <p
            className={`mt-2 text-xs ${cost !== null ? "font-medium text-warning" : "text-fg-muted"}`}
            data-testid="transfer-fx-cost"
          >
            {residualText(crossCurrency, cost, base)}
          </p>
        )}
        {unlink.isError && (
          <p className="mt-2 text-sm text-negative">{(unlink.error as Error).message}</p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-control border border-border p-3" data-testid="transfer-section">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-fg">Transfer</p>
        {!matching ? (
          <Button
            variant="secondary"
            className="px-2 py-1 text-xs"
            onClick={() => setMatching(true)}
            data-testid="transfer-match-open"
          >
            Match a transfer
          </Button>
        ) : (
          <span className="text-xs text-fg-muted">
            opposite sign, another account, within 5 days
          </span>
        )}
      </div>

      {matching && (
        <div className="mt-3 space-y-2">
          {candidates.isLoading && (
            <p className="text-xs text-fg-muted">Looking for the other leg…</p>
          )}
          {candidates.isError && (
            <p className="text-sm text-negative">{(candidates.error as Error).message}</p>
          )}

          {candidates.data?.items.map((c) => {
            const other = c.transaction;
            const crossCurrency = other.currency !== txn.currency;
            return (
              <button
                key={other.id}
                type="button"
                disabled={link.isPending}
                onClick={() =>
                  link.mutate(
                    { from_txn_id: txn.id, to_txn_id: other.id },
                    {
                      onSuccess: (group) =>
                        onReplaced({ ...txn, transfer_group_id: group.transfer_group_id }),
                    },
                  )
                }
                className="w-full rounded-control bg-surface-inset/60 p-2 text-left hover:bg-surface-inset disabled:opacity-50"
                data-testid={`transfer-candidate-${other.id}`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 truncate text-sm text-fg">{label(other)}</p>
                  <p className="shrink-0 text-sm text-fg">
                    {formatMoney(other.amount, other.currency)}
                  </p>
                </div>
                <p className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted">
                  {/* The mark is what separates these candidates at a glance:
                      they are all "opposite sign, another account, within 5
                      days", so the account is the only thing distinguishing
                      one row from the next. */}
                  <AccountMark
                    name={accountName(other.account_id)}
                    institution={accountOf(other.account_id)?.institution}
                  />
                  <Day value={other.transacted_at} style="compact" />
                  {` · ${legsApart(c.days_apart)} · ${accountName(other.account_id)}`}
                </p>
                {/* The price of this choice, before the user makes it. */}
                <p
                  className={`mt-1 text-xs ${
                    c.fx_cost_base !== null ? "font-medium text-warning" : "text-fg-muted"
                  }`}
                  data-testid={`transfer-candidate-cost-${other.id}`}
                >
                  {residualText(crossCurrency, c.fx_cost_base, base)}
                  {c.fx_cost_base !== null && !c.within_tolerance
                    ? " — wider than a bank spread usually is; check it is the right leg"
                    : ""}
                </p>
              </button>
            );
          })}

          {candidates.data && candidates.data.items.length === 0 && (
            <p className="text-xs text-fg-muted" data-testid="transfer-no-candidates">
              Nothing in this household looks like the other leg.
            </p>
          )}
          {link.isError && (
            <p className="text-sm text-negative">{(link.error as Error).message}</p>
          )}

          <Button
            variant="ghost"
            className="text-xs"
            onClick={() => setMatching(false)}
            data-testid="transfer-match-close"
          >
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}

interface SplitRow {
  amount: string;
  category_id: string;
  owner_id: string | null;
  notes: string;
}

function blankRow(): SplitRow {
  return { amount: "", category_id: "", owner_id: null, notes: "" };
}

function SplitEditor({
  txn,
  currency,
  categories,
  inheritFrom,
  onReplaced,
}: {
  txn: Transaction;
  currency: string;
  categories: Category[];
  /** Owner a split with no owner of its own resolves to. */
  inheritFrom: string;
  onReplaced: (updated: Transaction) => void;
}) {
  const replace = useReplaceSplits();
  const [open, setOpen] = useState(txn.splits.length > 0);
  const [rows, setRows] = useState<SplitRow[]>(() =>
    txn.splits.length > 0
      ? txn.splits.map((s) => ({
          amount: s.amount,
          category_id: s.category_id ?? "",
          owner_id: s.owner_id,
          notes: s.notes ?? "",
        }))
      : [blankRow(), blankRow()],
  );

  const target = Number(txn.amount);
  const sum = useMemo(
    () => rows.reduce((acc, r) => acc + (Number(r.amount) || 0), 0),
    [rows],
  );
  const balanced = Math.abs(sum - target) < 0.005;

  const setRow = (i: number, patch: Partial<SplitRow>) =>
    setRows((cur) => cur.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((cur) => [...cur, blankRow()]);
  const removeRow = (i: number) => setRows((cur) => cur.filter((_, idx) => idx !== i));

  const rowsValid = rows.every((r) => !validAmount(r.amount));

  const saveSplits = () => {
    const body: SplitIn[] = rows.map((r) => ({
      amount: r.amount,
      category_id: r.category_id || null,
      owner_id: r.owner_id,
      notes: r.notes || null,
    }));
    replace.mutate({ id: txn.id, splits: body }, { onSuccess: onReplaced });
  };

  const unsplit = () =>
    replace.mutate(
      { id: txn.id, splits: [] },
      {
        onSuccess: (updated) => {
          onReplaced(updated);
          setRows([blankRow(), blankRow()]);
          setOpen(false);
        },
      },
    );

  return (
    <div className="rounded-control border border-border p-3" data-testid="split-editor">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-fg">Split</p>
        {!open ? (
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => setOpen(true)} data-testid="split-open">
            Split transaction
          </Button>
        ) : (
          <span className="text-xs text-fg-muted">Total {formatMoney(String(target), currency)}</span>
        )}
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="space-y-2 rounded-control bg-surface-inset/60 p-2" data-testid={`split-row-${i}`}>
              <div className="grid grid-cols-2 gap-2">
                <input
                  aria-label={`Split ${i + 1} amount`}
                  value={r.amount}
                  inputMode="decimal"
                  placeholder="Amount"
                  onChange={(e) => setRow(i, { amount: e.target.value })}
                  className="rounded-control border border-border-strong bg-surface-inset px-2 py-1.5 text-sm"
                  data-testid={`split-amount-${i}`}
                />
                <select
                  aria-label={`Split ${i + 1} category`}
                  value={r.category_id}
                  onChange={(e) => setRow(i, { category_id: e.target.value })}
                  className="rounded-control border border-border-strong bg-surface-inset px-2 py-1.5 text-sm"
                  data-testid={`split-category-${i}`}
                >
                  <option value="">Uncategorized</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 items-start gap-2">
                <OwnerSelect
                  value={r.owner_id}
                  onChange={(id) => setRow(i, { owner_id: id })}
                  nullable
                  inheritFrom={inheritFrom}
                  allowCreate={false}
                  testid={`split-owner-${i}`}
                />
                <button
                  type="button"
                  onClick={() => removeRow(i)}
                  className="mt-5 rounded-control border border-border-strong px-2 py-1.5 text-sm text-fg-muted hover:text-negative"
                  data-testid={`split-remove-${i}`}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}

          <Button variant="ghost" className="text-xs" onClick={addRow} data-testid="split-add">
            + Add split
          </Button>

          <p
            className={`text-xs ${balanced ? "text-positive" : "text-warning"}`}
            data-testid="split-sum"
          >
            Splits sum {formatMoney(String(sum), currency)} — must equal {formatMoney(String(target), currency)}
          </p>

          {replace.isError && <p className="text-sm text-negative">{(replace.error as Error).message}</p>}

          <div className="flex gap-2">
            <Button
              onClick={saveSplits}
              disabled={!balanced || !rowsValid || rows.length === 0 || replace.isPending}
              data-testid="split-save"
            >
              Save splits
            </Button>
            {txn.splits.length > 0 && (
              <Button variant="secondary" onClick={unsplit} disabled={replace.isPending} data-testid="split-unsplit">
                Un-split
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
