# ADR 0010: Manual-first build order and manual-parity principle

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0007, ADR-0009, ADR-0011, PLAN.md (phases)

## Context

The hosted apps' manual/cash workflow is second-class, a common complaint. The owner wants correctness
first and nothing that can only be done automatically. Automation that writes into an unproven model risks
corrupting it.

## Decision

Two linked rules:
1. **Manual parity** — anything sync/import can do, a human can do by hand (accounts, transactions, splits,
   categories, transfers, holdings + types, prices, investment transactions, balances, FX rates). The manual
   write path is **canonical**.
2. **Manual-first build order** — Phase 1 builds and proves the manual ledger under a green test suite; Phase 2
   adds SimpleFIN sync and OFX import as **"just another writer"** into that proven model (tagging fields
   `provider` per ADR-0007). M1 is a fully usable app with no sync at all.

## Consequences

- **Positive:** correctness before convenience; sync can't write anything the model doesn't already support;
  users are never stuck when automation fails.
- **Negative / costs:** defers integration risk to Phase 2 — mitigated by building the mock SimpleFIN server
  and validating the provider→manual-model mapping early in WS-T (P0 spike, ADR-0022), not at Phase 2 start.
- **Follow-ups:** a manual-parity acceptance bar per automated capability; early spike on SimpleFIN data shape.
