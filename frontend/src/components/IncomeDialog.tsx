// Owner income profile + paystubs (ADR-0052). Settings → Owners' "Income &
// pay" entry opens this. Not read by Insights yet — a data-entry surface only.
import { useState } from "react";
import {
  useCreatePaystub,
  useDeletePaystub,
  useIncomeProfile,
  usePaystubs,
  useUpdateIncomeProfile,
  useUpdatePaystub,
} from "@/api/income";
import type {
  FilingStatus,
  Owner,
  PayFrequency,
  Paystub,
  PaystubLineIn,
  PaystubLineKind,
  UUID,
} from "@/api/types";
import Dialog from "@/components/Dialog";
import { Day } from "@/components/datetime";
import { Button, Field, Input, Select, Spinner, useFieldId, validAmount } from "@/components/form";
import { formatMoney } from "@/lib/format";
import { todayIso } from "@/lib/dates";

const FREQUENCIES: { value: PayFrequency; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "biweekly", label: "Biweekly" },
  { value: "semimonthly", label: "Semimonthly" },
  { value: "monthly", label: "Monthly" },
  { value: "annual", label: "Annual" },
];

const FILING_STATUSES: { value: FilingStatus; label: string }[] = [
  { value: "single", label: "Single" },
  { value: "married_joint", label: "Married filing jointly" },
  { value: "married_separate", label: "Married filing separately" },
  { value: "head_of_household", label: "Head of household" },
];

//: Display order and label for a paystub line's kind — the order a real stub
//: lists things in (what you earned, then what came out of it).
const LINE_KINDS: { value: PaystubLineKind; label: string }[] = [
  { value: "earning", label: "Earning" },
  { value: "pre_tax_deduction", label: "Pre-tax deduction" },
  { value: "tax", label: "Tax" },
  { value: "post_tax_deduction", label: "Post-tax deduction" },
  { value: "employer_contribution", label: "Employer contribution" },
];

const KIND_LABEL = Object.fromEntries(LINE_KINDS.map((k) => [k.value, k.label])) as Record<
  PaystubLineKind, string
>;

function kindTitle(kind: PaystubLineKind): string {
  return KIND_LABEL[kind] ?? kind;
}

export default function IncomeDialog({
  owner,
  householdCurrency,
  onClose,
}: {
  owner: Owner;
  householdCurrency: string;
  onClose: () => void;
}) {
  const profileQuery = useIncomeProfile(owner.id);
  const paystubsQuery = usePaystubs(owner.id);
  const [editing, setEditing] = useState<Paystub | "new" | null>(null);

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Income & pay — ${owner.name}`}
      testid="income-dialog"
    >
      {editing !== null ? (
        <PaystubEditor
          ownerId={owner.id}
          currency={profileQuery.data?.profile?.currency ?? householdCurrency}
          paystub={editing === "new" ? null : editing}
          onDone={() => setEditing(null)}
        />
      ) : (
        <div className="space-y-6">
          {profileQuery.isPending && <Spinner />}
          {profileQuery.isError && (
            <p className="text-sm text-negative" role="alert">
              {(profileQuery.error as Error).message}
            </p>
          )}
          {profileQuery.data && (
            <ProfileForm
              ownerId={owner.id}
              householdCurrency={householdCurrency}
              data={profileQuery.data}
            />
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-fg">Paystubs</h3>
              <Button variant="secondary" onClick={() => setEditing("new")} data-testid="add-paystub">
                Add paystub
              </Button>
            </div>
            {paystubsQuery.isPending && <Spinner />}
            {paystubsQuery.data && paystubsQuery.data.length === 0 && (
              <p className="text-sm text-fg-muted" data-testid="no-paystubs">
                No paystubs logged yet.
              </p>
            )}
            {paystubsQuery.data && paystubsQuery.data.length > 0 && (
              <ul className="divide-y divide-border rounded-control bg-surface-inset/40" data-testid="paystub-list">
                {paystubsQuery.data.map((p) => (
                  <PaystubRow key={p.id} ownerId={owner.id} paystub={p} onEdit={() => setEditing(p)} />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

// ---- profile + summary -----------------------------------------------------

function ProfileForm({
  ownerId,
  householdCurrency,
  data,
}: {
  ownerId: UUID;
  householdCurrency: string;
  data: NonNullable<ReturnType<typeof useIncomeProfile>["data"]>;
}) {
  const update = useUpdateIncomeProfile(ownerId);
  const p = data.profile;
  const [gross, setGross] = useState(p?.annual_gross_income ?? "");
  const [currency, setCurrency] = useState(p?.currency ?? householdCurrency);
  const [frequency, setFrequency] = useState<PayFrequency | "">(p?.pay_frequency ?? "");
  const [filingStatus, setFilingStatus] = useState<FilingStatus | "">(p?.filing_status ?? "");
  const [region, setRegion] = useState(p?.tax_region ?? "");
  const grossId = useFieldId("income-gross");
  const currencyId = useFieldId("income-currency");
  const frequencyId = useFieldId("income-frequency");
  const filingId = useFieldId("income-filing");
  const regionId = useFieldId("income-region");

  const summary = data.summary;
  const ytdTax = summary.ytd.find((r) => r.kind === "tax")?.amount ?? "0";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryTile label="Annualized gross" value={summary.annualized_gross} currency={currency} />
        <SummaryTile label="YTD tax" value={ytdTax} currency={currency} />
        <SummaryTile
          label="Effective tax rate"
          value={
            summary.effective_tax_rate
              ? `${(Number(summary.effective_tax_rate) * 100).toFixed(1)}%`
              : "—"
          }
        />
      </div>

      <form
        className="grid grid-cols-1 gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          update.mutate({
            annual_gross_income: gross.trim() ? gross.trim() : null,
            currency: currency.trim() || householdCurrency,
            pay_frequency: frequency || null,
            filing_status: filingStatus || null,
            tax_region: region.trim() || null,
          });
        }}
        data-testid="income-profile-form"
      >
        <Field label="Annual gross income" htmlFor={grossId}>
          <Input
            id={grossId}
            inputMode="decimal"
            value={gross}
            onChange={(e) => setGross(e.target.value)}
            placeholder="e.g. 120000"
            data-testid="income-gross"
          />
        </Field>
        <Field label="Currency" htmlFor={currencyId}>
          <Input
            id={currencyId}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            data-testid="income-currency"
          />
        </Field>
        <Field label="Pay frequency" htmlFor={frequencyId}>
          <Select
            id={frequencyId}
            value={frequency}
            onChange={(e) => setFrequency(e.target.value as PayFrequency | "")}
            data-testid="income-frequency"
          >
            <option value="">Not set</option>
            {FREQUENCIES.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Filing status" htmlFor={filingId}>
          <Select
            id={filingId}
            value={filingStatus}
            onChange={(e) => setFilingStatus(e.target.value as FilingStatus | "")}
            data-testid="income-filing-status"
          >
            <option value="">Not set</option>
            {FILING_STATUSES.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Tax region" htmlFor={regionId} hint='e.g. "US-CA"'>
          <Input
            id={regionId}
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            data-testid="income-region"
          />
        </Field>
        <div className="flex items-end">
          <Button type="submit" disabled={update.isPending} data-testid="income-profile-save">
            {update.isPending && <Spinner />}
            Save profile
          </Button>
        </div>
      </form>
      {update.isError && (
        <p className="text-sm text-negative" role="alert">{(update.error as Error).message}</p>
      )}
    </div>
  );
}

function SummaryTile({
  label, value, currency,
}: { label: string; value: string | null; currency?: string }) {
  return (
    <div className="rounded-card bg-surface-inset/60 p-3">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="text-base font-semibold text-fg" data-testid={`summary-${label.toLowerCase().replace(/\s+/g, "-")}`}>
        {value == null ? "—" : currency ? formatMoney(value, currency) : value}
      </p>
    </div>
  );
}

// ---- paystub list row -------------------------------------------------------

function PaystubRow({
  ownerId, paystub, onEdit,
}: { ownerId: UUID; paystub: Paystub; onEdit: () => void }) {
  const del = useDeletePaystub(ownerId);
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="space-y-2 p-3" data-testid={`paystub-row-${paystub.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="text-left text-sm font-medium text-fg underline-offset-2 hover:underline"
          onClick={onEdit}
          data-testid={`paystub-edit-${paystub.id}`}
        >
          <Day value={paystub.pay_date} /> {paystub.employer ? `— ${paystub.employer}` : ""}
        </button>
        <span className="text-base font-semibold text-fg">
          {formatMoney(paystub.net, paystub.currency)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" className="px-2 py-1 text-xs" onClick={onEdit}>Edit</Button>
        <Button
          variant="ghost"
          className="px-2 py-1 text-xs"
          onClick={() => setConfirming(true)}
          data-testid={`paystub-delete-${paystub.id}`}
        >
          Delete
        </Button>
      </div>
      {confirming && (
        <div className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm">
          <p className="text-negative">Delete this paystub? This cannot be undone.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setConfirming(false)}>Keep it</Button>
            <Button
              variant="danger"
              disabled={del.isPending}
              onClick={() => del.mutate(paystub.id, { onSuccess: () => setConfirming(false) })}
              data-testid={`paystub-delete-confirm-${paystub.id}`}
            >
              Yes, delete
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

// ---- the paystub editor: header + lines grouped by kind ---------------------

type DraftLine = PaystubLineIn & { key: string };

function draftFrom(paystub: Paystub | null): DraftLine[] {
  if (!paystub) return [];
  return paystub.lines.map((l) => ({
    key: l.id, kind: l.kind, label: l.label, amount: l.amount, ytd_amount: l.ytd_amount,
    position: l.position,
  }));
}

function sumKind(lines: DraftLine[], kind: PaystubLineKind): number {
  return lines
    .filter((l) => l.kind === kind)
    .reduce((acc, l) => acc + (Number(l.amount) || 0), 0);
}

function PaystubEditor({
  ownerId, currency, paystub, onDone,
}: { ownerId: UUID; currency: string; paystub: Paystub | null; onDone: () => void }) {
  const create = useCreatePaystub(ownerId);
  const update = useUpdatePaystub(ownerId);
  const [payDate, setPayDate] = useState(paystub?.pay_date ?? todayIso());
  const [employer, setEmployer] = useState(paystub?.employer ?? "");
  const [gross, setGross] = useState(paystub?.gross ?? "");
  const [net, setNet] = useState(paystub?.net ?? "");
  const [lines, setLines] = useState<DraftLine[]>(() => draftFrom(paystub));
  const [error, setError] = useState<string | null>(null);

  const payDateId = useFieldId("paystub-date");
  const employerId = useFieldId("paystub-employer");
  const grossId = useFieldId("paystub-gross");
  const netId = useFieldId("paystub-net");

  // The worksheet: what the lines add up to, live, so a mismatch shows before
  // submitting rather than as a 422 after.
  const computedGross = sumKind(lines, "earning");
  const computedNet =
    computedGross
    - sumKind(lines, "pre_tax_deduction")
    - sumKind(lines, "tax")
    - sumKind(lines, "post_tax_deduction");
  const hasLines = lines.length > 0;
  const grossMismatch = hasLines && Math.abs(computedGross - (Number(gross) || 0)) > 0.01;
  const netMismatch = hasLines && Math.abs(computedNet - (Number(net) || 0)) > 0.01;

  function addLine(kind: PaystubLineKind) {
    setLines((prev) => [
      ...prev,
      { key: crypto.randomUUID(), kind, label: "", amount: "0", position: prev.length },
    ]);
  }
  function updateLine(key: string, patch: Partial<DraftLine>) {
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }
  function removeLine(key: string) {
    setLines((prev) => prev.filter((l) => l.key !== key));
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const grossErr = validAmount(gross);
    const netErr = validAmount(net);
    if (grossErr || netErr) {
      setError(grossErr ?? netErr);
      return;
    }
    setError(null);
    const body = {
      pay_date: payDate,
      employer: employer.trim() || null,
      currency,
      gross: gross.trim(),
      net: net.trim(),
      lines: lines.map((l, i) => ({
        kind: l.kind, label: l.label.trim() || kindTitle(l.kind), amount: l.amount || "0",
        ytd_amount: l.ytd_amount || null, position: i,
      })),
    };
    const onSuccess = () => onDone();
    const onError = (err: Error) => setError(err.message);
    if (paystub) {
      update.mutate({ id: paystub.id, body }, { onSuccess, onError });
    } else {
      create.mutate(body, { onSuccess, onError });
    }
  }

  const pending = create.isPending || update.isPending;

  return (
    <form className="space-y-4" onSubmit={submit} data-testid="paystub-form">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Pay date" htmlFor={payDateId} required>
          <Input
            id={payDateId} type="date" value={payDate}
            onChange={(e) => setPayDate(e.target.value)}
            data-testid="paystub-date"
          />
        </Field>
        <Field label="Employer" htmlFor={employerId}>
          <Input
            id={employerId} value={employer} onChange={(e) => setEmployer(e.target.value)}
            data-testid="paystub-employer"
          />
        </Field>
        <Field label="Gross" htmlFor={grossId} required>
          <Input
            id={grossId} inputMode="decimal" value={gross}
            onChange={(e) => setGross(e.target.value)}
            data-testid="paystub-gross"
          />
        </Field>
        <Field label="Net" htmlFor={netId} required>
          <Input
            id={netId} inputMode="decimal" value={net}
            onChange={(e) => setNet(e.target.value)}
            data-testid="paystub-net"
          />
        </Field>
      </div>

      <div className="space-y-3">
        <h4 className="text-sm font-semibold text-fg">Lines</h4>
        {LINE_KINDS.map((k) => {
          const rows = lines.filter((l) => l.kind === k.value);
          return (
            <div key={k.value} className="space-y-2 rounded-control bg-surface-inset/40 p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-fg-muted">{k.label}</span>
                <Button
                  type="button" variant="ghost" className="px-2 py-1 text-xs"
                  onClick={() => addLine(k.value)}
                  data-testid={`paystub-add-line-${k.value}`}
                >
                  + Add
                </Button>
              </div>
              {rows.map((line) => (
                <div key={line.key} className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <Input
                    aria-label={`${k.label} label`}
                    value={line.label}
                    onChange={(e) => updateLine(line.key, { label: e.target.value })}
                    placeholder={k.label}
                    className="sm:flex-1"
                    data-testid={`line-label-${line.key}`}
                  />
                  <Input
                    aria-label={`${k.label} amount`}
                    inputMode="decimal"
                    value={line.amount}
                    onChange={(e) => updateLine(line.key, { amount: e.target.value })}
                    className="sm:w-32"
                    data-testid={`line-amount-${line.key}`}
                  />
                  <Button
                    type="button" variant="ghost" className="self-start px-2 py-1 text-xs sm:self-auto"
                    onClick={() => removeLine(line.key)}
                    data-testid={`line-remove-${line.key}`}
                  >
                    Remove
                  </Button>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {hasLines && (
        <div
          className={`rounded-control p-3 text-sm ${
            grossMismatch || netMismatch ? "bg-warning/15 text-warning-ink" : "bg-positive/10 text-fg"
          }`}
          data-testid="paystub-footer"
        >
          <p>Lines total: gross {computedGross.toFixed(2)}, net {computedNet.toFixed(2)}</p>
          {(grossMismatch || netMismatch) && (
            <p className="font-medium" data-testid="paystub-mismatch">
              Doesn’t match the header above yet — the lines must sum to gross and net.
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="text-sm text-negative" role="alert" data-testid="paystub-error">{error}</p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={pending} data-testid="paystub-save">
          {pending && <Spinner />}
          Save paystub
        </Button>
        <Button type="button" variant="ghost" onClick={onDone} data-testid="paystub-cancel">
          Cancel
        </Button>
      </div>
    </form>
  );
}
