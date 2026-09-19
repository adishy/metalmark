# ADR 0017: Currency & FX model — single-currency accounts, base_amount as cache, revaluation line

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Supersedes:** ADR-0006
- **Related:** ADR-0005, ADR-0008/0018 (transfers), ADR-0021, ARCHITECTURE.md §2, §4, §8

## Context

ADR-0006 claimed "per-account AND per-transaction currency" while the balance/snapshot model is single-currency
per account — a self-contradiction (you can't sum EUR and USD transactions into one native account balance).
A review forced a decision. The household holds **separate single-currency accounts** (a EUR account, a USD
account), not wallet accounts that hold several currencies at once. Reporting must convert to a base currency
while keeping history stable, handle missing/corrected rates, and explain FX-driven net-worth moves.

## Decision

1. **Accounts are single-currency.** `transactions.currency ≡ account.currency` (a foreign purchase posts in
   the account's currency; the bank already converted). True multi-currency wallet accounts are **out of scope
   for v1**. This resolves the balance contradiction.
2. **`fx_rates` is the source of truth; `transactions.base_amount` is a cache.** Rates stored at high precision
   (`NUMERIC(19,8)`), with a documented direction and triangulation in the one conversion service.
3. **Dated lookup:** convert at the latest `rate_date ≤ target` (target = the txn's household-local date, same
   basis as month bucketing). No rate available → value flagged **"no rate"**, never silently 0/unconverted.
4. **Invalidation:** a changed/corrected rate invalidates every dependent `base_amount` **and** precomputed
   rollup, keyed on `(currency, rate_date)`. **`base_currency` is immutable** in v1.
5. **FX revaluation:** v1 does **not** do position-level FX P&L. Net-worth change is decomposed into
   cash-flow + an explicit **"currency revaluation"** line, so multi-currency base-deltas are explained.

## Consequences

- **Positive:** a coherent, testable ledger invariant; correct multi-currency net worth; honest reporting;
  far less complexity than wallet accounts or full FX accounting.
- **Negative / costs:** no wallet accounts and no per-lot FX P&L in v1; the conversion service and its
  invalidation are correctness-critical (own module, WS-FX); triangulation rounding must be controlled.
- **Follow-ups:** cross-currency transfer handling (ADR-0018); property tests across currencies; a rate-
  correction → recompute test; "no rate" and stale-rate UI states.
