# ADR 0018: Cross-currency transfers — match on base amount, surface the FX cost

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0008 (extends), ADR-0017, ARCHITECTURE.md §2, §3

## Context

ADR-0008 links transfer legs and excludes them from cash-flow, matching on "opposite-sign, equal magnitude."
With single-currency accounts in different currencies (ADR-0017), a transfer is e.g. −100 EUR out / +108 USD
in — the legs are **not** equal in native amount, so equal-magnitude matching fails. Worse, the legs don't
net to zero in base terms; the residual is a **real FX spread/fee** that the transfer exclusion would hide.

## Decision

Extend transfer handling for cross-currency:
- **Matching:** same-currency legs match on equal magnitude; cross-currency legs match on **`base_amount`
  within a tolerance**, or by explicit user linking.
- **Residual:** the base-amount difference between the legs (spread + fee) is stored on the group as
  `fx_cost_base` and **surfaced** (as a fee / part of currency revaluation), never silently hidden by the
  cash-flow exclusion.

## Consequences

- **Positive:** cross-currency transfers link correctly and their real cost stays visible; keeps ADR-0008's
  exclusion honest.
- **Negative / costs:** base-amount tolerance matching is fuzzier than exact-magnitude (risk of false
  matches) — mitigated by tolerance + a manual confirm/override.
- **Follow-ups:** a cross-currency transfer acceptance bar (−100 EUR/+108 USD auto-matches, residual shown).
