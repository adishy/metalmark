# ADR 0056: A split is exact in both modes

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** Agent A (implementation), lead engineer (review)
- **Related:** ADR-0005 (money is Decimal, never float), ADR-0031 (auto-split rules are balanced
  by construction), ADR-0043 (every stored balance is signed), ARCHITECTURE.md §3 (`transaction_splits`)

## Context

A split allocates a parent transaction across N legs, and the legs are what every report sums
(ARCHITECTURE §3: children carry the amounts and the base amounts). The stored invariant is that
the legs sum **exactly** to the parent — in native amount and, via `allocate`, in `base_amount`
too — because a set that sums to something else is a parent and children that disagree, and
nothing downstream can tell which of them is right.

Amount-mode legs were checked against exactly that. Percentage-mode legs were not checked at
all: the mode allocates by **weight**, and `allocate` normalises by the weight sum
(`core/money.py`), so a set of legs carrying 60, or 250, was silently divided as though it were
the whole of the parent. Nothing bounded a single `pct` either — a zero or negative share was
accepted and allocated a leg worth nothing, or a leg pointing backwards.

There was a second, sharper problem in the same branch. The mode was chosen by
`any(s.pct is not None)`: one leg carrying **both** `amount` and `pct` flipped the whole set to
percentage mode, and every other leg's `amount` was then dropped — accepted, stored, and never
looked at again. Its twin was an all-amount set with one stray `pct`, which went the wrong way
for the same reason. Both are silent data loss behind a 200.

A percentage is not an amount: three equal thirds typed as decimals come to 99.99, and a rule
that could not know the parent's amount cannot express "these exact cents" at all — ADR-0031
made rule legs balanced *by construction* for exactly that reason. So the amount mode's
exactness is not transferable to the percentage mode, and the fix has to say what each mode
guarantees rather than pretend they are the same check.

## Decision

**A split set is exact in both modes, and a leg carries one share or the other.**

1. **A leg carries exactly one of `amount` or `pct`.** A leg with both is refused (the same rule
   `RuleSplitLeg` already states for a rule's legs), as is a leg with neither, and a set that
   mixes the two. Guessing which of two shares a leg meant is how the `amount` disappeared.
2. **The mode is a property of the set.** All-amount or all-pct; there is no third shape.
3. **`0 < pct <= 100` per leg**, enforced at the schema (`SplitIn.pct`) and again in the service.
   A non-positive share is not a share.
4. **A pct set's weights must sum to 100 within `PCT_SUM_TOLERANCE = 0.01`**, refused with the
   actual sum named. The tolerance is for the arithmetic of a round hundred (three 33.33s);
   it is far too narrow to admit a typo, and `allocate` normalises by the weight sum, so a set
   inside the window still divides the parent in the proportions the user meant. The legs
   themselves still sum to the parent **exactly** — the rounding cent goes to the largest leg
   (ADR-0005).
5. **An amount set still must sum exactly to the parent amount.** Unchanged, and stated here so
   the two modes are read as one invariant: *the legs sum to the parent, whatever mode produced
   them.*

## Consequences

- **Positive:** the two ways a split could be saved wrong — weights that are not a whole, and a
  leg whose stated number is ignored — are now refusals with a message, not silent allocation.
  A client that gets "Split percentages must sum to 100, got 60" can fix its own bug; before, it
  got a 200 and a set it did not ask for.
- **Positive:** the mode decision is total. Each leg is exactly one shape, so "which mode is
  this set" has one answer and no leg's fields are ever dropped.
- **Negative / costs:** a client that sends both fields per leg now gets a 400 rather than a
  result it was implicitly relying on. Nothing in this repo did that (the manual API is the only
  sender of `SplitIn`; the rules engine resolves its percent legs to amounts before writing), and
  the UI has no percentage entry at all — but the refusal is the point, not an accident.
- **Negative / costs:** the percentage tolerance is a second, looser rule beside the amount
  mode's exactness. Both are documented at their check; a reader who assumes symmetry will be
  surprised, which is why this ADR exists.
- **Follow-ups:** a client's own "balanced" gate has to agree with these rules exactly — a
  client that says balanced and a server that says 400 is the same class of failure from the
  other side. The split sheet (which today offers amounts only) sums in integer minor units for
  that reason.
