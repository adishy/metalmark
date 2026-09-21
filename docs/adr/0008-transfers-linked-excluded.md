# ADR 0008: Transfers are linked groups, excluded from cash-flow

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0006, ADR-0007, ARCHITECTURE.md §2, §4

## Context

Moving money between accounts (checking→savings, card/loan payments) creates two transactions. If they aren't
linked and excluded, the cash-flow Sankey and income/expense trends show phantom income and phantom expense —
defeating the point of the reporting. This is a signature feature of the hosted apps.

## Decision

We will model transfers as a **`transfer_group`** linking the legs, **auto-matched on ingest** (opposite-sign,
equal-magnitude, cross-account, near dates) with a manual link/override in the UI. **All cash-flow and spend
reporting excludes transfers**; they still appear in the transaction list.

## Consequences

- **Positive:** correct cash-flow/net income; matches the hosted apps' behavior; reconciles list vs. report
  totals.
- **Negative / costs:** cross-currency transfers are **not** equal-and-opposite (see ADR-0006) — matching and
  any "legs balance" invariant must be currency-aware, comparing base-currency amounts within tolerance.
- **Follow-ups:** define cross-currency transfer matching + residual (FX difference) handling in a follow-up
  ADR if review confirms it's needed; test a checking→savings pair links and is netted out.
