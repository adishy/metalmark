// The rules vertical's own API surface: the rule list, its CRUD, and the
// "apply to existing" run.
//
// Kept out of api/hooks.ts (shared, and pinned by other workstreams) while
// following its conventions — including reusing the shared invalidation list for
// the one call that rewrites ledger rows. Editing a rule changes no transaction
// until the household asks for it, so only `apply` invalidates the ledger;
// creating or deleting one invalidates the rule list alone.
//
// `updated_at` is deliberately absent from `Rule`: the column exists, but the
// server does not return it (see `RuleOut` in app/schemas/rules.py).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import type { Money, UUID } from "@/api/types";

/**
 * All conditions are optional and AND-ed. The keys are a closed set: the server
 * refuses an unknown one with a 422 rather than storing a condition that will
 * never fire, so anything read here is a key the engine understands.
 *
 * A response carries every key, unset ones as null, so the read and write types
 * are the same shape. A *write* may leave a key out entirely, which is how a
 * caller clears it — `conditions` replaces wholesale rather than merging.
 */
export interface RuleConditions {
  /** Case-insensitive substring of the merchant. */
  merchant_contains?: string | null;
  /** Python `re.search` against the description. */
  description_regex?: string | null;
  /** Bounds on the SIGNED amount: spending over $100 is amount_max: "-100". */
  amount_min?: Money | null;
  amount_max?: Money | null;
  direction?: "in" | "out" | null;
  account_ids?: UUID[] | null;
  category_id?: UUID | null;
  is_pending?: boolean | null;
}

/**
 * One leg of a rule's `split` action (ADR-0031). A leg names a share of the
 * transaction it matches, never a sum of its own — which is what lets a rule
 * written once apply to transactions whose amounts it cannot know.
 *
 * Exactly one leg in a split is the `remainder`: it carries no amount and no
 * percent, and takes whatever the other legs leave. Every other leg carries
 * exactly one of `amount` (signed like every money field, and matching the
 * parent's sign — a mixed-sign split is a transfer, ADR-0018) or `percent`
 * (`0 < p < 100`, taken from the parent's magnitude and signed to the parent).
 * The server is a 422 for anything else, and its message names the leg.
 *
 * Read and write are the same shape, as everywhere in this file: a response
 * carries every key with unset ones null, and a write may leave a key out — an
 * omitted key is an unset one.
 */
export interface RuleSplitLeg {
  /** Signed decimal string; money is never a JS float (ADR-0005). */
  amount?: Money | null;
  /** Decimal string strictly between 0 and 100. */
  percent?: Money | null;
  /** `true` on the one leg that takes what the others leave. */
  remainder?: boolean | null;
  /** null = this leg's share is uncategorized. */
  category_id?: UUID | null;
  /** null = inherit the parent transaction's owner (ADR-0026). */
  owner_id?: UUID | null;
  notes?: string | null;
}

/**
 * What a matching rule writes. Tags are the one additive action (union, never
 * removed); every other field is subject to provenance — the server skips any
 * field a human has already set (ADR-0007), and `splits` is already such a key,
 * so a split a person built is final (ADR-0031 §3).
 *
 * `false` is a real action for `set_hidden` and `mark_reviewed`, not "unset":
 * it means unhide / needs_review. Leaving the key out means "do nothing".
 */
export interface RuleActions {
  set_category_id?: UUID | null;
  add_tag_ids?: UUID[] | null;
  set_owner_id?: UUID | null;
  rename_merchant?: string | null;
  set_hidden?: boolean | null;
  mark_reviewed?: boolean | null;
  /** The legs of a rule-made split, or null for no split. At least two, exactly
   * one of them the remainder. */
  split?: RuleSplitLeg[] | null;
}

export interface Rule {
  id: UUID;
  name: string;
  /** Lower runs first; ties break by creation order. */
  priority: number;
  enabled: boolean;
  conditions: RuleConditions;
  actions: RuleActions;
  created_at: string;
}

export interface RuleCreate {
  name: string;
  priority?: number;
  enabled?: boolean;
  conditions?: RuleConditions;
  actions?: RuleActions;
}

/** Absent means "no change"; `conditions`/`actions` replace wholesale, so an
 * omitted key inside them is a removed condition, not an untouched one. */
export interface RuleUpdate {
  name?: string;
  priority?: number;
  enabled?: boolean;
  conditions?: RuleConditions;
  actions?: RuleActions;
}

/**
 * The two counts "apply to existing" reports, and they are not the same number.
 * Both count transactions: `matched` is how many rows an enabled rule's
 * conditions matched, `updated` how many the engine actually changed. A second
 * run reports the same `matched` and `updated: 0` — the run is idempotent.
 */
export interface RuleApplyResult {
  matched: number;
  updated: number;
}

/** In whatever order the household wants; the server sends priority order. */
export function useRules() {
  return useQuery({ queryKey: ["rules"], queryFn: () => api.get<Rule[]>("/rules") });
}

export function useCreateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RuleCreate) => api.post<Rule>("/rules", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useUpdateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: UUID; body: RuleUpdate }) => api.patch<Rule>(`/rules/${v.id}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.del<void>(`/rules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
  });
}

/** Run the enabled rules over every existing transaction. One POST can rewrite
 * the whole ledger, so the reports and the ledger list are stale afterwards. */
export function useApplyRules() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: () => api.post<RuleApplyResult>("/rules/apply"),
    onSuccess: invalidate,
  });
}
