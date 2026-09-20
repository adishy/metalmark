# Session 02 — Implementation (M1a build)

Compact log of who built what, for future agents. Newest tasks appended at the bottom.
Goal: a first version running locally (container-first), manual ledger proven under
unit + integration/e2e + visual-regression tests; then pause for SimpleFIN.

- **Orchestrator:** Claude Code (Opus 4.8). Delegates to subagents; owns the correctness-critical
  backend core (FX + ledger) per PLAN's "L+R+SYNC tight triangle" caveat.
- **Stack running:** docker compose (`db` pg16, `api` FastAPI, `worker`, `web` — see docker-compose.yml).
  Tests run in the `api` image against a throwaway `kestrel_test` DB on the `db` service
  (`KESTREL_TEST_PG_HOST=db`); testcontainers is the fallback path.

## Implementors

| # | Who | Scope | Result |
|---|-----|-------|--------|
| P0 | Opus 4.8 (orchestrator) | Phase 0 foundations: monorepo scaffold, docker-compose+Caddyfile+.env, FastAPI skeleton, SQLAlchemy 2.0 models (identity, ledger, fx), Alembic baseline migration, **app role + RLS tenant isolation (ADR-0014)**, argon2 auth + server-side sessions + CSRF + invite-only signup, Fernet secret helper, money/allocate/convert primitives (ADR-0005), WS-T harness (testcontainers/external-DB), money property tests + tenant-isolation integration test | ✅ green: 9 tests pass; auth flow (login/me/invite/signup) verified over HTTP in containers |
| T | Opus 4.8 (subagent): test & CI | Frontend unit tests (vitest + @testing-library: `format` money/date + Login render, 10 tests); Playwright e2e (`frontend/e2e/`: happy-path login→account→txn→reports, review approve + keyboard-swipe; unique-data/relative asserts, no reset needed); Linux visual baseline (login page, `*-chromium-linux.png` via playwright:v1.63.0-noble); `.github/workflows/ci.yml` (backend uv+pytest[+advisory ruff], frontend npm ci/typecheck/vitest/build, schemathesis+401 contract check, compose e2e w/ report artifact). Added `allowedHosts:["web"]` to vite dev server for container e2e. No app-code/testid changes. | ✅ green: vitest 10, e2e 4, backend 16, typecheck clean |

## Conventions established (P0)
- **Tenant isolation is one layer, not per-handler:** app connects as non-superuser `kestrel_app`;
  every household-scoped table has an RLS policy on `app.household_id` GUC (fail-closed when unset).
  `deps.get_context` sets the GUC per request after resolving identity; the worker will set it per job.
  Child tables (splits, tags) scoped via EXISTS against their RLS-protected parent.
- **Money = Decimal/NUMERIC(19,4); FX = NUMERIC(19,8)**; never float. Use `app.core.money` helpers.
- **Migrations:** baseline `0001` builds schema from ORM metadata + role + policies; later workstreams
  add incremental migrations (linearized, rebase on head — no Alembic multi-head).
- **Identity tables** (users/households/members/invites/sessions) are NOT household-RLS-scoped
  (they bootstrap the session); protected at the app layer via `unscoped_session`.

## Known follow-ups
- Email validation rejects reserved TLDs (`.test`/`.lan`); a LAN app may want local emails — revisit.
- Worker is a heartbeat stub (sync is Phase 2, after manual is proven).
