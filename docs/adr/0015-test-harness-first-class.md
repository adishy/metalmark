# ADR 0015: Test harness is a first-class workstream (red-green)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0005, ADR-0007, ADR-0010, ARCHITECTURE.md §7

## Context

The owner's #1 requirement is correctness, with red-green unit/integration/e2e tests that mock real data and a
testing harness built as a first-class citizen. Financial correctness bugs are silent and costly.

## Decision

Testing is **WS-0**, built before feature code, with red-green as the default workflow. It provides:
`testcontainers` Postgres for integration tests, a **mock SimpleFIN server** driven by golden + adversarial
fixtures (id-instability, disappearing pendings, reconnect-remap, transfer pairs, duplicate re-import,
multi-currency), property tests (Hypothesis/fast-check) for money/FX/splits, Playwright e2e over a seeded
stack, and CI gates (lint, types, coverage floor, schemathesis contract check, perf smoke on ~50k txns). A red
build blocks merge.

## Consequences

- **Positive:** correctness is enforced, not aspirational; refactors are safe; the contract-test gate keeps
  FE/BE honest.
- **Negative / costs:** upfront investment before visible features; fixtures must be maintained.
- **Follow-ups:** every workstream ships tests as part of its definition of done.
