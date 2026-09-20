// Slide-over sheet to edit a single transaction: core fields, owner, category,
// tags, notes, review status, plus an inline split editor (by $ or %) that saves
// through the dedicated splits endpoint. Delete lives here too (with confirm).
import { useMemo, useState } from "react";
import {
  useDeleteTransaction,
  useOwners,
  useReplaceSplits,
  useUpdateTransaction,
} from "@/api/hooks";
import type { Account, Category, SplitIn, Tag, Transaction } from "@/api/types";
import { formatMoney } from "@/lib/format";
import Dialog from "@/components/Dialog";
import OwnerSelect from "@/components/OwnerSelect";
import { Button, Field, Input, Select, Textarea, useFieldId, validAmount } from "@/components/form";

export default function TxnDetailSheet({
  txn,
  accounts,
  categories,
  tags,
  onClose,
  onReplaced,
}: {
  txn: Transaction;
  accounts: Account[];
  categories: Category[];
  tags: Tag[];
  onClose: () => void;
  onReplaced: (updated: Transaction) => void;
}) {
  const update = useUpdateTransaction();
  const del = useDeleteTransaction();
  const owners = useOwners();

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
                <option key={c.id} value={c.id}>{c.name}</option>
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
          <p className="text-xs text-slate-500" data-testid="detail-owner-effective">
            Effective owner: {ownerName.get(account?.owner_id ?? "") ?? "—"}
            {account && ` (from ${account.name})`}
          </p>
        )}

        <div>
          <p className="mb-1 text-xs font-medium text-slate-400">Tags</p>
          <div className="flex flex-wrap gap-2" data-testid="detail-tags">
            {tags.map((t) => {
              const on = tagIds.includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => toggleTag(t.id)}
                  aria-pressed={on}
                  className={`rounded-full border px-3 py-1 text-xs ${
                    on ? "border-brand bg-brand/20 text-brand" : "border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                  data-testid={`detail-tag-${t.id}`}
                >
                  {t.name}
                </button>
              );
            })}
            {tags.length === 0 && <span className="text-xs text-slate-500">No tags yet.</span>}
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

        {update.isError && <p className="text-sm text-red-400">{(update.error as Error).message}</p>}

        <SplitEditor
          txn={txn}
          currency={account?.currency ?? txn.currency}
          categories={categories}
          inheritFrom={ownerName.get(txn.effective_owner_id) ?? "transaction"}
          onReplaced={onReplaced}
        />

        {confirmDel && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm" data-testid="txn-delete-confirm">
            <p className="text-red-200">Delete this transaction? This cannot be undone.</p>
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
    <div className="rounded-lg border border-slate-800 p-3" data-testid="split-editor">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-slate-200">Split</p>
        {!open ? (
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => setOpen(true)} data-testid="split-open">
            Split transaction
          </Button>
        ) : (
          <span className="text-xs text-slate-500">Total {formatMoney(String(target), currency)}</span>
        )}
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="space-y-2 rounded-lg bg-slate-800/60 p-2" data-testid={`split-row-${i}`}>
              <div className="grid grid-cols-2 gap-2">
                <input
                  aria-label={`Split ${i + 1} amount`}
                  value={r.amount}
                  inputMode="decimal"
                  placeholder="Amount"
                  onChange={(e) => setRow(i, { amount: e.target.value })}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm"
                  data-testid={`split-amount-${i}`}
                />
                <select
                  aria-label={`Split ${i + 1} category`}
                  value={r.category_id}
                  onChange={(e) => setRow(i, { category_id: e.target.value })}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm"
                  data-testid={`split-category-${i}`}
                >
                  <option value="">Uncategorized</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
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
                  className="mt-5 rounded-lg border border-slate-700 px-2 py-1.5 text-sm text-slate-400 hover:text-red-300"
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
            className={`text-xs ${balanced ? "text-emerald-400" : "text-amber-400"}`}
            data-testid="split-sum"
          >
            Splits sum {formatMoney(String(sum), currency)} — must equal {formatMoney(String(target), currency)}
          </p>

          {replace.isError && <p className="text-sm text-red-400">{(replace.error as Error).message}</p>}

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
