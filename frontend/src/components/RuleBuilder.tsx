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
// arithmetic — the numeric comparisons are the bounds check, which the server
// re-does in Decimal, and the split's sign and percent checks, which the server
// re-does in Decimal too.
//
// The split action (ADR-0031) is the one action whose value is a list, so it is
// a block of its own inside "…do this" rather than a control beside the others.
// Its legs are a list in the draft for the same reason the rest of the draft is
// strings: the editor has states the wire shape does not (a leg marked as the
// rest that still has an amount typed in it), and the checks in `splitIssues`
// are what closes the gap, in the words the API's own 422 uses.

import { useState, type ReactNode } from "react";
import { useAccounts, useCategories, useOwners, useTags } from "@/api/hooks";
import {
  useCreateRule,
  useUpdateRule,
  type Rule,
  type RuleActions,
  type RuleConditions,
  type RuleSplitLeg,
} from "@/api/rules";
import Dialog from "@/components/Dialog";
import { Button, Checkbox, Field, Input, Select, Spinner, requiredText, useFieldId, validAmount } from "@/components/form";

type Tri = "" | "true" | "false";

/** One leg of the split, as the editor holds it. `amount` and `percent` are both
 * here even though a leg may carry only one: which one is filled is the user's
 * answer, and deciding it in the draft would mean guessing when they switch. */
interface Leg {
  amount: string;
  percent: string;
  remainder: boolean;
  categoryId: string;
  ownerId: string;
  /** Carried through, never edited here — a leg's note is about one transaction,
   * and the manual split editor does not edit one either. */
  notes: string;
}

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
  /** The split action is off (no split) or on (legs, and they must be legal). */
  splitOn: boolean;
  legs: Leg[];
}

function blankLeg(): Leg {
  return { amount: "", percent: "", remainder: false, categoryId: "", ownerId: "", notes: "" };
}

function initial(rule: Rule | null | undefined): Draft {
  const c: RuleConditions = rule?.conditions ?? {};
  const a: RuleActions = rule?.actions ?? {};
  const legs = a.split ?? [];
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
    // A rule with legs has a split; a rule with none is not split at all, which
    // is what the toggle's off state means.
    splitOn: legs.length > 0,
    legs: legs.map((l) => ({
      amount: l.amount ?? "",
      percent: l.percent ?? "",
      // `remainder` is null on every leg but one, so this is the only reading of
      // "is this leg the rest" that does not treat null as falsy by accident.
      remainder: l.remainder === true,
      categoryId: l.category_id ?? "",
      ownerId: l.owner_id ?? "",
      notes: l.notes ?? "",
    })),
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
  // Kept apart from `errs` because a split has several things wrong with it at
  // once often enough to be worth saying all of them, and each names its own leg.
  const [splitErrs, setSplitErrs] = useState<string[]>([]);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const toggle = (key: "accountIds" | "tagIds", id: string) =>
    set({
      [key]: d[key].includes(id) ? d[key].filter((x) => x !== id) : [...d[key], id],
    } as Partial<Draft>);

  const setLeg = (i: number, patch: Partial<Leg>) =>
    set({ legs: d.legs.map((l, idx) => (idx === i ? { ...l, ...patch } : l)) });
  const addLeg = () => set({ legs: [...d.legs, blankLeg()] });
  const removeLeg = (i: number) => set({ legs: d.legs.filter((_, idx) => idx !== i) });

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
    // A leg's controls are a list, so their ids are derived from one field id
    // rather than one hook each: a hook per leg would be a hook count that
    // changes with the list.
    leg: useFieldId("rule-split-leg"),
  };
  const legId = (i: number, control: string) => `${ids.leg}-${i}-${control}`;

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
    // The split's own checks, which are reasons rather than a disabled Save: a
    // button that is greyed out says nothing about what is wrong with it (§4.1).
    const issues = splitIssues(d);
    setErrs(next);
    setSplitErrs(issues);
    if (Object.values(next).some(Boolean) || issues.length > 0) return;

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
          <Button onClick={save} disabled={saving} aria-busy={saving} data-testid="rule-save">
            {saving && <Spinner />}
            Save rule
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

          {/* ---- the split action (ADR-0031) --------------------------------
              Every other action here is one value; a split is a list of legs, so
              it gets a block rather than a seat in the grid. The editor's shape
              is the wire shape: a leg carries an amount or a percent, and exactly
              one leg carries neither because it takes what the others leave. */}
          <div className="space-y-3 border-t border-border pt-3">
            <Checkbox
              label="Split the transaction"
              hint="Each leg takes a share of it, with its own category and owner."
              checked={d.splitOn}
              onChange={(e) =>
                set({
                  splitOn: e.target.checked,
                  // Seeded with the shape a split has to have — an amount leg,
                  // then the leg that takes the rest — so switching this on does
                  // not hand the user a split the server would refuse.
                  legs:
                    e.target.checked && d.legs.length === 0
                      ? [blankLeg(), { ...blankLeg(), remainder: true }]
                      : d.legs,
                })
              }
              data-testid="rule-split-toggle"
            />

            {d.splitOn && (
              <div className="space-y-3">
                {/* The restriction ADR-0031 accepts on purpose, said where a
                    person reaches for it — someone writing "$50 here and the
                    rest there" must see that it is an amount leg plus the
                    remainder leg, not two amounts. */}
                <p className="text-xs text-fg-muted" data-testid="rule-split-explainer">
                  A split is a share of the transaction, not a fixed amount of it. One leg is
                  the rest: it takes whatever the other legs leave, so “$50 here, the rest
                  there” is an amount leg plus the remainder leg — never two amounts. That is
                  what keeps the split adding up to its transaction when the bank corrects the
                  amount.
                </p>

                {d.legs.map((leg, i) => (
                  <fieldset
                    key={i}
                    // The same well as the account and tag lists above, so a leg
                    // reads as one group and its inputs stay distinct from the
                    // ground they sit on (§2.6).
                    className="space-y-2 rounded-control bg-surface-raised/60 p-2"
                    data-testid={`rule-split-leg-${i}`}
                  >
                    {/* The number is the server's numbering, not a new one: a 422
                        names the leg it is about, and `splitIssues` below names
                        the same one. */}
                    <legend className="text-xs font-medium text-fg-muted">Leg {i + 1}</legend>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      <Field
                        label="Amount"
                        htmlFor={legId(i, "amount")}
                        hint="Signed, like the transaction"
                      >
                        <Input
                          id={legId(i, "amount")}
                          value={leg.amount}
                          inputMode="decimal"
                          placeholder="-50"
                          onChange={(e) => setLeg(i, { amount: e.target.value })}
                          data-testid={`rule-split-leg-${i}-amount`}
                        />
                      </Field>
                      <Field label="Percent" htmlFor={legId(i, "percent")} hint="Of the transaction">
                        <Input
                          id={legId(i, "percent")}
                          value={leg.percent}
                          inputMode="decimal"
                          placeholder="25"
                          onChange={(e) => setLeg(i, { percent: e.target.value })}
                          data-testid={`rule-split-leg-${i}-percent`}
                        />
                      </Field>
                      {/* Centred against the two inputs beside it, the same
                          alignment as the Enabled box in the header. */}
                      <div className="flex items-center">
                        <Checkbox
                          label="The rest"
                          checked={leg.remainder}
                          onChange={(e) =>
                            setLeg(i, {
                              remainder: e.target.checked,
                              // A leg that takes the rest takes no share of its
                              // own, so marking it drops whatever share it was
                              // carrying instead of leaving a value the server
                              // would refuse.
                              ...(e.target.checked ? { amount: "", percent: "" } : {}),
                            })
                          }
                          data-testid={`rule-split-leg-${i}-rest`}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <Field label="Category" htmlFor={legId(i, "category")}>
                        <Select
                          id={legId(i, "category")}
                          value={leg.categoryId}
                          onChange={(e) => setLeg(i, { categoryId: e.target.value })}
                          data-testid={`rule-split-leg-${i}-category`}
                        >
                          <option value="">Uncategorized</option>
                          {categories.data?.map((c) => (
                            <option key={c.id} value={c.id}>{c.name}</option>
                          ))}
                        </Select>
                      </Field>
                      <Field label="Owner" htmlFor={legId(i, "owner")}>
                        <Select
                          id={legId(i, "owner")}
                          value={leg.ownerId}
                          onChange={(e) => setLeg(i, { ownerId: e.target.value })}
                          data-testid={`rule-split-leg-${i}-owner`}
                        >
                          <option value="">Same as the transaction</option>
                          {owners.data?.map((o) => (
                            <option key={o.id} value={o.id}>{o.name}</option>
                          ))}
                        </Select>
                      </Field>
                    </div>
                    {/* What the leg will take, on the row that also removes it.
                        Nothing on this row is a guess at the transaction's
                        amount: a leg that names one says what it was given, and
                        the leg that does not says "the rest", because that is
                        the whole of what the rule knows. */}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p
                        className="text-xs text-fg-muted"
                        data-testid={`rule-split-leg-${i}-takes`}
                      >
                        {takesText(leg)}
                      </p>
                      <Button
                        variant="ghost"
                        className="text-xs"
                        onClick={() => removeLeg(i)}
                        data-testid={`rule-split-leg-${i}-remove`}
                      >
                        Remove
                      </Button>
                    </div>
                  </fieldset>
                ))}

                <Button
                  variant="ghost"
                  className="text-xs"
                  onClick={addLeg}
                  data-testid="rule-split-add-leg"
                >
                  + Add leg
                </Button>

                {/* role="alert" per line, the same as a Field's error: the
                    sentence appears in response to Save, and nothing moves focus
                    to it. */}
                {splitErrs.length > 0 && (
                  <div className="space-y-1" data-testid="rule-split-error">
                    {splitErrs.map((message) => (
                      <p key={message} role="alert" className="text-sm text-negative">
                        <span aria-hidden="true">⚠ </span>
                        {message}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            )}
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
      {/* `max-h-40`, not `max-h-32`. The rows are now 44 px label targets
          (§4.14), so the old 128 px well showed two whole rows and a third
          sliced through the middle — which reads as a rendering fault rather
          than as "there is more below". 160 px less the 16 px of padding fits
          three whole rows (3×44 + 2×4 gap = 140) with a few pixels of the
          fourth showing, which is the scroll affordance the sliced row was
          accidentally standing in for. */}
      {items.length === 0 ? (
        <p className="text-xs text-fg-muted">{empty}</p>
      ) : (
        <div className="flex max-h-40 flex-wrap gap-x-4 gap-y-1 overflow-y-auto rounded-control bg-surface-raised/60 p-2">
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
  // An omitted key is how a cleared action disappears, which is also how a rule
  // that had a split loses it: the toggle off means no `split` is sent at all.
  if (d.splitOn) a.split = splitFrom(d.legs);
  return a;
}

/** The legs, in the wire shape: only the keys a leg actually carries. */
function splitFrom(legs: Leg[]): RuleSplitLeg[] {
  return legs.map((l) => {
    const leg: RuleSplitLeg = {};
    if (l.remainder) {
      // The remainder carries neither amount nor percent — it takes what the
      // other legs leave, which is what keeps the sum exact for any amount.
      leg.remainder = true;
    } else if (l.amount.trim()) {
      leg.amount = l.amount.trim();
    } else if (l.percent.trim()) {
      leg.percent = l.percent.trim();
    }
    if (l.categoryId) leg.category_id = l.categoryId;
    if (l.ownerId) leg.owner_id = l.ownerId;
    // A leg's note is not edited here, but it is carried: a rule is written once
    // and applied to many transactions, and a re-save that dropped it would be
    // the silent edit this dialog exists to avoid.
    if (l.notes.trim()) leg.notes = l.notes.trim();
    return leg;
  });
}

/** What one leg will take, in the split's own words. Nothing here knows the
 * transaction's amount — a rule is written once and applied to rows it cannot
 * see — so an amount leg says the amount it was given and the remainder says
 * "the rest", which is the whole of what the rule knows. */
function takesText(leg: Leg): string {
  if (leg.remainder) return "Takes the rest";
  if (leg.amount.trim()) return `Takes ${leg.amount.trim()}`;
  if (leg.percent.trim()) return `Takes ${leg.percent.trim()}%`;
  return "Takes nothing yet";
}

/** The number a leg's amount or percent field holds, or null when it holds
 * none — the draft keeps both as text until the user is done typing. */
function fieldNumber(text: string): number | null {
  const trimmed = text.trim();
  return trimmed && !validAmount(trimmed) ? Number(trimmed) : null;
}

/**
 * The split's checks, in the words ADR-0031 and the API's 422 use: a client that
 * refuses with a different vocabulary teaches the user a rule the server does
 * not have. Each message names its leg, because "the schema is a 422 otherwise"
 * is only actionable if it says which leg is the problem.
 *
 * These are reasons, not a disabled Save. The editor can reach every one of
 * these states — a leg marked as the rest with an amount typed into it, two legs
 * both marked, a percent of 150 — and the server re-checks all of them in
 * Decimal on the way in, so this only saves the round trip.
 */
function splitIssues(d: Draft): string[] {
  if (!d.splitOn) return [];
  const out: string[] = [];

  d.legs.forEach((leg, i) => {
    const n = i + 1;
    const percent = leg.percent.trim();
    if (leg.remainder) {
      if (leg.amount.trim() || percent) {
        out.push(
          `Leg ${n} takes the rest, so it carries no amount or percent — it takes what the other legs leave.`,
        );
      }
      return;
    }
    if (!leg.amount.trim() && !percent) {
      out.push(
        `Leg ${n} carries neither an amount nor a percent — every leg that is not the rest carries exactly one of them.`,
      );
      return;
    }
    if (leg.amount.trim() && percent) {
      out.push(
        `Leg ${n} carries both an amount and a percent — a leg takes one or the other, never two.`,
      );
      return;
    }
    if (percent) {
      const p = fieldNumber(percent);
      if (p === null) {
        out.push(`Leg ${n}: enter a valid number for the percent.`);
        return;
      }
      if (!(p > 0 && p < 100)) {
        out.push(
          `Leg ${n}: percent must be greater than 0 and less than 100 — the leg that takes the rest is the remainder leg.`,
        );
      }
      return;
    }
    const amount = fieldNumber(leg.amount);
    if (amount === null) {
      out.push(`Leg ${n}: enter a valid number for the amount.`);
      return;
    }
    if (amount === 0) {
      out.push(
        `Leg ${n}: an amount leg must be non-zero — a leg that moves nothing is a leg to delete.`,
      );
    }
  });

  // Below two legs the rest of the checks are noise: one leg is the whole
  // transaction wearing a split's clothes, zero is nothing at all, and the
  // problem is the count.
  if (d.legs.length < 2) {
    out.push(
      "A split needs at least two legs — an amount or percent leg plus the rest, because a one-leg split is just the transaction.",
    );
    return out;
  }

  const rests = d.legs.map((l, i) => (l.remainder ? i + 1 : 0)).filter((n) => n > 0);
  if (rests.length === 0) {
    out.push(
      "No leg takes the rest — mark exactly one leg as the remainder, without which the legs cannot sum to a transaction amount the rule does not know when it is written.",
    );
  } else if (rests.length > 1) {
    out.push(
      // "both" would be wrong at three and the server's wording has the same
      // flaw; the reason is what has to match, not the quantifier.
      `${rests.map((n) => `leg ${n}`).join(" and ")} are marked as the rest — exactly one leg takes what is left, because two would have no principle to divide it by.`,
    );
  }

  const taken = d.legs
    .filter((l) => !l.remainder)
    .reduce((sum, l) => sum + (fieldNumber(l.percent) ?? 0), 0);
  if (taken >= 100) {
    out.push(
      `The percent legs take ${taken}% of the transaction between them — they must come to less than 100%, or the leg that takes the rest is left with nothing to take.`,
    );
  }

  // ADR-0031 §2: every amount leg shares the parent's sign, which a rule cannot
  // know — but it does know its legs, and they can only all match one sign if
  // they agree with each other. That is what makes a mixed-sign split (a
  // transfer, which ADR-0018 models) unresolvable here.
  // A zero leg is left out: it has no sign to disagree with, and it is already
  // reported as a leg of its own.
  const signs = new Set(
    d.legs
      .filter((l) => !l.remainder)
      .map((l) => fieldNumber(l.amount))
      .filter((n): n is number => n !== null && n !== 0)
      .map((n) => Math.sign(n)),
  );
  if (signs.size > 1) {
    out.push(
      "The amount legs point both ways — they must all share the transaction's sign, because legs pointing both ways are a transfer, not a split.",
    );
  } else if (d.direction === "out" && signs.has(1)) {
    // The builder knows something the schema does not: this rule's own direction
    // condition says the transaction is negative, so a positive amount leg can
    // never match it. Same reason, said with what is on screen.
    out.push(
      "This rule matches money out, so the transaction is negative — its amount legs must be negative too.",
    );
  } else if (d.direction === "in" && signs.has(-1)) {
    out.push(
      "This rule matches money in, so the transaction is positive — its amount legs must be positive too.",
    );
  }
  return out;
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
