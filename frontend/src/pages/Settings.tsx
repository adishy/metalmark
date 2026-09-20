import { useMemo, useState } from "react";
import { useAuth } from "@/auth/AuthContext";
import {
  useCategories,
  useCategoryGroups,
  useCreateCategory,
  useCreateCategoryGroup,
  useCreateInvite,
  useCreateTag,
  useDeleteCategory,
  useDeleteCategoryGroup,
  useDeleteTag,
  useFxRates,
  useHousehold,
  useMembers,
  useTags,
  useUpsertFxRate,
} from "@/api/hooks";
import type { Invite } from "@/api/types";
import { formatDate } from "@/lib/format";
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
  { id: "profile", label: "Profile" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [tab, setTab] = useState<TabId>("categories");
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Settings</h2>
      <div className="flex flex-wrap gap-1 border-b border-slate-800" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-t-lg px-3 py-2 text-sm ${
              tab === t.id ? "border-b-2 border-brand text-white" : "text-slate-400 hover:text-white"
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
        {tab === "profile" && <ProfileSection />}
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-2xl bg-slate-900 p-4">
      <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
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
                <p className="text-sm font-medium text-slate-300">
                  {g.name} <span className="text-xs text-slate-500">({g.type})</span>
                </p>
                <button
                  className="text-xs text-slate-500 hover:text-red-300"
                  onClick={() => delGroup.mutate(g.id)}
                  data-testid={`group-delete-${g.id}`}
                >
                  Delete group
                </button>
              </div>
              <ul className="mt-1 divide-y divide-slate-800 rounded-lg bg-slate-800/40">
                {(byGroup.get(g.id) ?? []).map((c) => (
                  <li key={c.id} className="flex items-center justify-between px-3 py-2 text-sm">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-3 w-3 rounded-full" style={{ background: c.color ?? "#64748b" }} />
                      {c.name}
                    </span>
                    <button
                      className="text-xs text-slate-500 hover:text-red-300"
                      onClick={() => delCat.mutate(c.id)}
                      data-testid={`category-delete-${c.id}`}
                    >
                      Delete
                    </button>
                  </li>
                ))}
                {(byGroup.get(g.id) ?? []).length === 0 && (
                  <li className="px-3 py-2 text-xs text-slate-500">No categories.</li>
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
            <li key={t.id} className="flex items-center gap-2 rounded-full bg-slate-800 px-3 py-1 text-sm">
              <span className="inline-block h-3 w-3 rounded-full" style={{ background: t.color ?? "#64748b" }} />
              {t.name}
              <button
                className="text-slate-500 hover:text-red-300"
                onClick={() => delTag.mutate(t.id)}
                aria-label={`Delete ${t.name}`}
                data-testid={`tag-delete-${t.id}`}
              >
                ✕
              </button>
            </li>
          ))}
          {tags.data?.length === 0 && <li className="text-xs text-slate-500">No tags yet.</li>}
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
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
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
        <p className="text-xs text-slate-500">
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
        {upsert.isError && <p className="text-sm text-red-400">{(upsert.error as Error).message}</p>}
      </Card>

      <Card title="FX rates">
        <ul className="divide-y divide-slate-800" data-testid="fx-list">
          {rates.data?.map((r) => (
            <li key={r.id} className="flex justify-between px-1 py-2 text-sm">
              <span>1 {r.base_currency} = {r.rate} {r.quote_currency}</span>
              <span className="text-slate-500">{formatDate(r.rate_date)}</span>
            </li>
          ))}
          {rates.data?.length === 0 && <li className="py-2 text-xs text-slate-500">No rates yet.</li>}
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
  const invite = useCreateInvite();
  const isOwner = (household.data?.role ?? me?.role) === "owner";

  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [result, setResult] = useState<Invite | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const emailId = useFieldId("invite-email");
  const roleId = useFieldId("invite-role");

  return (
    <div className="space-y-4">
      <Card title="Household">
        <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2" data-testid="household-info">
          <div><dt className="text-slate-500">Name</dt><dd>{household.data?.name ?? "—"}</dd></div>
          <div>
            <dt className="text-slate-500">Base currency</dt>
            <dd>{household.data?.base_currency ?? "—"} <span className="text-xs text-slate-500">(immutable)</span></dd>
          </div>
          <div><dt className="text-slate-500">Timezone</dt><dd>{household.data?.timezone ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Your role</dt><dd>{household.data?.role ?? me?.role}</dd></div>
        </dl>
      </Card>

      <Card title="Members">
        <table className="w-full text-sm" data-testid="members-table">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="py-1">Name</th><th className="py-1">Email</th><th className="py-1">Role</th>
            </tr>
          </thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.user_id} className="border-t border-slate-800">
                <td className="py-1.5">{m.display_name}</td>
                <td className="py-1.5 text-slate-400">{m.email}</td>
                <td className="py-1.5">{m.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {isOwner && (
        <Card title="Invite a member">
          <form
            className="grid grid-cols-1 gap-3 sm:grid-cols-3"
            data-testid="invite-form"
            onSubmit={(e) => {
              e.preventDefault();
              const v = requiredText(email);
              setErr(v);
              if (v) return;
              invite.mutate(
                { email, role },
                {
                  onSuccess: (inv) => {
                    setResult(inv);
                    setEmail("");
                  },
                },
              );
            }}
          >
            <Field label="Email" htmlFor={emailId} required error={err}>
              <Input id={emailId} type="email" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="invite-email" />
            </Field>
            <Field label="Role" htmlFor={roleId}>
              <Select id={roleId} value={role} onChange={(e) => setRole(e.target.value)} data-testid="invite-role">
                <option value="member">member</option>
                <option value="owner">owner</option>
              </Select>
            </Field>
            <div className="flex items-end">
              <Button type="submit" disabled={invite.isPending} data-testid="invite-save">Create invite</Button>
            </div>
          </form>
          {invite.isError && <p className="mt-2 text-sm text-red-400">{(invite.error as Error).message}</p>}
          {result && (
            <div className="mt-3 rounded-lg border border-brand/40 bg-brand/10 p-3 text-sm" data-testid="invite-result">
              <p className="text-slate-300">Invite created for {result.email}. Share this token once:</p>
              <code className="mt-1 block break-all rounded bg-slate-950 px-2 py-1 text-brand" data-testid="invite-token">
                {result.token ?? "(token hidden)"}
              </code>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- profile

function ProfileSection() {
  const { me, logout } = useAuth();
  return (
    <Card title="Profile">
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2" data-testid="profile-info">
        <div><dt className="text-slate-500">Display name</dt><dd>{me?.user.display_name}</dd></div>
        <div><dt className="text-slate-500">Email</dt><dd>{me?.user.email}</dd></div>
      </dl>
      <Button variant="secondary" onClick={() => logout()} data-testid="profile-signout">Sign out</Button>
    </Card>
  );
}
