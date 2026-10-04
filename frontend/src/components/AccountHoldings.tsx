import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Account, Holding, HoldingValue, Security } from "@/api/types";
import { usePortfolio } from "@/api/investments";
import { Button, Field, Input, Select, Spinner, useFieldId } from "@/components/form";
import { Day } from "@/components/datetime";
import { todayIso } from "@/lib/dates";
import { formatMoney } from "@/lib/format";
import { sameQuantity, securityTypeLabel, trimDecimal } from "@/lib/investments";

const TYPES = ["stock", "etf", "mutual_fund", "bond", "option", "crypto", "cash", "other"];

export default function AccountHoldings({ account, canEdit = true }: { account: Account; canEdit?: boolean }) {
  const qc = useQueryClient();
  const holdings = useQuery({ queryKey: ["holdings", account.id], queryFn: () => api.get<Holding[]>(`/investments/holdings?account_id=${account.id}`) });
  const portfolio = usePortfolio();
  const values = portfolio.data?.accounts.find((a) => a.account_id === account.id)?.holdings;
  const [editing, setEditing] = useState<Holding | "new" | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const invalidate = async () => {
    await Promise.all(["holdings", "portfolio", "allocation", "accounts", "net-worth"].map((key) => qc.invalidateQueries({ queryKey: [key] })));
  };
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/investments/holdings/${id}`), onSuccess: async () => { setDeleting(null); await invalidate(); } });
  const reset = useMutation({ mutationFn: (id: string) => api.patch(`/investments/holdings/${id}`, { reset_overrides: true }), onSuccess: invalidate });
  return <section className="space-y-3" data-testid="account-holdings-editor">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold">Holdings</h3>
      {canEdit && editing === null && <Button variant="secondary" onClick={() => setEditing("new")}>Add holding</Button>}
    </div>
    {holdings.isPending && <Spinner />}
    {holdings.isError && <p className="text-sm text-negative" role="alert">{holdings.error.message}</p>}
    {(remove.isError || reset.isError) && <p className="text-sm text-negative" role="alert">{(remove.error ?? reset.error)?.message}</p>}
    {editing !== null ? <HoldingForm account={account} holding={editing === "new" ? null : editing} value={editing === "new" ? undefined : values?.find((v) => v.security_id === editing.security_id)} onCancel={() => setEditing(null)} onSaved={async () => { setEditing(null); await invalidate(); }} /> : <>
      {holdings.data?.length === 0 && <p className="text-sm text-fg-muted">No holdings yet.</p>}
      <ul className="divide-y divide-border">
        {holdings.data?.map((h) => {
          const value = values?.find((v) => v.security_id === h.security_id);
          return <li key={h.security_id} className="space-y-2 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0"><p className="break-words font-medium">{h.security.name}{h.security.ticker && ` · ${h.security.ticker}`}</p><p className="text-sm text-fg-muted">{securityTypeLabel(h.security.security_type)} · {trimDecimal(h.quantity)} units · {h.is_override ? "Manual override" : h.quantity_source === "provider" ? "From your bank" : h.quantity_source === "history" ? "From recorded trades" : "Manual"}</p></div>
              {value?.value_native != null && <p className="text-base font-semibold">{formatMoney(value.value_native, h.security.currency)}</p>}
            </div>
            {/* A total set by hand is said on every kind of row — manual ones too —
                so a pinned value never reads as a market price. */}
            {h.market_value_override != null && h.market_value_override_as_of && <p className="text-xs text-fg-muted" data-testid={`holding-pinned-${h.security_id}`}>Value set by you · <Day value={h.market_value_override_as_of} /></p>}
            {h.as_of && <p className="text-xs text-fg-muted">Quantity as of <Day value={h.as_of} /></p>}
            {canEdit && h.id && <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" onClick={() => setEditing(h)}>Edit</Button>
              {h.is_override && <Button variant="ghost" disabled={reset.isPending} onClick={() => reset.mutate(h.id!)}>Use bank updates</Button>}
              {h.quantity_source !== "history" && (deleting === h.id ? <div className="flex flex-wrap items-center gap-2"><span className="text-sm">Delete this holding?</span><Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(h.id!)}>Confirm delete</Button><Button variant="ghost" onClick={() => setDeleting(null)}>Cancel</Button></div> : <Button variant="ghost" className="ml-auto" onClick={() => setDeleting(h.id)}>Delete</Button>)}
            </div>}
          </li>;
        })}
      </ul>
      {reset.isSuccess && <p className="text-sm text-fg-muted" role="status">Bank updates enabled. The next sync refreshes this holding.</p>}
    </>}
  </section>;
}

/** Whether a number field holds a different number than it started with —
 *  `"10"` retyped over `"10.00000000"` is not a change. */
function numberChanged(now: string, before: string): boolean {
  if (now === before) return false;
  return !(now !== "" && before !== "" && sameQuantity(now, before));
}

function HoldingForm({ account, holding, value, onCancel, onSaved }: { account: Account; holding: Holding | null; value?: HoldingValue; onCancel: () => void; onSaved: () => Promise<void> }) {
  const prefix = useFieldId("holding");
  const [name, setName] = useState(holding?.security.name ?? "");
  const [ticker, setTicker] = useState(holding?.security.ticker ?? "");
  const [type, setType] = useState(holding?.security.security_type ?? "stock");
  const [currency, setCurrency] = useState(holding?.security.currency ?? account.currency);
  const [quantity, setQuantity] = useState(holding?.quantity ?? "1");
  const [initialMarketValue] = useState(value?.value_native ?? "");
  const [marketValue, setMarketValue] = useState(initialMarketValue);
  const historyOwned = holding?.quantity_source === "history";
  const [basis, setBasis] = useState(holding?.cost_basis ?? "");
  // An existing holding keeps the date its quantity was confirmed; only a new one
  // starts at today.
  const [initialAsOf] = useState(holding ? holding.as_of ?? "" : todayIso());
  const [asOf, setAsOf] = useState(initialAsOf);
  const save = useMutation({ mutationFn: async () => {
    const symbol = ticker.trim().toUpperCase() || null;
    // An unchanged value is never sent: what the field shows may be a market
    // valuation, and sending it back would pin it.
    const valueChange = marketValue !== initialMarketValue ? { market_value: marketValue || null } : {};
    if (holding?.id) {
      // Only what changed. Writing a bank position's quantity, basis or date
      // stops its bank updates (ADR-0059), so an untouched one must not be sent.
      const body = {
        ...(name.trim() !== holding.security.name ? { name: name.trim() } : {}),
        ...(symbol !== (holding.security.ticker || null) ? { ticker: symbol } : {}),
        ...(type !== holding.security.security_type ? { security_type: type } : {}),
        ...(!historyOwned && numberChanged(quantity, holding.quantity) ? { quantity } : {}),
        ...(!historyOwned && numberChanged(basis, holding.cost_basis ?? "") ? { cost_basis: basis || null } : {}),
        ...valueChange,
        ...(asOf !== initialAsOf ? { as_of: asOf || null } : {}),
      };
      return api.patch(`/investments/holdings/${holding.id}`, body);
    }
    const securities = await api.get<Security[]>("/investments/securities");
    const existing = symbol ? securities.find((s) => s.ticker === symbol && s.currency === currency) : undefined;
    const security = existing ?? await api.post<Security>("/investments/securities", { name: name.trim(), ticker: symbol, security_type: type, currency });
    // A local name or type only where it differs from the security's own.
    return api.post("/investments/holdings", {
      account_id: account.id, security_id: security.id, quantity, cost_basis: basis || null, as_of: asOf || null,
      ...(name.trim() !== security.name ? { name: name.trim() } : {}),
      ...(type !== security.security_type ? { security_type: type } : {}),
      ...valueChange,
    });
  }, onSuccess: onSaved });
  return <form className="space-y-3 rounded-control border border-border p-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
    <p className="text-sm font-semibold">{holding ? "Edit holding" : "Add holding"}</p>
    {historyOwned && <p className="text-sm text-fg-muted">Quantity and cost basis come from recorded trades. You can edit the name, symbol, type and value here.</p>}
    {holding?.quantity_source === "provider" && <p className="text-sm text-fg-muted">A name, symbol, type or value you set here stays yours, and the bank keeps updating the rest. Changing the quantity, cost basis or date stops bank updates for this position until you choose “Use bank updates”.</p>}
    {holding?.is_override && <p className="text-sm text-fg-muted">Bank updates are stopped for this position. Choose “Use bank updates” to resume them.</p>}
    <div className="grid min-w-0 gap-3 sm:grid-cols-2">
      <Field label="Name" htmlFor={`${prefix}-name`} required><Input id={`${prefix}-name`} value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} /></Field>
      <Field label="Symbol" htmlFor={`${prefix}-ticker`}><Input id={`${prefix}-ticker`} value={ticker} onChange={(e) => setTicker(e.target.value)} maxLength={32} /></Field>
      <Field label="Type" htmlFor={`${prefix}-type`}><Select id={`${prefix}-type`} value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{securityTypeLabel(t)}</option>)}</Select></Field>
      <Field label="Quote currency" htmlFor={`${prefix}-currency`}><Input id={`${prefix}-currency`} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} required minLength={3} maxLength={3} disabled={!!holding} /></Field>
      <Field label="Quantity" htmlFor={`${prefix}-quantity`} required><Input id={`${prefix}-quantity`} type="number" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)} required disabled={historyOwned} /></Field>
      <Field label={`Market value · ${currency}`} htmlFor={`${prefix}-value`}><Input id={`${prefix}-value`} type="number" step="any" value={marketValue} onChange={(e) => setMarketValue(e.target.value)} /></Field>
      <Field label={`Total cost basis · ${account.currency}`} htmlFor={`${prefix}-basis`}><Input id={`${prefix}-basis`} type="number" step="any" value={basis} onChange={(e) => setBasis(e.target.value)} disabled={historyOwned} /></Field>
      <Field label="Quantity as of" htmlFor={`${prefix}-date`}><Input id={`${prefix}-date`} type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} required={!holding} /></Field>
    </div>
    {save.isError && <p className="text-sm text-negative" role="alert">{save.error.message}</p>}
    <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save holding"}</Button></div>
  </form>;
}
