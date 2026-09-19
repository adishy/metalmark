# ADR 0023: Split Phase 1 into 1a/1b; carve FX into its own module

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0010, ADR-0017, ADR-0011, PLAN.md

## Context

With multi-currency + investments + observability + a first-class test harness all added, "manual-first"
Phase 1 had grown into "build almost the whole product first" — the entire ledger, FX, investments,
allocation, all Phase-1 frontend, and the harness in one phase. That's a large, slip-prone single milestone,
and the two hardest new areas (multi-currency and investments) intersect in one deliverable (the base-currency
consolidated allocation view).

## Decision

- **Split Phase 1** into **1a** (core single-currency-account ledger correctness: transactions, splits,
  transfers incl. cross-currency, categories, review/swipe, net worth + category + income/expense reports with
  the revaluation line — prove reconciliation) and **1b** (investments + consolidated allocation view +
  cash-flow Sankey + investment reporting).
- **The FX conversion service is its own workstream/module (WS-FX)**, not a sub-bullet of the critical-path
  ledger workstream — it's the highest-correctness-risk unit and a shared dependency.
- **Defer within phases:** auto-split rules → Phase 2 (basic rules in 1a); the Sankey (flashy, complex, least
  essential of the reports) → 1b, not 1a.

## Consequences

- **Positive:** smaller, verifiable milestones; currency proven in the simpler ledger before it meets
  investments; the riskiest module is isolated and owned.
- **Negative / costs:** more phase bookkeeping; a couple of features land later than a naive reading of the
  requirements might expect (all still in v1).
- **Follow-ups:** milestones M1a/M1b reflect the split; WS-FX has its own acceptance bar.
