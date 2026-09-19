# ADR 0011: First-class investments + consolidated allocation view

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0001, ADR-0006, ADR-0010, ARCHITECTURE.md §2, §4
- **Note:** cost-basis method to be pinned in a follow-up ADR (see below).

## Context

Investment tracking is Monarch's most-complained feature (no allocation view, no dividend category, can't see
individual investment transactions). SimpleFIN provides no holdings data at all, so this must be modeled
ourselves and be fully manual (ADR-0010). The owner requested a single cross-account holdings/% allocation view.

## Decision

We will model investments first-class: **`securities`** (with `security_type`), **`holdings`** (position per
account), **`security_prices`** (manual + optional fetch), and **`investment_transactions`**
(buy/sell/dividend/interest/fee). An investment account's balance is derived as Σ(holding market values) in
base currency. A **consolidated allocation view** aggregates each security across all accounts into one row
with **% of portfolio**, groupable by security/type/account/currency. Dividends/interest flow into income
reporting.

## Consequences

- **Positive:** closes Monarch's biggest gap; portfolio-wide allocation; dividends visible.
- **Negative / costs:** cost-basis method (FIFO vs average) is unspecified and affects gain/loss; same security
  in different currencies and stale prices complicate the allocation view; derived balance may disagree with a
  provider-reported balance if a connected investment account also syncs a balance.
- **Follow-ups:** ADR for cost-basis method; define price-staleness display + which balance wins (derived vs
  provider) per account.
