# ADR 0014: Tenant isolation via one mandatory scoping layer / RLS

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0012, ADR-0013, ARCHITECTURE.md §5

## Context

All data is household-scoped. Enforcing `household_id` filters by hand on every endpoint is the classic source
of cross-tenant (IDOR) data leaks — one missed `.filter()` exposes another household. The model is
multi-tenant even with a single household today.

## Decision

Tenant scoping is enforced in **one mandatory place**, not per handler: **Postgres Row-Level Security** keyed
on a per-session GUC, or a single mandatory query-scoping layer that every query passes through. A Phase-0
test proves household B cannot read or mutate household A's data.

## Consequences

- **Positive:** isolation doesn't depend on per-endpoint discipline; defense in depth; testable once, centrally.
- **Negative / costs:** RLS/GUC plumbing adds setup complexity; every DB session must set the tenant context.
- **Follow-ups:** the isolation test is a P0 exit criterion; security review verifies no bypass path exists.
