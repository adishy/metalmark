# ADR 0031: Auto-split rules — balanced by construction, and a human's split is final

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Related:** ADR-0007, ADR-0019, ADR-0026, `docs/PLAN-v0.9.md` decision K, `services/rules.py`, `services/transactions.py`

## Context

`Rule.actions`' own comment (`models/ledger.py:399-401`) lists six actions and ends *"auto-split is deferred
to the sync phase (§2)"*. Splits themselves are not deferred — they ship, and they ship with a property that
decides this whole design:

**A split parent's amount is the sum of its children, and that invariant is already enforced.**
`services/transactions.py:197` refuses to edit a split parent's amount directly, and `replace_splits`
(`:292-333`) allocates a parent's amount across its legs, requiring either all-percent or all-amount and
distributing the base-amount rounding across them. A rule that creates splits is therefore writing into a
structure with a real invariant, not a free-form JSON blob.

**The provenance mechanism this needs already exists, and already covers splits.**
`services/transactions.py:332` marks `field_sources["splits"] = "user"` when a human replaces a transaction's
splits. `TransactionSplit` has no provenance column of its own — but it does not need one, because the
parent's `field_sources` map keys on the *field name*, and `"splits"` is already a key. The rule engine's
existing rule (ADR-0007: never write a field whose `field_sources` entry is `user`) then applies verbatim,
with nothing new to invent.

Two questions decide the rest. What does a split rule *say*, given that a rule is written once and applied to
transactions whose amounts the rule cannot know? And what happens when a bank later corrects an amount on a
row that has already been split?

## Decision

**1. A `split` action, added to `ACTION_KEYS` (which stays a closed frozenset).**

```
split: [ {amount | percent, category_id?, owner_id?, notes?}, …, {remainder: true, category_id?, …} ]
```

`RuleBlob` sets `extra="forbid"`, so the closed key set keeps rejecting anything else, and the action's own
inner shape is a second `RuleBlob` with the same discipline.

**2. Exactly one leg is the remainder, and it is required.**

The remainder takes the parent's amount minus every other leg. The sum is therefore balanced **by
construction, for any parent amount** — not validated against an amount the rule had no way to know when it
was written. Every other leg carries exactly one of `amount` or `percent`, and the schema is a 422
otherwise: an amount leg is signed and must match the parent's sign (a mixed-sign split is a transfer, which
ADR-0018 already models properly), a percent leg is `0 < p < 100` and takes that fraction of the parent's
*magnitude*, signed to the parent.

This is the decision that makes "apply to existing" safe. The alternative — legs that must sum to 100% with
no remainder — is one bank correction away from a split that does not add up, and the invariant at
`transactions.py:197` means the correction is then *refused*, which would leave the provider's own data
unable to land.

**3. A rule may only split a transaction whose splits it owns.**

`field_sources["splits"] == "user"` means a human built this split, and ADR-0007's rule already says a rule
does not write a field a human owns. So: a human's split is final, a rule's split is revisable, and this needs
no new column, no new concept, and no new sentence in the rule engine.

**4. Idempotence is by comparison, not by a flag.**

Re-running recomputes the legs from the rule and the current parent amount and writes only when the resulting
set differs from what is stored. "Apply to existing" run twice over the same rule set is a no-op the second
time, which is the bar — and it falls out of `_apply_to_row`'s existing `(matched, changed)` return rather
than needing a separate record of what ran.

**5. When a parent's amount changes, its rule-owned splits are recomputed.**

Sync updates an amount on a rule-split row; the splits are re-derived rather than left stale. Without this,
the invariant at `transactions.py:197` refuses the provider's update and the ledger silently stops tracking
the bank — the row keeps a split that no longer sums to its own amount, which is the worst of the available
outcomes because every total downstream is now quietly wrong. A `user`-owned split is *not* recomputed; the
update is refused for that row exactly as it is today, and the run reports it.

**6. One allocation path, shared with the human's.**

The rule path builds the same leg shape `replace_splits` takes and calls the same allocation code, so
base-amount distribution and rounding-remainder behaviour cannot diverge between a split a person made and a
split a rule made. Two implementations of "divide this amount across these legs" is how the two ends up
differing by a cent.

## Consequences

- **Positive:** every rule-made split sums to its parent for every possible amount, which is the property the
  feature exists to have; no migration (`field_sources` already carries `"splits"`, and `replace_splits`
  already writes it); the human-versus-rule boundary is ADR-0007 applied to a key that already exists rather
  than a new rule; a human's split survives a re-run of the rule that would have made a different one.
- **Negative / costs:** a rule cannot express a fixed-dollar split with no remainder leg — *"$50 here, and
  the rest wherever"* must be written as an amount leg plus the remainder. That restriction is deliberate and
  it is the thing that guarantees the sum, but it is a real expressiveness cost and the rule editor must
  explain it. A rule that owns a split also takes on the obligation to recompute it when the amount moves
  (§5), which is work the manual path does not do because a human editing an amount gets a refusal instead.
- **`ACTION_KEYS` grows from six to seven**, so the rules schema in `contracts/openapi.yaml` changes and
  needs the regen; it is a versioned surface and the `contract` gate will catch it.
- **Follow-ups:** splits-within-splits stay unsupported (a leg cannot itself be split). Transfers remain
  ADR-0018's job and are not expressible as a split, by design — the sign rule in §2 is what keeps the two
  from being confused for one another.
