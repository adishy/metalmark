# ADR 0006: Multi-currency is first-class, converted at dated FX rates

- **Status:** Superseded by ADR-0017
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0005, ADR-0008, ADR-0011, ADR-0017 (supersedes this), ARCHITECTURE.md §2
- **Note:** A design review found this ADR over-claimed multi-currency (it implied per-transaction currency
  independent of the account, which contradicts the single-currency balance/snapshot model). **ADR-0017**
  replaces it with the concrete, decided model: single-currency accounts, `base_amount` as an invalidatable
  cache, high-precision dated rates, and a currency-revaluation line. Kept for the reasoning trail.

## Context

The household holds accounts in more than one currency. Half-modeling currency (a single base, ignoring the
rest) produces wrong net worth. We must convert for rollups while keeping history stable as rates move.

## Decision

We will make currency **first-class**: each account and transaction carries a native `currency`; each
household has a `base_currency`. An `fx_rates(base, quote, rate_date, rate, source)` table drives conversion.
**"Current" views use the latest rate; time-series views convert each point at that date's rate** so history
doesn't rewrite itself. Rates auto-fetch daily (Frankfurter/ECB) with **manual entry** as a first-class
fallback. The converted `base_amount` and the `fx_rate_date` used are stored for reproducibility.

## Consequences

- **Positive:** correct multi-currency net worth and reporting; stable history; offline-friendly (manual rates).
- **Negative / costs:** significant added complexity — cross-currency transfers won't be equal-and-opposite
  (see ADR-0008 follow-up), FX gain/loss and missing/corrected historical rates need explicit handling.
- **Follow-ups:** define missing-rate behavior (carry-forward + flag), rate-correction re-computation, and
  realized/unrealized FX treatment; property-test conversions across currencies.
