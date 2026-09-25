# ADR 0021: Investment account balance source — derived vs stated, with a reconciling plug

- **Status:** Accepted, amended by 0044 (a derived account with no positions is read from its balance; synced accounts are stated) and 0051 (sync writes the bank's holdings as positions)
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0011 (refines), ADR-0020, ARCHITECTURE.md §2, §4

## Context

SimpleFIN gives an investment account a **balance but no holdings**; a manual account derives balance =
Σ(holding market values). If a synced account (stated balance, no holdings) later gets hand-added holdings,
`stated balance ≠ Σ(holdings)`. Net worth and the allocation view need one unambiguous number, and the two
sources shouldn't silently disagree.

## Decision

Each account carries **`balance_source`**:
- `derived` — balance = Σ(holding market values), converted to base (default for manual investment accounts,
  and the basis for the allocation view).
- `stated` — a provider/entered balance kept as-is (default for a synced investment account with no holdings).

When a `stated` account also has hand-added holdings, the gap between stated balance and Σ(holdings) is
represented by an explicit **"unaccounted cash" plug** holding, so the allocation view and net worth
reconcile instead of silently diverging.

## Consequences

- **Positive:** one authoritative balance per account; allocation view always reconciles to net worth; the
  synced-then-manually-augmented case is handled explicitly.
- **Negative / costs:** the plug is a concept users must understand; UI must explain it.
- **Follow-ups:** test the stated-balance + added-holdings reconciliation; allocation view reads the right basis.
