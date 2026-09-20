import { useMemo, useState } from "react";
import { useAuth } from "@/auth/AuthContext";
import {
  useAccounts,
  useCategories,
  useCategoryGroups,
  useCreateCategory,
  useCreateCategoryGroup,
  useCreateOwner,
  useCreateTag,
  useDeleteCategory,
  useDeleteCategoryGroup,
  useDeleteOwner,
  useDeleteTag,
  useFxRates,
  useHousehold,
  useMembers,
  useOwners,
  useTags,
  useUpdateHousehold,
  useUpdateOwner,
  useUpsertFxRate,
} from "@/api/hooks";
import {
  useApplyRules,
  useDeleteRule,
  useRules,
  useUpdateRule,
  type Rule,
  type RuleActions,
  type RuleConditions,
  type RuleApplyResult,
} from "@/api/rules";
import RuleBuilder from "@/components/RuleBuilder";
import type { Owner, OwnerReassignment } from "@/api/types";
import { formatDate } from "@/lib/format";
import { todayIso } from "@/lib/dates";
import {
  Button,
  Field,
  Input,
  Select,
  requiredText,
  useFieldId,
  validCurrency,
  validRate,
} from "@/components/form";

const TABS = [
  { id: "categories", label: "Categories" },
  { id: "tags", label: "Tags" },
  { id: "currencies", label: "Currencies" },
  { id: "household", label: "Household" },
  { id: "owners", label: "Owners" },
  { id: "rules", label: "Rules" },
  { id: "profile", label: "Profile" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [tab, setTab] = useState<TabId>("categories");
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Settings</h2>
      <div className="flex flex-wrap gap-1 border-b border-border" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-t-lg px-3 py-2 text-sm ${
              tab === t.id ? "border-b-2 border-accent text-fg" : "text-fg-muted hover:text-fg"
            }`}
            data-testid={`settings-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div data-testid={`settings-panel-${tab}`}>
        {tab === "categories" && <CategoriesSection />}
        {tab === "tags" && <TagsSection />}
        {tab === "currencies" && <CurrenciesSection />}
        {tab === "household" && <HouseholdSection />}
        {tab === "owners" && <OwnersSection />}
        {tab === "rules" && <RulesSection />}
        {tab === "profile" && <ProfileSection />}
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-card bg-surface-raised p-4">
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

// -------------------------------------------------------------- categories

function CategoriesSection() {
  const groups = useCategoryGroups();
  const categories = useCategories();
  const createGroup = useCreateCategoryGroup();
  const delGroup = useDeleteCategoryGroup();
  const createCat = useCreateCategory();
  const delCat = useDeleteCategory();

  const [gName, setGName] = useState("");
  const [gType, setGType] = useState("expense");
  const [cName, setCName] = useState("");
  const [cGroup, setCGroup] = useState("");
  const [cColor, setCColor] = useState("#14b8a6");
  const [gErr, setGErr] = useState<string | null>(null);
  const [cErr, setCErr] = useState<string | null>(null);

  const ids = {
    gName: useFieldId("group-name"),
    gType: useFieldId("group-type"),
    cName: useFieldId("cat-name"),
    cGroup: useFieldId("cat-group"),
    cColor: useFieldId("cat-color"),
  };

  const byGroup = useMemo(() => {
    const m = new Map<string, typeof categories.data>();
    categories.data?.forEach((c) => {
      const arr = m.get(c.group_id) ?? [];
      arr.push(c);
      m.set(c.group_id, arr);
    });
    return m;
  }, [categories.data]);

  const defaultGroup = groups.data?.[0]?.id ?? "";

  return (
    <div className="space-y-4">
      <Card title="Add category group">
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-3"
          data-testid="add-group-form"
          onSubmit={(e) => {
            e.preventDefault();
            const err = requiredText(gName);
            setGErr(err);
            if (err) return;
            createGroup.mutate(
              { name: gName, type: gType },
              { onSuccess: () => setGName("") },
            );
          }}
        >
          <Field label="Group name" htmlFor={ids.gName} required error={gErr}>
            <Input id={ids.gName} value={gName} onChange={(e) => setGName(e.target.value)} data-testid="group-name" />
          </Field>
          <Field label="Type" htmlFor={ids.gType}>
            <Select id={ids.gType} value={gType} onChange={(e) => setGType(e.target.value)} data-testid="group-type">
              <option value="income">income</option>
              <option value="expense">expense</option>
              <option value="transfer">transfer</option>
            </Select>
          </Field>
          <div className="flex items-end">
            <Button type="submit" disabled={createGroup.isPending} data-testid="group-save">Add group</Button>
          </div>
        </form>
      </Card>

      <Card title="Add category">
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-4"
          data-testid="add-category-form"
          onSubmit={(e) => {
            e.preventDefault();
            const err = requiredText(cName) || requiredText(cGroup || defaultGroup);
            setCErr(err);
            if (err) return;
            createCat.mutate(
              { name: cName, group_id: cGroup || defaultGroup, color: cColor },
              { onSuccess: () => setCName("") },
            );
          }}
        >
          <Field label="Category name" htmlFor={ids.cName} required error={cErr}>
            <Input id={ids.cName} value={cName} onChange={(e) => setCName(e.target.value)} data-testid="category-name" />
          </Field>
          <Field label="Group" htmlFor={ids.cGroup}>
            <Select id={ids.cGroup} value={cGroup || defaultGroup} onChange={(e) => setCGroup(e.target.value)} data-testid="category-group">
              {groups.data?.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Color" htmlFor={ids.cColor}>
            <Input id={ids.cColor} type="color" value={cColor} onChange={(e) => setCColor(e.target.value)} data-testid="category-color" className="h-10 p-1" />
          </Field>
          <div className="flex items-end">
            <Button type="submit" disabled={createCat.isPending || !groups.data?.length} data-testid="category-save">Add category</Button>
          </div>
        </form>
      </Card>

      <Card title="Categories">
        <ul className="space-y-4" data-testid="category-list">
          {groups.data?.map((g) => (
            <li key={g.id}>
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-fg">
                  {g.name} <span className="text-xs text-fg-muted">({g.type})</span>
                </p>
                <button
                  className="text-xs text-fg-muted hover:text-negative"
                  onClick={() => delGroup.mutate(g.id)}
                  data-testid={`group-delete-${g.id}`}
                >
                  Delete group
                </button>
              </div>
              <ul className="mt-1 divide-y divide-border rounded-control bg-surface-inset/40">
                {(byGroup.get(g.id) ?? []).map((c) => (
                  <li key={c.id} className="flex items-center justify-between px-3 py-2 text-sm">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-3 w-3 rounded-full" style={{ background: c.color ?? "#64748b" }} />
                      {c.name}
                    </span>
                    <button
                      className="text-xs text-fg-muted hover:text-negative"
                      onClick={() => delCat.mutate(c.id)}
                      data-testid={`category-delete-${c.id}`}
                    >
                      Delete
                    </button>
                  </li>
                ))}
                {(byGroup.get(g.id) ?? []).length === 0 && (
                  <li className="px-3 py-2 text-xs text-fg-muted">No categories.</li>
                )}
              </ul>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

// -------------------------------------------------------------------- tags

function TagsSection() {
  const tags = useTags();
  const createTag = useCreateTag();
  const delTag = useDeleteTag();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#38bdf8");
  const [err, setErr] = useState<string | null>(null);
  const nameId = useFieldId("tag-name");
  const colorId = useFieldId("tag-color");

  return (
    <div className="space-y-4">
      <Card title="Add tag">
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-3"
          data-testid="add-tag-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = requiredText(name);
            setErr(v);
            if (v) return;
            createTag.mutate({ name, color }, { onSuccess: () => setName("") });
          }}
        >
          <Field label="Tag name" htmlFor={nameId} required error={err}>
            <Input id={nameId} value={name} onChange={(e) => setName(e.target.value)} data-testid="tag-name" />
          </Field>
          <Field label="Color" htmlFor={colorId}>
            <Input id={colorId} type="color" value={color} onChange={(e) => setColor(e.target.value)} data-testid="tag-color" className="h-10 p-1" />
          </Field>
          <div className="flex items-end">
            <Button type="submit" disabled={createTag.isPending} data-testid="tag-save">Add tag</Button>
          </div>
        </form>
      </Card>

      <Card title="Tags">
        <ul className="flex flex-wrap gap-2" data-testid="tag-list">
          {tags.data?.map((t) => (
            <li key={t.id} className="flex items-center gap-2 rounded-full bg-surface-inset px-3 py-1 text-sm">
              <span className="inline-block h-3 w-3 rounded-full" style={{ background: t.color ?? "#64748b" }} />
              {t.name}
              <button
                className="text-fg-muted hover:text-negative"
                onClick={() => delTag.mutate(t.id)}
                aria-label={`Delete ${t.name}`}
                data-testid={`tag-delete-${t.id}`}
              >
                ✕
              </button>
            </li>
          ))}
          {tags.data?.length === 0 && <li className="text-xs text-fg-muted">No tags yet.</li>}
        </ul>
      </Card>
    </div>
  );
}

// -------------------------------------------------------------- currencies

function CurrenciesSection() {
  const rates = useFxRates();
  const upsert = useUpsertFxRate();
  const household = useHousehold();
  const base = household.data?.base_currency ?? "USD";

  const [baseCcy, setBaseCcy] = useState(base);
  const [quote, setQuote] = useState("");
  const [rate, setRate] = useState("");
  const [date, setDate] = useState(() => todayIso());
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  const ids = {
    base: useFieldId("fx-base"),
    quote: useFieldId("fx-quote"),
    rate: useFieldId("fx-rate"),
    date: useFieldId("fx-date"),
  };

  return (
    <div className="space-y-4">
      <Card title="Add FX rate">
        <p className="text-xs text-fg-muted">
          One unit of <strong>base</strong> equals <strong>rate</strong> units of <strong>quote</strong>
          {" "}(e.g. 1 USD = 0.92 EUR).
        </p>
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-5"
          data-testid="add-fx-form"
          onSubmit={(e) => {
            e.preventDefault();
            const next = {
              base: validCurrency(baseCcy),
              quote: validCurrency(quote),
              rate: validRate(rate),
            };
            setErrs(next);
            if (next.base || next.quote || next.rate) return;
            upsert.mutate(
              {
                base_currency: baseCcy.toUpperCase(),
                quote_currency: quote.toUpperCase(),
                rate_date: date,
                rate,
              },
              { onSuccess: () => setRate("") },
            );
          }}
        >
          <Field label="Base" htmlFor={ids.base} required error={errs.base}>
            <Input id={ids.base} value={baseCcy} maxLength={3} onChange={(e) => setBaseCcy(e.target.value)} data-testid="fx-base" />
          </Field>
          <Field label="Quote" htmlFor={ids.quote} required error={errs.quote}>
            <Input id={ids.quote} value={quote} maxLength={3} onChange={(e) => setQuote(e.target.value)} data-testid="fx-quote" />
          </Field>
          <Field label="Rate" htmlFor={ids.rate} required error={errs.rate}>
            <Input id={ids.rate} value={rate} inputMode="decimal" onChange={(e) => setRate(e.target.value)} data-testid="fx-rate" />
          </Field>
          <Field label="Date" htmlFor={ids.date}>
            <Input id={ids.date} type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="fx-date" />
          </Field>
          <div className="flex items-end">
            <Button type="submit" disabled={upsert.isPending} data-testid="fx-save">Add rate</Button>
          </div>
        </form>
        {upsert.isError && <p className="text-sm text-negative">{(upsert.error as Error).message}</p>}
      </Card>

      <Card title="FX rates">
        <ul className="divide-y divide-border" data-testid="fx-list">
          {rates.data?.map((r) => (
            <li key={r.id} className="flex justify-between px-1 py-2 text-sm">
              <span>1 {r.base_currency} = {r.rate} {r.quote_currency}</span>
              <span className="text-fg-muted">{formatDate(r.rate_date)}</span>
            </li>
          ))}
          {rates.data?.length === 0 && <li className="py-2 text-xs text-fg-muted">No rates yet.</li>}
        </ul>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------- household

function HouseholdSection() {
  const { me } = useAuth();
  const household = useHousehold();
  const members = useMembers();
  const update = useUpdateHousehold();
  const isOwner = (household.data?.role ?? me?.role) === "owner";

  // null = untouched, so the field follows the server until the user types.
  const [draftName, setDraftName] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const nameId = useFieldId("household-name");
  const name = draftName ?? household.data?.name ?? "";
  const dirty = draftName !== null && draftName !== household.data?.name;

  return (
    <div className="space-y-4">
      <Card title="Household">
        <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2" data-testid="household-info">
          <div><dt className="text-fg-muted">Name</dt><dd>{household.data?.name ?? "—"}</dd></div>
          <div>
            <dt className="text-fg-muted">Base currency</dt>
            <dd>{household.data?.base_currency ?? "—"} <span className="text-xs text-fg-muted">(immutable)</span></dd>
          </div>
          <div><dt className="text-fg-muted">Timezone</dt><dd>{household.data?.timezone ?? "—"}</dd></div>
          <div><dt className="text-fg-muted">Your role</dt><dd>{household.data?.role ?? me?.role}</dd></div>
        </dl>

        {isOwner && (
          <form
            className="flex items-end gap-2"
            data-testid="household-form"
            onSubmit={(e) => {
              e.preventDefault();
              const v = requiredText(name);
              setErr(v);
              if (v) return;
              update.mutate({ name: name.trim() }, { onSuccess: () => setDraftName(null) });
            }}
          >
            <Field label="Household name" htmlFor={nameId} required error={err} className="flex-1">
              <Input
                id={nameId}
                value={name}
                onChange={(e) => setDraftName(e.target.value)}
                data-testid="household-name"
              />
            </Field>
            <Button type="submit" disabled={update.isPending || !dirty} data-testid="household-name-save">
              Save
            </Button>
          </form>
        )}
        {update.isError && <p className="text-sm text-negative">{(update.error as Error).message}</p>}
      </Card>

      <Card title="Members">
        {/* Signup is open, so there is nothing to invite: a new person creates
            their own account and lands in this household. */}
        <p className="text-xs text-fg-muted">
          Anyone can create an account from the sign-up page and will join this household as a
          member. Members are managed here, not on the ledger: what owns money are the owners below.
        </p>
        <table className="w-full text-sm" data-testid="members-table">
          <thead>
            <tr className="text-left text-xs text-fg-muted">
              <th className="py-1">Name</th><th className="py-1">Email</th><th className="py-1">Role</th>
            </tr>
          </thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.user_id} className="border-t border-border">
                <td className="py-1.5">{m.display_name}</td>
                <td className="py-1.5 text-fg-muted">{m.email}</td>
                <td className="py-1.5">{m.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------- owners

function OwnersSection() {
  const owners = useOwners();
  const household = useHousehold();
  const create = useCreateOwner();
  // Creating, renaming and deleting owners are all household-shape writes that
  // the API restricts to the owner role — a member may read the list (the owner
  // pickers need it) but none of the controls that change it.
  const canEdit = household.data?.role === "owner";
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [deleted, setDeleted] = useState<{ name: string; counts: OwnerReassignment } | null>(null);
  const nameId = useFieldId("owner-name");

  return (
    <div className="space-y-4">
      {canEdit && (
      <Card title="Add owner">
        <form
          className="flex items-end gap-2"
          data-testid="add-owner-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = requiredText(name);
            setErr(v);
            if (v) return;
            create.mutate({ name }, { onSuccess: () => setName("") });
          }}
        >
          <Field label="Name" htmlFor={nameId} required error={err} className="flex-1">
            <Input id={nameId} value={name} onChange={(e) => setName(e.target.value)} data-testid="owner-name" />
          </Field>
          <Button type="submit" disabled={create.isPending} data-testid="owner-save">Add owner</Button>
        </form>
        {create.isError && (
          <p className="text-sm text-negative" data-testid="owner-error">
            {(create.error as Error).message}
          </p>
        )}
      </Card>
      )}

      <Card title="Owners">
        <p className="text-xs text-fg-muted">
          Owners are what accounts, transactions and splits are assigned to. Shared is the default
          and the fallback when an owner is deleted.
        </p>
        <ul className="space-y-2" data-testid="owner-list">
          {owners.data?.map((o) => (
            <OwnerRow
              key={o.id}
              owner={o}
              owners={owners.data ?? []}
              canEdit={canEdit}
              onDeleted={(counts) => setDeleted({ name: o.name, counts })}
            />
          ))}
        </ul>
        {deleted && (
          <p className="text-xs text-fg-muted" data-testid="owner-delete-result">
            Deleted “{deleted.name}” and moved {deleted.counts.reassigned_accounts} accounts,{" "}
            {deleted.counts.reassigned_transactions} transactions and{" "}
            {deleted.counts.reassigned_splits} splits.
          </p>
        )}
      </Card>
    </div>
  );
}

function OwnerRow({
  owner,
  owners,
  canEdit,
  onDeleted,
}: {
  owner: Owner;
  owners: Owner[];
  canEdit: boolean;
  onDeleted: (counts: OwnerReassignment) => void;
}) {
  const update = useUpdateOwner();
  const del = useDeleteOwner();
  const [name, setName] = useState(owner.name);
  const [confirming, setConfirming] = useState(false);
  const [reassignTo, setReassignTo] = useState<string | null>(null);
  const reassignId = useFieldId("owner-reassign");

  const shared = owners.find((o) => o.kind === "shared");
  const isShared = owner.kind === "shared";
  const trimmed = name.trim();
  const dirty = trimmed !== owner.name && trimmed !== "";

  return (
    <li className="space-y-2 rounded-control bg-surface-inset/40 px-3 py-2" data-testid={`owner-row-${owner.id}`}>
      <div className="flex items-center gap-2">
        {canEdit ? (
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label={`Name of ${owner.name}`}
            className="max-w-xs"
            data-testid={`owner-rename-input-${owner.id}`}
          />
        ) : (
          // Read-only viewers get text, not a field that silently refuses to save.
          <span className="text-sm" data-testid={`owner-name-${owner.id}`}>{owner.name}</span>
        )}
        <span
          className={`rounded px-1.5 py-0.5 text-xs ${
            isShared ? "bg-surface-inset text-fg" : "bg-accent/15 text-accent"
          }`}
          data-testid={`owner-kind-${owner.id}`}
        >
          {owner.kind}
        </span>
        <div className="flex-1" />
        {canEdit && (
        <Button
          variant="secondary"
          className="px-2 py-1 text-xs"
          disabled={!dirty || update.isPending}
          onClick={() => update.mutate({ id: owner.id, body: { name: trimmed } })}
          data-testid={`owner-rename-${owner.id}`}
        >
          Rename
        </Button>
        )}
        {!isShared && canEdit && (
          <Button
            variant="ghost"
            className="px-2 py-1 text-xs"
            onClick={() => setConfirming(true)}
            data-testid={`owner-delete-${owner.id}`}
          >
            Delete
          </Button>
        )}
      </div>

      {isShared && (
        <p className="text-xs text-fg-muted">Shared is permanent — it cannot be deleted.</p>
      )}
      {update.isError && (
        <p className="text-xs text-negative" data-testid={`owner-rename-error-${owner.id}`}>
          {(update.error as Error).message}
        </p>
      )}

      {confirming && (
        <div
          className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm"
          data-testid={`owner-delete-confirm-${owner.id}`}
        >
          <p className="text-negative">
            Delete “{owner.name}”? Everything assigned to it moves to the owner below.
          </p>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <Field label="Reassign to" htmlFor={reassignId} className="w-48">
              <Select
                id={reassignId}
                value={reassignTo ?? shared?.id ?? ""}
                onChange={(e) => setReassignTo(e.target.value)}
                data-testid={`owner-reassign-${owner.id}`}
              >
                {owners
                  .filter((o) => o.id !== owner.id)
                  .map((o) => (
                    <option key={o.id} value={o.id}>{o.name}</option>
                  ))}
              </Select>
            </Field>
            <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              className="px-2 py-1 text-xs"
              disabled={del.isPending}
              onClick={() =>
                del.mutate(
                  { id: owner.id, reassignTo: reassignTo ?? shared?.id },
                  {
                    onSuccess: (counts) => {
                      setConfirming(false);
                      onDeleted(counts);
                    },
                  },
                )
              }
              data-testid={`owner-delete-confirm-btn-${owner.id}`}
            >
              Yes, delete
            </Button>
          </div>
          {del.isError && <p className="mt-2 text-sm text-negative">{(del.error as Error).message}</p>}
        </div>
      )}
    </li>
  );
}

// -------------------------------------------------------------------- rules

function RulesSection() {
  const rules = useRules();
  const update = useUpdateRule();
  const del = useDeleteRule();
  const apply = useApplyRules();
  const household = useHousehold();
  const categories = useCategories();
  const owners = useOwners();
  const tags = useTags();
  const accounts = useAccounts();
  // A rule rewrites ledger history, so every control that writes one is
  // owner-only — the same split the API enforces via require_owner. A member
  // still sees the list, which is a household-scoped read.
  const isOwner = household.data?.role === "owner";

  const [editing, setEditing] = useState<Rule | null>(null);
  const [creating, setCreating] = useState(false);
  const [applied, setApplied] = useState<RuleApplyResult | null>(null);

  const name = (list: { id: string; name: string }[] | undefined) => (id: string) =>
    list?.find((x) => x.id === id)?.name ?? "—";
  const names = {
    account: name(accounts.data),
    category: name(categories.data),
    owner: name(owners.data),
    tag: name(tags.data),
  };

  return (
    <div className="space-y-4">
      <Card title="Rules">
        <p className="text-xs text-fg-muted">
          Rules run over new transactions as they arrive and over the existing ledger when you ask
          them to. Lower priority runs first, and a rule never overwrites a field a person has set.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!isOwner}
            onClick={() => setCreating(true)}
            data-testid="rule-new"
          >
            New rule
          </Button>
          <Button
            variant="secondary"
            disabled={!isOwner || apply.isPending || !rules.data?.length}
            onClick={() => apply.mutate(undefined, { onSuccess: (r) => setApplied(r) })}
            data-testid="rule-apply"
          >
            {apply.isPending ? "Applying…" : "Apply to existing"}
          </Button>
          {!isOwner && (
            <span className="text-xs text-fg-muted">
              Only a household owner can create, edit or apply rules.
            </span>
          )}
        </div>

        {applied && (
          <p className="text-xs text-fg-muted" data-testid="rule-apply-result">
            {applied.updated === 0
              ? `Matched ${applied.matched} transactions — nothing left to change, the rules are already applied.`
              : `Matched ${applied.matched} transactions and updated ${applied.updated}.`}
          </p>
        )}
        {apply.isError && (
          <p className="text-sm text-negative" data-testid="rule-apply-error">
            {(apply.error as Error).message}
          </p>
        )}

        <ul className="space-y-2" data-testid="rule-list">
          {rules.data?.map((r) => (
            <li
              key={r.id}
              className="flex flex-wrap items-start gap-3 rounded-control bg-surface-inset/40 px-3 py-2"
              data-testid={`rule-row-${r.id}`}
            >
              <label className="flex items-center gap-2 pt-0.5 text-sm">
                <input
                  type="checkbox"
                  checked={r.enabled}
                  disabled={!isOwner || update.isPending}
                  onChange={(e) =>
                    update.mutate({ id: r.id, body: { enabled: e.target.checked } })
                  }
                  aria-label={`${r.name} enabled`}
                  data-testid={`rule-enabled-${r.id}`}
                />
              </label>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">
                  {r.name}{" "}
                  <span className="text-xs text-fg-muted">priority {r.priority}</span>
                  {!r.enabled && <span className="ml-2 text-xs text-warning/90">disabled</span>}
                </p>
                <p className="text-xs text-fg-muted" data-testid={`rule-when-${r.id}`}>
                  When {describeConditions(r.conditions, names)}
                </p>
                <p className="text-xs text-fg-muted" data-testid={`rule-then-${r.id}`}>
                  Then {describeActions(r.actions, names)}
                </p>
              </div>
              <div className="flex gap-1">
                <Button
                  variant="secondary"
                  className="px-2 py-1 text-xs"
                  disabled={!isOwner}
                  onClick={() => setEditing(r)}
                  data-testid={`rule-edit-${r.id}`}
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  className="px-2 py-1 text-xs"
                  disabled={!isOwner || del.isPending}
                  onClick={() => del.mutate(r.id)}
                  data-testid={`rule-delete-${r.id}`}
                >
                  Delete
                </Button>
              </div>
              {update.isError && update.variables?.id === r.id && (
                <p className="w-full text-xs text-negative">{(update.error as Error).message}</p>
              )}
            </li>
          ))}
          {rules.data?.length === 0 && (
            <li className="text-xs text-fg-muted" data-testid="rule-empty">
              No rules yet. A rule can categorise, tag, rename or hide transactions for you.
            </li>
          )}
        </ul>
      </Card>

      {/* Keyed on the id so switching rules remounts the draft rather than
          carrying the previous rule's values into this one. */}
      {(creating || editing) && (
        <RuleBuilder
          key={editing?.id ?? "new"}
          rule={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function describeConditions(c: RuleConditions, names: Names): string {
  const parts: string[] = [];
  if (c.merchant_contains) parts.push(`merchant contains “${c.merchant_contains}”`);
  if (c.description_regex) parts.push(`description matches /${c.description_regex}/`);
  // The bounds are on the signed amount, so they are shown that way rather than
  // as a currency — a rule has no currency, and "$-100" would read backwards.
  if (c.amount_min != null) parts.push(`amount is at least ${c.amount_min}`);
  if (c.amount_max != null) parts.push(`amount is at most ${c.amount_max}`);
  if (c.direction) parts.push(c.direction === "in" ? "money in" : "money out");
  if (c.account_ids?.length) parts.push(`in ${list(c.account_ids, names.account)}`);
  if (c.category_id) parts.push(`category is ${names.category(c.category_id)}`);
  if (c.is_pending != null) parts.push(c.is_pending ? "it is pending" : "it has posted");
  return parts.length ? parts.join(" and ") : "any transaction";
}

function describeActions(a: RuleActions, names: Names): string {
  const parts: string[] = [];
  if (a.set_category_id) parts.push(`set category to ${names.category(a.set_category_id)}`);
  if (a.add_tag_ids?.length) parts.push(`add ${list(a.add_tag_ids, names.tag)}`);
  if (a.set_owner_id) parts.push(`set owner to ${names.owner(a.set_owner_id)}`);
  if (a.rename_merchant) parts.push(`rename merchant to “${a.rename_merchant}”`);
  if (a.set_hidden != null) parts.push(a.set_hidden ? "hide it" : "unhide it");
  if (a.mark_reviewed != null) {
    parts.push(a.mark_reviewed ? "mark it reviewed" : "mark it needs review");
  }
  return parts.length ? parts.join(", ") : "do nothing";
}

interface Names {
  account: (id: string) => string;
  category: (id: string) => string;
  owner: (id: string) => string;
  tag: (id: string) => string;
}

/** A rule can name a category, owner or tag that has since been deleted — the
 * ids live in JSONB, so no foreign key stops it. The server skips such a rule
 * and keeps the rest running, and "—" here says the same thing. */
function list(ids: string[], name: (id: string) => string): string {
  return ids.map(name).join(", ");
}

// ----------------------------------------------------------------- profile

function ProfileSection() {
  const { me, logout } = useAuth();
  return (
    <Card title="Profile">
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2" data-testid="profile-info">
        <div><dt className="text-fg-muted">Display name</dt><dd>{me?.user.display_name}</dd></div>
        <div><dt className="text-fg-muted">Email</dt><dd>{me?.user.email}</dd></div>
      </dl>
      <Button variant="secondary" onClick={() => logout()} data-testid="profile-signout">Sign out</Button>
    </Card>
  );
}
