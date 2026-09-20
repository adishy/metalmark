# ADR 0025: RLS implementation — per-transaction GUC + fail-closed + child policies

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0014 (tenant isolation), ARCHITECTURE.md §5, backend/app/db.py,
  backend/alembic/versions/0001_initial_schema.py

## Context

ADR-0014 chose "one mandatory scoping layer / RLS" for tenant isolation. This ADR records the concrete
mechanism decided during implementation so it isn't re-litigated.

## Decision

- The app connects as a **non-superuser role** (`kestrel_app`) that does **not** have `BYPASSRLS`. Migrations
  run as the owner/superuser (which bypasses RLS — acceptable, migrations are trusted).
- Every household-scoped table has an RLS policy keyed on a transaction-local GUC:
  `USING (household_id = NULLIF(current_setting('app.household_id', true), '')::uuid)` (+ same `WITH CHECK`).
  The GUC is set per request/job via `SELECT set_config('app.household_id', :id, true)` (transaction-local,
  parameterizable — `SET LOCAL` can't be parameterized over asyncpg).
- **Fail-closed:** when the GUC is unset/empty, `NULLIF(...)::uuid` is NULL and `household_id = NULL` matches
  no rows. An unscoped query therefore returns nothing (proven by a test). Identity/auth is resolved first
  (identity tables carry no household RLS) and the GUC is set before any financial query in the handler.
- **Child tables** (`transaction_splits`, `transaction_tags`) have no `household_id`; their policy is an
  `EXISTS (SELECT 1 FROM transactions t WHERE t.id = <fk>)` against the RLS-protected parent, so the parent's
  policy naturally scopes them.
- Identity tables (`users`, `households`, `household_members`, `invites`, `sessions`) and reference data
  (`fx_rates`) are **not** under household RLS; they are accessed via `unscoped_session` and protected at the
  application layer.

## Consequences

- **Positive:** isolation is enforced in one place for both API and worker; a missed handler filter cannot
  leak across households; fail-closed default. Integration test proves household B cannot read/insert into A.
- **Negative / costs:** every scoped path must open a transaction and set the GUC (done in `scoped_session` /
  `get_context`); a raw session that forgets it simply sees no rows (safe, but can surprise). Multi-household
  FX-rate propagation on rate edits is currently scoped to the acting household (noted follow-up).
