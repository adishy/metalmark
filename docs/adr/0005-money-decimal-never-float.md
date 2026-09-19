# ADR 0005: Money as NUMERIC/Decimal; never float

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0006, ADR-0008, ARCHITECTURE.md §2, §7

## Context

This is a financial ledger; rounding drift is a correctness defect. SimpleFIN sends amounts as decimal
strings. Splits by percentage and multi-currency conversion both introduce rounding.

## Decision

We will store all monetary values as Postgres **`NUMERIC(19,4)`** and handle them as Python **`Decimal`** end
to end. **Floats are never used for money.** Percentage splits and FX conversions round to the cent with a
single documented rounding policy (remainder assigned to the largest child; banker's vs half-up specified in
code and tested).

## Consequences

- **Positive:** exact arithmetic; reproducible totals; property-testable invariants (splits sum to parent).
- **Negative / costs:** must be disciplined at every boundary (JSON, ORM, chart data) to avoid float coercion.
- **Follow-ups:** property tests (Hypothesis/fast-check) over amounts and currencies are a WS-T/L acceptance bar.
