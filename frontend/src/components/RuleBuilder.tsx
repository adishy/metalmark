// The rule editor: conditions on the left half of the form, actions on the
// right, and a name/priority header. One dialog for both create and edit — the
// only difference is whether the rule passed in is null and which mutation the
// Save button calls.
//
// The form is deliberately a flat draft (strings for every control, including
// the trinary selects) rather than living in the API's shape: a select has an
// "unset" state that no `boolean | null` distinguishes from `false`, and the
// amount fields are text until the user is done typing. `conditionsFrom` /
// `actionsFrom` at the bottom convert once, on save.
//
// Amounts stay strings end to end (ADR-0005). Nothing here does money
// arithmetic — the one numeric comparison is the bounds check, which the server
// re-does in Decimal.

import { useState, type ReactNode } from "react";
import { useAccounts, useCategories, useOwners, useTags } from "@/api/hooks";
import { useCreateRule, useUpdateRule, type Rule, type RuleActions, type RuleConditions } from "@/api/rules";
import Dialog from "@/components/Dialog";
import { Button, Checkbox, Field, Input, Select, requiredText, useFieldId, validAmount } from "@/components/form";

type Tri = "" | "true" | "false";

interface Draft {
  name: string;
  priority: string;
  enabled: boolean;
  merchant: string;
  regex: string;
  amountMin: string;
  amountMax: string;
  direction: "" | "in" | "out";
  accountIds: string[];
  categoryId: string;
  isPending: Tri;
  setCategoryId: string;
  tagIds: string[];
  ownerId: string;
  renameMerchant: string;
  setHidden: Tri;
  markReviewed: Tri;
}

function initial(rule: Rule | null | undefined): Draft {
  const c: RuleConditions = rule?.conditions ?? {};
  const a: RuleActions = rule?.actions ?? {};
  return {
    name: rule?.name ?? "",
    priority: String(rule?.priority ?? 100),
    enabled: rule?.enabled ?? true,
    merchant: c.merchant_contains ?? "",
    regex: c.description_regex ?? "",
    amountMin: c.amount_min ?? "",
    amountMax: c.amount_max ?? "",
    direction: c.direction ?? "",
    accountIds: c.account_ids ?? [],
    categoryId: c.category_id ?? "",
    isPending: c.is_pending == null ? "" : String(c.is_pending) as Tri,
    setCategoryId: a.set_category_id ?? "",
    tagIds: a.add_tag_ids ?? [],
    ownerId: a.set_owner_id ?? "",
    renameMerchant: a.rename_merchant ?? "",
    setHidden: a.set_hidden == null ? "" : String(a.set_hidden) as Tri,
    markReviewed: a.mark_reviewed == null ? "" : String(a.mark_reviewed) as Tri,
  };
}

export default function RuleBuilder({
  rule = null,
  onClose,
}: {
  /** null = a new rule. A rule = edit it. The parent keys this component on the
   * id so switching rules remounts rather than reusing the previous draft. */
  rule?: Rule | null;
  onClose: () => void;
}) {
  const categories = useCategories();
  const accounts = useAccounts();
  const owners = useOwners();
  const tags = useTags();
  const create = useCreateRule();
  const update = useUpdateRule();
  const saving = create.isPending || update.isPending;

  const [d, setD] = useState<Draft>(() => initial(rule));
  const [errs, setErrs] = useState<Record<string, string | null>>({});
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const toggle = (key: "accountIds" | "tagIds", id: string) =>
    set({
      [key]: d[key].includes(id) ? d[key].filter((x) => x !== id) : [...d[key], id],
    } as Partial<Draft>);

  const ids = {
    name: useFieldId("rule-name"),
    priority: useFieldId("rule-priority"),
    merchant: useFieldId("rule-merchant"),
    regex: useFieldId("rule-regex"),
    min: useFieldId("rule-min"),
    max: useFieldId("rule-max"),
    direction: useFieldId("rule-direction"),
    category: useFieldId("rule-category"),
    pending: useFieldId("rule-pending"),
    setCategory: useFieldId("rule-set-category"),
    owner: useFieldId("rule-owner"),
    rename: useFieldId("rule-rename"),
    hidden: useFieldId("rule-hidden"),
    reviewed: useFieldId("rule-reviewed"),
  };

  const save = () => {
    const next: Record<string, string | null> = { name: requiredText(d.name) };
    // An empty amount field means "no bound", so only a non-empty one is checked.
    if (d.amountMin.trim()) next.amountMin = validAmount(d.amountMin);
    if (d.amountMax.trim()) next.amountMax = validAmount(d.amountMax);
    if (d.amountMin.trim() && d.amountMax.trim() && !next.amountMin && !next.amountMax) {
      // A hint, not the authority: the server compares the two as Decimals and
      // rejects an inverted pair itself. Number() is exact enough to save the
      // round trip for the cases a human actually types.
      next.amountMax =
        Number(d.amountMin) > Number(d.amountMax) ? "Lower bound is above the upper bound" : null;
    }
    if (d.regex.trim() && !compiles(d.regex)) {
      next.regex = "Invalid regular expression";
    }
    if (!/^\d+$/.test(d.priority.trim())) next.priority = "Enter a whole number";
    setErrs(next);
    if (Object.values(next).some(Boolean)) return;

    const body = {
      name: d.name.trim(),
      priority: Number(d.priority),
      enabled: d.enabled,
      conditions: conditionsFrom(d),
      actions: actionsFrom(d),
    };
    const opts = { onSuccess: onClose };
    if (rule) update.mutate({ id: rule.id, body }, opts);
    else create.mutate(body, opts);
  };

  const failure = create.error ?? update.error;

  return (
    <Dialog
      open
      onClose={onClose}
      title={rule ? "Edit rule" : "New rule"}
      testid="rule-builder"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} data-testid="rule-cancel">
            Cancel
          </Button>
          <Button onClick={save} disabled={saving} data-testid="rule-save">
            {saving ? "Saving…" : "Save rule"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_auto]">
          <Field label="Name" htmlFor={ids.name} required error={errs.name}>
            <Input
              id={ids.name}
              value={d.name}
              onChange={(e) => set({ name: e.target.value })}
              data-testid="rule-name"
            />
          </Field>
          <Field label="Priority" htmlFor={ids.priority} error={errs.priority} hint="Lower runs first">
            <Input
              id={ids.priority}
              value={d.priority}
              inputMode="numeric"
              className="w-24"
              onChange={(e) => set({ priority: e.target.value })}
              data-testid="rule-priority"
            />
          </Field>
          {/* Centred, not bottom-aligned: the row is as tall as the Priority
              field's hint line, and a 44 px target reads against the inputs
              beside it rather than against the hint below them. */}
          <div className="flex items-center">
            <Checkbox
              label="Enabled"
              checked={d.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
              data-testid="rule-enabled"
            />
          </div>
        </div>

        <Section title="When a transaction…">
          <Field label="Merchant contains" htmlFor={ids.merchant} hint="Case-insensitive">
            <Input
              id={ids.merchant}
              value={d.merchant}
              onChange={(e) => set({ merchant: e.target.value })}
              data-testid="rule-merchant-contains"
            />
          </Field>
          <Field label="Description matches" htmlFor={ids.regex} error={errs.regex} hint="Regular expression">
            <Input
              id={ids.regex}
              value={d.regex}
              placeholder="e.g. ^AMZN"
              onChange={(e) => set({ regex: e.target.value })}
              data-testid="rule-description-regex"
            />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Amount at least" htmlFor={ids.min} error={errs.amountMin} hint="Signed">
              <Input
                id={ids.min}
                value={d.amountMin}
                inputMode="decimal"
                placeholder="-100"
                onChange={(e) => set({ amountMin: e.target.value })}
                data-testid="rule-amount-min"
              />
            </Field>
            <Field label="Amount at most" htmlFor={ids.max} error={errs.amountMax} hint="Signed">
              <Input
                id={ids.max}
                value={d.amountMax}
                inputMode="decimal"
                placeholder="-10"
                onChange={(e) => set({ amountMax: e.target.value })}
                data-testid="rule-amount-max"
              />
            </Field>
            <Field label="Direction" htmlFor={ids.direction}>
              <Select
                id={ids.direction}
                value={d.direction}
                onChange={(e) => set({ direction: e.target.value as Draft["direction"] })}
                data-testid="rule-direction"
              >
                <option value="">Either</option>
                <option value="out">Money out</option>
                <option value="in">Money in</option>
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Category is" htmlFor={ids.category}>
              <Select
                id={ids.category}
                value={d.categoryId}
                onChange={(e) => set({ categoryId: e.target.value })}
                data-testid="rule-category"
              >
                <option value="">Any category</option>
                {categories.data?.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Pending" htmlFor={ids.pending}>
              <Select
                id={ids.pending}
                value={d.isPending}
                onChange={(e) => set({ isPending: e.target.value as Tri })}
                data-testid="rule-is-pending"
              >
                <option value="">Either</option>
                <option value="true">Pending only</option>
                <option value="false">Posted only</option>
              </Select>
            </Field>
          </div>
          <Checks
            legend="Accounts"
            empty="No accounts yet."
            testid="rule-account"
            items={(accounts.data ?? []).map((a) => ({ id: a.id, label: a.name }))}
            selected={d.accountIds}
            onToggle={(id) => toggle("accountIds", id)}
          />
        </Section>

        <Section title="…do this">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Set category" htmlFor={ids.setCategory}>
              <Select
                id={ids.setCategory}
                value={d.setCategoryId}
                onChange={(e) => set({ setCategoryId: e.target.value })}
                data-testid="rule-set-category"
              >
                <option value="">Leave unchanged</option>
                {categories.data?.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Set owner" htmlFor={ids.owner}>
              <Select
                id={ids.owner}
                value={d.ownerId}
                onChange={(e) => set({ ownerId: e.target.value })}
                data-testid="rule-set-owner"
              >
                <option value="">Leave unchanged</option>
                {owners.data?.map((o) => (
                  <option key={o.id} value={o.id}>{o.name}</option>
                ))}
              </Select>
            </Field>
          </div>
          <Checks
            legend="Add tags"
            empty="No tags yet."
            testid="rule-tag"
            items={(tags.data ?? []).map((t) => ({ id: t.id, label: t.name }))}
            selected={d.tagIds}
            onToggle={(id) => toggle("tagIds", id)}
          />
          <Field label="Rename merchant to" htmlFor={ids.rename}>
            <Input
              id={ids.rename}
              value={d.renameMerchant}
              onChange={(e) => set({ renameMerchant: e.target.value })}
              data-testid="rule-rename-merchant"
            />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Visibility" htmlFor={ids.hidden}>
              <Select
                id={ids.hidden}
                value={d.setHidden}
                onChange={(e) => set({ setHidden: e.target.value as Tri })}
                data-testid="rule-set-hidden"
              >
                <option value="">Leave unchanged</option>
                <option value="true">Hide</option>
                <option value="false">Unhide</option>
              </Select>
            </Field>
            <Field label="Review status" htmlFor={ids.reviewed}>
              <Select
                id={ids.reviewed}
                value={d.markReviewed}
                onChange={(e) => set({ markReviewed: e.target.value as Tri })}
                data-testid="rule-mark-reviewed"
              >
                <option value="">Leave unchanged</option>
                <option value="true">Mark reviewed</option>
                <option value="false">Mark needs review</option>
              </Select>
            </Field>
          </div>
          {/* A rule that writes with nothing to match on is a catch-all, which is
              occasionally what someone wants and usually a forgotten condition. */}
          {/* role="status" for the same reason the missing-FX-rate warning
              carries it (§6.4): the sentence appears in response to what the
              user just typed, and nothing moves focus to it. */}
          {!hasAnyCondition(d) && (
            <p
              role="status"
              aria-atomic="true"
              className="text-xs text-warning/90"
              data-testid="rule-no-conditions"
            >
              No conditions — this rule matches every transaction.
            </p>
          )}
          <p className="text-xs text-fg-muted">
            A field a person has already set is never overwritten. Tags are added, never removed.
          </p>
        </Section>

        {failure && (
          <p
            role="alert"
            className="rounded-control bg-negative/10 px-3 py-2 text-sm text-negative"
            data-testid="rule-error"
          >
            {(failure as Error).message}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-card bg-surface-inset/40 p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{title}</h3>
      {children}
    </section>
  );
}

/** A scrollable checkbox list — the same control for accounts and tags, which
 * are both "pick any number of these". */
function Checks({
  legend,
  empty,
  testid,
  items,
  selected,
  onToggle,
}: {
  legend: string;
  empty: string;
  testid: string;
  items: { id: string; label: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs font-medium text-fg-muted">{legend}</legend>
      {items.length === 0 ? (
        <p className="text-xs text-fg-muted">{empty}</p>
      ) : (
        <div className="flex max-h-32 flex-wrap gap-x-4 gap-y-1 overflow-y-auto rounded-control bg-surface-raised/60 p-2">
          {items.map((it) => (
            <Checkbox
              key={it.id}
              label={it.label}
              checked={selected.includes(it.id)}
              onChange={() => onToggle(it.id)}
              data-testid={`${testid}-${it.id}`}
            />
          ))}
        </div>
      )}
    </fieldset>
  );
}

/** Only the keys the user actually set are sent. `conditions` replaces wholesale
 * server-side, so an omitted key is how a cleared condition disappears. */
function conditionsFrom(d: Draft): RuleConditions {
  const c: RuleConditions = {};
  if (d.merchant.trim()) c.merchant_contains = d.merchant.trim();
  if (d.regex.trim()) c.description_regex = d.regex.trim();
  if (d.amountMin.trim()) c.amount_min = d.amountMin.trim();
  if (d.amountMax.trim()) c.amount_max = d.amountMax.trim();
  if (d.direction) c.direction = d.direction;
  if (d.accountIds.length) c.account_ids = d.accountIds;
  if (d.categoryId) c.category_id = d.categoryId;
  if (d.isPending) c.is_pending = d.isPending === "true";
  return c;
}

function actionsFrom(d: Draft): RuleActions {
  const a: RuleActions = {};
  if (d.setCategoryId) a.set_category_id = d.setCategoryId;
  if (d.tagIds.length) a.add_tag_ids = d.tagIds;
  if (d.ownerId) a.set_owner_id = d.ownerId;
  if (d.renameMerchant.trim()) a.rename_merchant = d.renameMerchant.trim();
  if (d.setHidden) a.set_hidden = d.setHidden === "true";
  if (d.markReviewed) a.mark_reviewed = d.markReviewed === "true";
  return a;
}

function hasAnyCondition(d: Draft): boolean {
  return Object.keys(conditionsFrom(d)).length > 0;
}

/** JS regex syntax is not Python's, so this only catches the patterns a typo
 * produces; the server compiles the real one and answers 422 for the rest. */
function compiles(pattern: string): boolean {
  try {
    new RegExp(pattern);
    return true;
  } catch {
    return false;
  }
}
