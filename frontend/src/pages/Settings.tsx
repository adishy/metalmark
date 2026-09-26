import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import InstitutionsSection from "@/pages/InstitutionsSection";
import { useAuth } from "@/auth/AuthContext";
import {
  downloadAccountCsv,
  downloadExport,
  useImportDocument,
  type ImportResult,
} from "@/api/portability";
import {
  useAccounts,
  useCategories,
  useCategoryGroups,
  useCreateCategory,
  useCreateCategoryGroup,
  useCreateOwner,
  useCreateTag,
  useDeleteCategory,
  useUpdateCategory,
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
  type RuleSplitLeg,
  type RuleApplyResult,
} from "@/api/rules";
import {
  useClaimConnection,
  useConnections,
  useDeleteConnection,
  useTriggerSync,
} from "@/api/sync";
import RuleBuilder from "@/components/RuleBuilder";
import ConnectionBadge from "@/components/ConnectionBadge";
import { CloseIcon } from "@/components/icons";
import { Day, Instant } from "@/components/datetime";
import ScrollTabs from "@/components/ScrollTabs";
import type { Owner, OwnerReassignment } from "@/api/types";
import { todayIso } from "@/lib/dates";
import {
  Button,
  Checkbox,
  Field,
  Input,
  Select,
  Spinner,
  requiredText,
  useFieldId,
  validCurrency,
  validRate,
} from "@/components/form";

const TABS = [
  // First, and admin-only. The position is deliberate: the sync control panel
  // shipped complete and could not be found, so the settings entry point is the
  // first thing an administrator sees here rather than the fifth of eight.
  { id: "admin", label: "Admin", adminOnly: true },
  { id: "categories", label: "Categories" },
  { id: "tags", label: "Tags" },
  { id: "institutions", label: "Institutions" },
  { id: "currencies", label: "Currencies" },
  { id: "household", label: "Household" },
  { id: "data", label: "Data" },
  { id: "connections", label: "Connections" },
  { id: "owners", label: "Owners" },
  { id: "rules", label: "Rules" },
  { id: "profile", label: "Profile" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [tab, setTab] = useState<TabId>("categories");
  const { me } = useAuth();
  const isAdmin = me?.user.is_admin === true;
  // `adminOnly` is declared on exactly one member of the union, so a bare
  // property read would not typecheck — the `in` check narrows to it, and its
  // only value there is `true`. A member's list is the eight that follow.
  const tabs = TABS.filter((t) => !("adminOnly" in t) || isAdmin);

  return (
    // §9.1 files Settings under form width — `max-w-2xl` (672 px), because a
    // control stretched to 1280 px is a control nobody can scan down. §9.3 also
    // gives it a rail, and the rail is *navigation*, not content, so the 672
    // describes the panel: 896 (`max-w-4xl`) − 224 (`lg:w-56` rail) − 24
    // (`gap-6`) = 648 px of panel, which is that number arrived at from the
    // other side. Below `lg:` there is no rail and the cap is simply 896, which
    // is where this page already was — the one page §9 did not need to widen.
    //
    // The rail column is `auto` rather than a literal `14rem` so the width has
    // one home (`lg:w-56` on the tablist, as §9.3 writes it) instead of two that
    // can disagree. The heading spans both columns because it labels the page,
    // not the panel.
    <div className="mx-auto max-w-4xl space-y-4 lg:grid lg:grid-cols-[auto_minmax(0,1fr)] lg:items-start lg:gap-6 lg:space-y-0">
      <h1 className="text-xl font-semibold lg:col-span-2">Settings</h1>
      {/* Below `lg:` this is the underline strip it has always been. At `lg:`
          it is a vertical rail (`rail`, §9.3) — the tab list is *navigation*
          beside its panel, so the underline becomes the AppShell nav's inset
          fill instead. Eleven sections are a lot to read from a picker, and
          tabs show where you are among them (DESIGN §5). */}
      <ScrollTabs
        tabs={tabs}
        selected={tab}
        onSelect={(id) => setTab(id as TabId)}
        ariaLabel="Settings sections"
        testidPrefix="settings-tab"
        rail
      />

      <div
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        data-testid={`settings-panel-${tab}`}
      >
        {tab === "admin" && <AdminSection onOpenConnections={() => setTab("connections")} />}
        {tab === "categories" && <CategoriesSection />}
        {tab === "tags" && <TagsSection />}
        {tab === "institutions" && <InstitutionsSection />}
        {tab === "currencies" && <CurrenciesSection />}
        {tab === "household" && <HouseholdSection />}
        {tab === "data" && <DataSection />}
        {tab === "connections" && <ConnectionsTab />}
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
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      {children}
    </section>
  );
}

// ------------------------------------------------------------------- admin

/**
 * The way in to the sync control panel.
 *
 * This tab exists because the panel did not need a feature, it needed a door.
 * It shipped complete — queue, runs, per-run logs, pause, cancel, retune, all
 * tested and green in CI — and the only link to it in the entire app was a
 * muted aside in the fifth of eight Settings tabs, with the word "Admin"
 * appearing nowhere in the interface. Anything that cannot be found is not
 * shipped, so there are now two doors and both of them say "Admin": this tab,
 * first in the list, and a nav item for administrators.
 *
 * The tab is *filtered* rather than shown-and-refused, matching the nav item and
 * the route. The server is the check that matters — every route the panel calls
 * enforces `require_owner` — so a member never sees a door that would only open
 * onto 403s.
 */
function AdminSection({ onOpenConnections }: { onOpenConnections: () => void }) {
  return (
    <div className="space-y-4">
      <Card title="MetalMark Money internals">
        <p className="text-sm text-fg-muted">
          <span className="font-semibold text-fg">Sync activity</span> is the control panel for
          everything MetalMark Money does on its own — what is queued, what is running, what ran, and the
          log of each run.
        </p>
        <ul className="ml-4 list-disc space-y-1 text-sm text-fg-muted">
          <li>Trigger a sync now, and watch the job it queues.</li>
          <li>Pause a connection, or change how often it runs.</li>
          <li>Inspect and cancel a job while it is running.</li>
          <li>Read a run's counts and its ordered event log.</li>
        </ul>
        <Link
          to="/admin"
          className="inline-flex min-h-11 items-center rounded-control bg-accent px-4 text-sm font-semibold text-accent-fg"
          data-testid="admin-open-panel"
        >
          Open the control panel
        </Link>
      </Card>

      <Card title="Bank connections">
        <p className="text-sm text-fg-muted">
          Adding, reconnecting or removing a bank's credentials is in the{" "}
          <button
            type="button"
            className="text-accent underline"
            onClick={onOpenConnections}
            data-testid="admin-goto-connections"
          >
            Connections
          </button>{" "}
          tab. Operating a connection once it exists — pausing it, retuning it, reading its runs —
          is the control panel.
        </p>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------- connections

/**
 * The credential lifecycle: connect, see whether it is working, disconnect.
 * Operating the sync — pausing, retuning, cancelling, reading the logs — is the
 * control panel at `/admin`, which this links to. That split is the Jellyfin
 * one: library configuration lives in settings, activity and logs live in the
 * dashboard.
 *
 * The owner check wraps the *section* rather than disabling controls inside it,
 * because the reads are owner-only too (`require_owner` on every connection
 * route, including the GETs). A member rendering this section would fire three
 * requests that all 403 and show a screen of errors; not mounting it is the
 * honest version of "this is not yours to see".
 */
function ConnectionsTab() {
  const household = useHousehold();
  if (household.data?.role !== "owner") {
    return (
      <Card title="Bank connections">
        <p className="text-sm text-fg-muted" data-testid="connections-owner-only">
          Only a household owner can connect a bank. A connection names the household's banks, so
          the list is owner-only too.
        </p>
      </Card>
    );
  }
  return <ConnectionsSection />;
}

function ConnectionsSection() {
  const connections = useConnections();
  const claim = useClaimConnection();
  const del = useDeleteConnection();
  const trigger = useTriggerSync();

  const tokenId = useFieldId("setup-token");
  const [token, setToken] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const missing = requiredText(token);
    setTokenError(missing);
    if (missing) return;
    claim.mutate(
      { setup_token: token.trim() },
      {
        // The token is single-use, so it is cleared on success and *kept* on
        // failure: a typo should be fixable without going back to the bridge,
        // and a failed token is spent anyway.
        onSuccess: () => setToken(""),
      },
    );
  }

  return (
    <div className="space-y-4">
      <Card title="Bank connections">
        <p className="text-sm text-fg-muted">
          Connect a bank through SimpleFIN. Once connected, accounts and transactions arrive on
          their own and re-syncing is safe to run as often as you like — it never duplicates or
          overwrites anything you have edited.
        </p>
        <p className="text-xs text-fg-muted">
          Watching a sync run, pausing one, or changing how often it runs lives in the{" "}
          <Link className="text-accent underline" to="/admin" data-testid="open-admin">
            sync activity panel
          </Link>
          .
        </p>

        <form className="space-y-3" onSubmit={submit}>
          <Field
            label="Setup token"
            htmlFor={tokenId}
            required
            error={tokenError}
            hint="From your bridge's “create a connection” page. Used once and never stored — only the access URL it returns is kept, encrypted."
          >
            <Input
              id={tokenId}
              type="password"
              value={token}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setToken(e.target.value)}
              aria-invalid={tokenError ? true : undefined}
              data-testid="setup-token"
            />
          </Field>
          <Button
            type="submit"
            disabled={claim.isPending}
            aria-busy={claim.isPending}
            data-testid="connect-submit"
          >
            {claim.isPending && <Spinner />}
            Connect
          </Button>
          {claim.isError && (
            <p className="text-sm text-negative" role="alert" data-testid="connect-error">
              {(claim.error as Error).message}
            </p>
          )}
        </form>
      </Card>

      <Card title="Connected banks">
        {connections.isPending && <Spinner />}
        {connections.isError && (
          <p className="text-sm text-negative" role="alert">
            {(connections.error as Error).message}
          </p>
        )}
        {connections.isSuccess && connections.data.length === 0 && (
          <p className="text-sm text-fg-muted" data-testid="no-connections">
            No banks connected yet.
          </p>
        )}

        {connections.data && connections.data.length > 0 && (
          <ul className="divide-y divide-border rounded-control bg-surface-inset/40" data-testid="connection-list">
            {connections.data.map((c) => (
              <li key={c.id} className="space-y-2 p-3" data-testid={`conn-row-${c.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-fg">
                    {c.org_name ?? "Unnamed connection"}
                  </span>
                  <ConnectionBadge connection={c} />
                </div>
                <p className="text-xs text-fg-muted">
                  {c.last_synced_at ? (
                    <>
                      Last synced <Instant value={c.last_synced_at} style="relative" />
                    </>
                  ) : (
                    "Never synced"
                  )}
                </p>
                {c.last_error && (
                  <p className="text-xs text-negative" data-testid={`conn-last-error-${c.id}`}>
                    {c.last_error}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="secondary"
                    disabled={trigger.isPending || !c.is_enabled}
                    onClick={() => trigger.mutate(c.id)}
                    data-testid={`sync-now-${c.id}`}
                  >
                    Sync now
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => setDisconnecting(disconnecting === c.id ? null : c.id)}
                    data-testid={`disconnect-${c.id}`}
                  >
                    Disconnect
                  </Button>
                </div>

                {disconnecting === c.id && (
                  <div
                    className="rounded-control border border-negative/40 bg-negative/10 p-3 text-sm"
                    data-testid={`disconnect-confirm-${c.id}`}
                  >
                    {/* The reassuring half is the part people do not believe, so
                        it is stated with the same weight as the warning. */}
                    <p className="text-negative">
                      Disconnect “{c.org_name ?? "this connection"}”? The accounts and every
                      transaction stay, and become hand-entered ones. Nothing is deleted, and
                      reconnecting later picks the same accounts back up rather than importing
                      them twice.
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button variant="secondary" onClick={() => setDisconnecting(null)}>
                        Keep it
                      </Button>
                      <Button
                        variant="danger"
                        disabled={del.isPending}
                        onClick={() =>
                          del.mutate(c.id, {
                            onSuccess: () => setDisconnecting(null),
                          })
                        }
                        data-testid={`disconnect-confirm-btn-${c.id}`}
                      >
                        Yes, disconnect
                      </Button>
                    </div>
                    {del.isError && (
                      <p className="mt-2 text-sm text-negative" role="alert">
                        {(del.error as Error).message}
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
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
  const updateCat = useUpdateCategory();

  const [gName, setGName] = useState("");
  const [gType, setGType] = useState("expense");
  const [cName, setCName] = useState("");
  const [cGroup, setCGroup] = useState("");
  const [cColor, setCColor] = useState("#14b8a6");
  const [cIcon, setCIcon] = useState("");
  const [gErr, setGErr] = useState<string | null>(null);
  const [cErr, setCErr] = useState<string | null>(null);

  const ids = {
    gName: useFieldId("group-name"),
    gType: useFieldId("group-type"),
    cName: useFieldId("cat-name"),
    cGroup: useFieldId("cat-group"),
    cColor: useFieldId("cat-color"),
    cIcon: useFieldId("cat-icon"),
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
          className="grid grid-cols-1 gap-3 sm:grid-cols-5"
          data-testid="add-category-form"
          onSubmit={(e) => {
            e.preventDefault();
            const err = requiredText(cName) || requiredText(cGroup || defaultGroup);
            setCErr(err);
            if (err) return;
            createCat.mutate(
              {
                name: cName,
                group_id: cGroup || defaultGroup,
                color: cColor,
                icon: cIcon.trim() || null,
              },
              {
                onSuccess: () => {
                  setCName("");
                  setCIcon("");
                },
              },
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
          <Field label="Emoji" htmlFor={ids.cIcon} hint="Optional, e.g. 🛒">
            <Input id={ids.cIcon} value={cIcon} maxLength={16} onChange={(e) => setCIcon(e.target.value)} data-testid="category-icon" />
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
                {/* A raw <button> here computed to 67x16 — the `text-xs` line
                    box with no padding, well under the 44 px floor (§5). The
                    primitives carry `min-h-11`; use them. */}
                <Button
                  variant="ghost"
                  className="px-2 py-1 text-xs"
                  onClick={() => delGroup.mutate(g.id)}
                  data-testid={`group-delete-${g.id}`}
                >
                  Delete group
                </Button>
              </div>
              <ul className="mt-1 divide-y divide-border rounded-control bg-surface-inset/40">
                {(byGroup.get(g.id) ?? []).map((c) => (
                  <li key={c.id} className="flex items-center justify-between px-3 py-2 text-sm">
                    <span className="flex min-w-0 flex-1 items-center gap-3">
                      {/* The emoji is edited in place: it is the one field
                          people change for fun, and a dialog for one
                          character is a long way round. Saved on blur. The
                          wrapper sets the width — the control is w-full. */}
                      <span className="w-14 shrink-0">
                      <Input
                        aria-label={`Emoji for ${c.name}`}
                        defaultValue={c.icon ?? ""}
                        maxLength={16}
                        className="px-1 text-center"
                        onBlur={(e) => {
                          const next = e.target.value.trim() || null;
                          if (next !== (c.icon ?? null)) {
                            updateCat.mutate({ id: c.id, body: { icon: next } });
                          }
                        }}
                        data-testid={`category-icon-${c.id}`}
                      />
                      </span>
                      {!c.icon && (
                        <span className="inline-block h-3 w-3 rounded-full" style={{ background: c.color ?? "#64748b" }} />
                      )}
                      <span className="truncate">{c.name}</span>
                    </span>
                    <Button
                      variant="ghost"
                      className="px-2 py-1 text-xs"
                      onClick={() => delCat.mutate(c.id)}
                      data-testid={`category-delete-${c.id}`}
                    >
                      Delete
                    </Button>
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
        {/* `pr-1` and no `py-1`: the icon button is 44 px and sets the pill's
            height itself, so extra padding would only make the chip taller than
            its target. Note the tag list is not seeded, so the target-size
            sweep cannot see this control — it was fixed by reading §4.2, not by
            watching it go red. */}
        <ul className="flex flex-wrap gap-2" data-testid="tag-list">
          {tags.data?.map((t) => (
            <li key={t.id} className="flex items-center gap-2 rounded-full bg-surface-inset py-0 pl-3 pr-1 text-sm">
              <span className="inline-block h-3 w-3 rounded-full" style={{ background: t.color ?? "#64748b" }} />
              {t.name}
              <button
                className="inline-flex size-11 items-center justify-center rounded-full text-fg-muted hover:text-negative"
                onClick={() => delTag.mutate(t.id)}
                aria-label={`Delete ${t.name}`}
                data-testid={`tag-delete-${t.id}`}
              >
                <CloseIcon />
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
        {upsert.isError && (
          <p className="text-sm text-negative" role="alert">
            {(upsert.error as Error).message}
          </p>
        )}
      </Card>

      <Card title="FX rates">
        <ul className="divide-y divide-border" data-testid="fx-list">
          {rates.data?.map((r) => (
            <li key={r.id} className="flex justify-between px-1 py-2 text-sm">
              <span>1 {r.base_currency} = {r.rate} {r.quote_currency}</span>
              {/* A date-only value, so it is a calendar day and not an instant:
                  a rate dated the 1st is the 1st wherever it is read. */}
              <span className="text-fg-muted">
                <Day value={r.rate_date} style="long" />
              </span>
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
        {update.isError && (
          <p className="text-sm text-negative" role="alert">
            {(update.error as Error).message}
          </p>
        )}
      </Card>

      <Card title="Members">
        {/* Signup is open, so there is nothing to invite: a new person creates
            their own account and lands in this household. */}
        <p className="text-xs text-fg-muted">
          Anyone can create an account from the sign-up page and will join this household as a
          member. Members are managed here, not on the ledger: what owns money are the owners below.
        </p>
        {/* A real table, kept at phone width because the columns are the point
            — so §4.7 applies: a scroll container that the keyboard can reach
            (2.1.1), a caption, and scoped headers. Without `scope`, a screen
            reader reads three cells and no idea which column they belong to. */}
        <div
          className="overflow-x-auto"
          role="region"
          aria-label="Household members"
          tabIndex={0}
        >
          <table className="w-full text-sm" data-testid="members-table">
            <caption className="sr-only">
              Household members, with the email address and role of each
            </caption>
            <thead>
              <tr className="text-left text-xs text-fg-muted">
                <th scope="col" className="py-1">Name</th>
                <th scope="col" className="py-1">Email</th>
                <th scope="col" className="py-1">Role</th>
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
        </div>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------- data

/**
 * Getting the ledger out, and getting one back in (ADR-0036).
 *
 * Three cards, in the order a person needs them: everything out, one account
 * out, a document back in. Export is for any member — reading the household's
 * ledger is what being a member already is — and import is owner-only, because
 * it can merge a second household's history into this one and change its base
 * currency. That is the same split the API enforces; the section states it
 * rather than rendering controls that would 403.
 *
 * The card that will be misread is the first one, so it says the quiet part out
 * loud: this is a *portable* document, not a backup. It has no credentials, no
 * user accounts and no sessions, and restoring an instance from one is not a
 * thing that works. The backup is `scripts/backup.sh`, and the drill that proves
 * it is a CI gate.
 */
function DataSection() {
  const household = useHousehold();
  const accounts = useAccounts();
  const isOwner = household.data?.role === "owner";

  // Downloads as mutations, so the button can say it is working and a failure
  // can land somewhere visible. They are reads on the wire; what they are here
  // is one interaction with a beginning, an end and an error.
  const exportAll = useMutation({ mutationFn: downloadExport });
  const exportCsv = useMutation({ mutationFn: downloadAccountCsv });
  const importDoc = useImportDocument();

  const [accountId, setAccountId] = useState("");
  const [picked, setPicked] = useState<File | null>(null);
  const accountField = useFieldId("data-account");
  const fileField = useFieldId("data-file");

  const chosen = accountId || accounts.data?.[0]?.id || "";

  return (
    <div className="space-y-4">
      <Card title="Export everything">
        <p className="text-sm text-fg-muted">
          Your whole ledger in one JSON document: accounts, transactions and their splits,
          categories, rules, tags, owners, securities, holdings and the exchange rates behind the
          conversions.
        </p>
        <p className="text-sm text-fg-muted">
          It is a <strong className="text-fg">portable copy, not a backup</strong>. It holds what
          you entered and nothing about the instance — no user accounts, no sessions, and
          deliberately no bank credentials, so an imported connection comes back needing to be
          reconnected. Backing up an instance is <code>scripts/backup.sh</code>.
        </p>
        <Button
          onClick={() => exportAll.mutate()}
          disabled={exportAll.isPending}
          aria-busy={exportAll.isPending}
          data-testid="export-download"
        >
          {exportAll.isPending && <Spinner />}
          Download export
        </Button>
        {exportAll.isError && (
          <p className="text-sm text-negative" role="alert" data-testid="export-error">
            {(exportAll.error as Error).message}
          </p>
        )}
      </Card>

      <Card title="Export one account as CSV">
        <p className="text-sm text-fg-muted">
          A spreadsheet of one account's transactions, with the columns the CSV importer already
          knows — so it can be read straight back in, here or anywhere else.
        </p>
        <p className="text-xs text-fg-muted">
          One account per file on purpose: an import lands every row of a file in the account you
          pick at the other end, so a file holding several accounts would put them all in one.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Account" htmlFor={accountField} className="w-56">
            <Select
              id={accountField}
              value={chosen}
              onChange={(e) => setAccountId(e.target.value)}
              data-testid="export-csv-account"
            >
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            variant="secondary"
            disabled={!chosen || exportCsv.isPending}
            aria-busy={exportCsv.isPending}
            onClick={() => exportCsv.mutate(chosen)}
            data-testid="export-csv-download"
          >
            {exportCsv.isPending && <Spinner />}
            Download CSV
          </Button>
        </div>
        {exportCsv.isError && (
          <p className="text-sm text-negative" role="alert" data-testid="export-csv-error">
            {(exportCsv.error as Error).message}
          </p>
        )}
      </Card>

      <Card title="Import a document">
        {!isOwner ? (
          <p className="text-sm text-fg-muted" data-testid="import-owner-only">
            Only a household owner can import. An import merges a second history into this
            household's own, so it is not a member's call to make.
          </p>
        ) : (
          <form
            className="space-y-3"
            data-testid="import-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (picked) importDoc.mutate(picked);
            }}
          >
            <p className="text-sm text-fg-muted">
              A document exported from MetalMark Money — this household or another one. It merges:
              nothing here is deleted, and anything the document and this household already agree
              on is left alone rather than duplicated.
            </p>
            <Field
              label="Export file"
              htmlFor={fileField}
              hint="The .json file a “Download export” saved."
            >
              <Input
                id={fileField}
                type="file"
                accept=".json,application/json"
                onChange={(e) => {
                  setPicked(e.target.files?.[0] ?? null);
                  // The previous result is about the previous file, and leaving
                  // it on screen beside a newly picked one is a sentence about
                  // the wrong document.
                  importDoc.reset();
                }}
                data-testid="import-document-file"
              />
            </Field>
            <Button
              type="submit"
              disabled={!picked || importDoc.isPending}
              aria-busy={importDoc.isPending}
              data-testid="import-document-submit"
            >
              {importDoc.isPending && <Spinner />}
              Import
            </Button>
            {importDoc.isError && (
              <p className="text-sm text-negative" role="alert" data-testid="import-document-error">
                {(importDoc.error as Error).message}
              </p>
            )}
            {importDoc.data && <ImportSummary result={importDoc.data} />}
          </form>
        )}
      </Card>
    </div>
  );
}

/**
 * What an import actually did.
 *
 * Two lists rather than one, because "created" and "already here" are the two
 * answers a person wants and they are never both interesting: a first import of
 * someone else's document is all created, and importing your own export back is
 * all matched — which is the reassuring result, and reads as one.
 */
function ImportSummary({ result }: { result: ImportResult }) {
  const created = Object.entries(result.created).filter(([, n]) => n > 0);
  const matched = Object.entries(result.matched).filter(([, n]) => n > 0);

  return (
    <div className="space-y-2 rounded-control bg-surface-inset/40 p-3" role="status" data-testid="import-result">
      {created.length === 0 ? (
        <p className="text-sm text-fg" data-testid="import-nothing-new">
          Nothing new — this household already had everything in that document.
        </p>
      ) : (
        <div>
          <p className="text-sm font-medium text-fg">Imported</p>
          <Counts counts={created} testid="import-created" />
        </div>
      )}
      {matched.length > 0 && (
        <div>
          <p className="text-sm font-medium text-fg">Already here, left alone</p>
          <Counts counts={matched} testid="import-matched" />
        </div>
      )}
      {result.warnings.length > 0 && (
        <div data-testid="import-warnings">
          <p className="text-sm font-medium text-warning">Imported with warnings</p>
          <ul className="ml-4 list-disc text-sm text-fg-muted">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** One line per entity, `label: count`.
 *
 * A count and a label rather than a sentence, because the plural is the
 * server's: "1 security" and "2 securities" from one template is a grammar
 * table, and the reader is checking the numbers anyway.
 */
function Counts({ counts, testid }: { counts: [string, number][]; testid: string }) {
  return (
    <ul className="ml-4 list-disc text-sm text-fg-muted" data-testid={testid}>
      {counts.map(([entity, n]) => (
        <li key={entity}>
          {ENTITY_LABELS[entity] ?? entity.replace(/_/g, " ")}: {n}
        </li>
      ))}
    </ul>
  );
}

/** The server's entity keys, as words. A key that is missing here is not
 * dropped — it is printed with its underscores opened out, so a new entity is
 * legible before anyone remembers to add it. */
const ENTITY_LABELS: Record<string, string> = {
  accounts: "accounts",
  balance_snapshots: "balance snapshots",
  categories: "categories",
  category_groups: "category groups",
  connections: "bank connections",
  fx_rates: "exchange rates",
  holdings: "holdings",
  investment_transactions: "investment transactions",
  owners: "owners",
  rules: "rules",
  securities: "securities",
  security_prices: "security prices",
  tags: "tags",
  transaction_splits: "splits",
  transaction_tags: "tag links",
  transactions: "transactions",
  transfer_groups: "transfer links",
};

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
          <p className="text-sm text-negative" role="alert" data-testid="owner-error">
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
          <p
            className="text-xs text-fg-muted"
            role="status"
            data-testid="owner-delete-result"
          >
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
            isShared ? "bg-surface-inset text-fg" : "bg-accent/15 text-accent-ink"
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
        <p
          className="text-xs text-negative"
          role="alert"
          data-testid={`owner-rename-error-${owner.id}`}
        >
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
          {del.isError && (
            <p className="mt-2 text-sm text-negative" role="alert">
              {(del.error as Error).message}
            </p>
          )}
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
            aria-busy={apply.isPending}
            onClick={() => apply.mutate(undefined, { onSuccess: (r) => setApplied(r) })}
            data-testid="rule-apply"
          >
            {apply.isPending && <Spinner />}
            Apply to existing
          </Button>
          {!isOwner && (
            <span className="text-xs text-fg-muted">
              Only a household owner can create, edit or apply rules.
            </span>
          )}
        </div>

        {applied && (
          <p className="text-xs text-fg-muted" role="status" data-testid="rule-apply-result">
            {applied.updated === 0
              ? `Matched ${applied.matched} transactions — nothing left to change, the rules are already applied.`
              : `Matched ${applied.matched} transactions and updated ${applied.updated}.`}
          </p>
        )}
        {apply.isError && (
          <p className="text-sm text-negative" role="alert" data-testid="rule-apply-error">
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
              {/* The row already reads the rule's name, so the box's own label
                  stays visually hidden — a second copy of the name beside the
                  box would be read twice. It still comes from the label, not an
                  aria-label, so the accessible name and the 44px hit row are the
                  same element. `min-w-11` is there because a hidden label leaves
                  the row 32px wide; the target still has to be 44px both ways. */}
              <Checkbox
                label={<span className="sr-only">{`${r.name} enabled`}</span>}
                className="min-w-11"
                checked={r.enabled}
                disabled={!isOwner || update.isPending}
                onChange={(e) =>
                  update.mutate({ id: r.id, body: { enabled: e.target.checked } })
                }
                data-testid={`rule-enabled-${r.id}`}
              />
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
                <p className="w-full text-xs text-negative" role="alert">
                  {(update.error as Error).message}
                </p>
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
  // Without this a rule whose only action is a split (ADR-0031) would read
  // "do nothing", which is the opposite of what it does.
  if (a.split?.length) parts.push(`split ${describeSplit(a.split, names)}`);
  return parts.length ? parts.join(", ") : "do nothing";
}

/** A rule-made split, as the rule list reads it: what each leg takes, and where
 * it goes. Never a total — the rule was written before it met the transactions
 * it will split, and the leg that takes the rest is exactly what says so. */
function describeSplit(legs: RuleSplitLeg[], names: Names): string {
  return legs
    .map((leg) => {
      const to = leg.category_id ? ` to ${names.category(leg.category_id)}` : "";
      if (leg.remainder) return `the rest${to}`;
      if (leg.percent != null) return `${leg.percent}%${to}`;
      if (leg.amount != null) return `${leg.amount}${to}`;
      return `a leg${to}`;
    })
    .join(", ");
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
