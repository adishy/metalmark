# ADR 0034: A position's quantity comes from history too, when history exists

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Refines:** ADR-0020 (fills a gap it left)
- **Related:** ADR-0011, ADR-0021, ADR-0032, ADR-0033

## Context

ADR-0020 settled cost basis: *"if `investment_transactions` exist for a
(security, account), cost basis and realized gains are computed from that history;
otherwise the manually-entered scalar `cost_basis` is authoritative. The two are
never both authoritative for the same position."*

It says nothing about **quantity**, and quantity has the same two candidate
writers. That gap is not benign, because basis and quantity are read together:

- basis is `Σ(cash paid) ± sale allocations` — computed *from history*;
- quantity would be `holdings.quantity` — a **scalar**.

So a household that records a buy through `investment_transactions` and also has a
hand-entered `holdings.quantity` gets a position whose basis reflects trades and
whose share count does not. The failure is silent and it is arithmetic:
`average cost = basis / quantity` divides a history-derived numerator by an
unrelated denominator, and the answer is a number that looks like a real cost per
share. ADR-0032's appreciation term reads the same pair, so the wrong quantity also
propagates into net worth's decomposition.

There is a second, sharper case. A position whose history says it was fully sold
has a basis of zero and a *history-implied* quantity of zero, while its
`holdings.quantity` scalar can still read 10. Valuation multiplies that 10 by the
current price and reports a position that does not exist.

## Decision

**When `investment_transactions` exist for a (security, account), the position's
quantity is derived from them, exactly as its cost basis is.** The scalar
`holdings.quantity` becomes authoritative only in the no-history case, which is
ADR-0020's rule applied to the field it did not name.

`quantity` is `Σ(signed quantity)` over the history — the same sum the sign
convention in `models/investments.py` exists to make trivial. A `buy` adds,
a `sell` subtracts, a `split` applies its delta.

**One discriminator, not two.** The test for "does history exist" is the same one
ADR-0020 already specifies, and it decides both fields together. They cannot be in
different modes, because that is precisely the incoherence above.

**A manual write is refused, not ignored.** `PATCH /investments/holdings/{id}`
answering 409 for a position whose history is authoritative — the same posture the
ledger takes on editing a split parent's amount. A silently-dropped write is worse
than a rejection: the client believes a value was stored, and the next read
contradicts it with no explanation of where the number came from.

**Both sources are reported.** Holding responses carry
`quantity_source` and `basis_source` (`history` | `manual`), so the UI can say
which of the two the household is looking at. ADR-0021 already established that
the UI should explain a reconciling plug rather than hide it; a position whose
hand-entered quantity is being ignored deserves the same honesty.

## Consequences

- **Positive:** the position is internally consistent by construction, so
  `basis / quantity` cannot be a ratio of unrelated numbers; a fully-sold position
  disappears rather than reporting phantom shares; and the discriminator is
  ADR-0020's existing rule, so nothing new has to be remembered.
- **Negative / costs:** a household that records *some* trades but keeps the
  position scalar current by hand will find the scalar ignored from the first
  trade onward. That is the same surprise ADR-0020 already accepted for basis, and
  `quantity_source` is what makes it visible rather than mysterious. It also means
  deleting a trade can move a position's quantity — correctly, but it is a write
  path (delete a transaction, recompute the holding) that has to exist and be
  tested, not a read-time curiosity.
- **Follow-ups:** a "reconcile position to provider balance" flow would be the
  natural repair for the hand-maintained-scalar case, and it is out of scope here.
  ADR-0020's realized-gains work reads the same history and inherits this rule
  unchanged.
