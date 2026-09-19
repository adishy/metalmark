# ADR 0020: Investment cost basis — average cost for v1; lots deferred

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0011 (refines), ADR-0021, ARCHITECTURE.md §2

## Context

ADR-0011 modeled `holdings.cost_basis` as a single scalar, with no lot tracking — so FIFO/specific-lot is
impossible and there's no realized-gain concept. Also, cost basis has two potential writers: set directly on
the holding, and implied by `investment_transactions` (buy/sell). Manual parity makes both first-class, so we
need a rule for which wins.

## Decision

- **v1 uses average-cost** basis. Lot-level / FIFO / specific-lot tracking is **deferred** (no lot table).
- **Single authority:** if `investment_transactions` exist for a (security, account), cost basis and realized
  gains are **computed from that history**; otherwise the manually-entered scalar `cost_basis` is authoritative.
  The two are never both authoritative for the same position.

## Consequences

- **Positive:** simple, well-defined, covers the common case; no ambiguous double-write.
- **Negative / costs:** not suitable for tax-lot optimization or precise realized-gain-by-lot reporting —
  explicitly out of scope for v1.
- **Follow-ups:** a later ADR if lot tracking is needed; test that transaction-derived basis overrides the
  scalar when both are present.
