# Kestrel — Multi-Agent Execution Plan

How to build this with several agents working in parallel without stepping on each other.

## Coordination model

- **The seam is the contract.** `docs/ARCHITECTURE.md` (schema) + `contracts/openapi.yaml` (API) are the
  source of truth. Frontend agents build against a **mock server** (Prism/MSW) generated from the OpenAPI
  spec; backend agents build against the same spec. Neither waits on the other's implementation.
- **One agent owns each workstream.** Cross-workstream changes to the schema or OpenAPI spec go through a
  short "contract PR" that updates ARCHITECTURE.md first.
- **Definition of done per workstream:** code + migrations + unit tests + OpenAPI updated + a short
  README section + green CI. Every backend endpoint ships with at least one integration test.
- **Test data:** SimpleFIN Bridge publishes a **demo/sandbox setup token** — WS-A wires it into a seed
  script so every agent has realistic accounts/transactions locally. But the demo token is benign: WS-A also
  builds **adversarial synthetic provider fixtures** (id-instability, disappearing pendings, reconnect-remap,
  transfer pairs, duplicate re-import) — the acceptance bars are meaningless without them.
- **Contract testing is enforced, not assumed.** The "spec is the seam" strategy only works if the backend
  can't silently drift from the OpenAPI spec. CI validates live API responses against the spec (e.g.
  schemathesis); a drift fails the build before it lies to the frontend mocks.
- **Migrations are linearized.** "P0 freezes all migrations" is *not* the model (it would freeze the schema
  before A/B/C discover write-path gaps). Instead: P0 lands the **core identity/account/transaction schema**;
  each workstream adds its own migrations; a single **migration integrator** (or a strict rebase-on-head rule)
  prevents Alembic multi-head merge hell.

## Phase 0 — Foundations (BLOCKING; one agent, ~first sprint)

Everything else depends on this. Do not parallelize until it lands. **Trimmed to the true seams** — it is the
single-threaded critical path, so it holds only what parallel work genuinely needs, not every table.

- Monorepo scaffold, `docker-compose` (`db`, `api`, `worker`, `web`), Caddyfile, `.env.example`.
- Backend skeleton: FastAPI app factory, SQLAlchemy 2.0 base, Alembic, settings, `/healthz`, structured logging.
- **Core schema only** (not "all migrations"): identity/tenancy + accounts + **transactions with
  `field_sources` provenance + `transfer_group_id`**. Rules/holdings/import/budget tables land with their own
  workstreams under the migration-linearization rule. **The write-path contract (provenance precedence +
  transfer link) is co-designed by A/B/C leads and frozen here** — it is the one thing that must be right
  before the core schema locks, and everything correctness-critical downstream depends on it.
- **Auth + tenancy**: users, households, members, invites, **server-side sessions**; argon2id; httpOnly cookie
  + CSRF; invite-only signup; the **mandatory scoping layer / RLS** (tenant isolation lives here, not in each
  handler); `require_household` dependency.
- Encryption helper (`KESTREL_SECRET_KEY` from docker secret) for connection URLs.
- **`contracts/openapi.yaml` v0** covering auth + resource stubs **and the shared transaction filter/query +
  `/reports` query contract** (consumed by E/F/G/H — pin it here so those four don't thrash when B evolves).
  Generated TS client + MSW handlers + schemathesis contract check in CI.
- Frontend skeleton: Vite + TS + Tailwind + Radix/shadcn, TanStack Query, router, auth screens, app shell,
  PWA manifest/service worker (shell-only cache, `no-store` on data), dark mode.
- CI: lint (ruff/eslint), format, type-check (mypy/tsc), pytest + vitest, build, contract check.
- **Security review is a Phase-0 gate, not a Sprint-3 task** — session model, encryption, CSRF, and tenant
  isolation are all decided here.

**Exit criteria:** a user can be invited, sign up, log in, see an empty authenticated app shell; the write-path
contract is documented + migrated; tenant isolation has a test proving household B can't read household A;
CI (incl. contract check) green; mock API server runs.

## Parallel workstreams (after Phase 0)

Each is independently assignable. Dependencies noted.

**Phase column** encodes the build order: **1 = manual ledger (prove correctness), 2 = automation layered on
top, 3 = polish/ops.** Sync can only write what the manual model already supports and proves correct.

| WS | Title | Phase | Depends | Core deliverables |
|---|---|---|---|---|
| **0** | Test harness & CI (cross-cutting, built first) | 1→3 | P0 | `testcontainers` Postgres; **mock SimpleFIN server**; golden/adversarial fixtures (incl. multi-currency); property-test setup; Playwright scaffold; coverage + schemathesis gates; perf smoke (~50k txns). Red-green is the default workflow. |
| **L** | Ledger core (BE) — the canonical manual model | 1 | P0 | Accounts + transactions CRUD; **`field_sources` provenance**; splits (+rounding); tags; categories/groups; **manual transfers**; **multi-currency amounts + `fx_rates` + dated conversion service + daily rate fetch + manual rate entry**; the shared **filter/query + `/reports` contract**; review_status. |
| **INV** | Investments (BE) | 1 | L | Securities, holdings, `security_prices` (manual + optional fetch), **investment_transactions** (buy/sell/dividend/interest/fee); derived investment-account balances; **consolidated allocation API** (aggregate a security across accounts, % of portfolio in base currency). |
| **R** | Rules engine (BE) | 1 | L | Conditions/actions evaluator; provenance-aware apply hook; **idempotent** "apply to existing"; auto-split; priority order; fixtures. |
| **IMP** | Import & export (BE) | 1 (CSV) → 2 (OFX) | L, INV | CSV import (mapping + `import_hash` dedupe + preview + commit); OFX/QFX via **defused XML** (P2); **full data export**. |
| **SYNC** | Aggregation & sync + observability (BE) | **2** | L, INV, 0 | `AggregatorProvider` + `SimpleFinProvider`; claim + **reconnect remap**; worker + `sync_jobs` (SKIP LOCKED + reaper + 1/conn); sync algorithm (provenance-aware, low-water mark, pending reconcile, **transfer auto-match**) as "just another writer"; `sync_runs`/`sync_run_events`; errlist→status + **failure notifications**. |
| **UA** | Accounts UI (FE) | 1 (manual) → 2 (connect) | L / SYNC | Manual account CRUD + net-worth header (P1); connect-via-token, status badges, reconnect, "Sync now" (P2). |
| **UT** | Transactions UI + swipe review (FE) | 1 | L | List (filter/search, **virtualized**); detail/edit sheet; splits; tags; **swipe deck** + desktop keyboard review; **optimistic updates**; PWA shell. |
| **UINV** | Investments UI (FE) | 1 | INV | Holdings/securities/prices/investment-txn entry; **consolidated holdings & % allocation view** (group by security/type/account/currency). |
| **UR** | Reporting & dashboards (FE) | 1 → 2 | L, INV | ECharts: net worth over time; **cash-flow Sankey**; category donut + drill-down; income/expense trend; investment reporting; shared filter model. |
| **UH** | Household, sharing, rules UI, settings, **admin sync dashboard** (FE) | 1 → 2 | L, R, SYNC | Invite/household mgmt; ownership ("Shared Views" filters); category manager; **rule builder**; currency settings; **admin sync-observability dashboard** (P2). |
| **OPS** | Ops, security, perf, docs (cross-cutting) | rolling (security gate in P0) | rolling | docker-compose hardening; Caddy + Tailscale; nightly **encrypted** `pg_dump` (key separate) + restore drill; failure alerting; **precomputed report rollups**; security review at P0 + each milestone; user + admin docs. |

### Coupling caveats (read before assigning)
- **L + R + SYNC are a tight triangle**, not independent lanes — they all write the same transaction columns
  and share the provenance + auto-split contract (frozen in P0). SYNC is Phase 2, so L+R settle the contract
  against *manual* writes first; SYNC then conforms to it.
- **Transfers span L (model/manual link), SYNC (auto-match), UR (reporting exclusion)** — give transfer
  semantics one explicit owner.
- **The filter/query + `/reports` contract is consumed by UT/UR/UH** — pinned in P0 so they don't thrash.
- **Multi-currency touches L, INV, UR, OPS** — the dated-conversion service lives in L and everyone calls it;
  no one converts ad hoc.

## Milestones / demos (manual-first)

1. **M1 "Correct manual ledger" (Phase 1):** hand-enter multi-currency accounts, transactions, holdings +
   investment txns; split/categorize/tag/own; link transfers; rules auto-categorize; swipe-review; see net
   worth, Sankey, category drill-down, and the **consolidated allocation view** — all reconciling, all under a
   green test suite. **No sync yet, and it's already a usable app.**
2. **M2 "It syncs, safely" (Phase 2):** connect SimpleFIN; transactions flow in as provider-writes that never
   clobber human/rule edits; CSV/OFX import; **reconnect keeps all history**; the admin sync dashboard shows
   per-run logs/counts/timings. Prove sync output is indistinguishable from the manual path.
3. **M3 "Household-ready" (Phase 3):** performance rollups + budgets met on 50k txns; PWA polish; encrypted
   backups + a passed restore drill; security review; invite the household; deployed behind Tailscale.

## Per-workstream acceptance bars (examples)

- **L:** money & FX math use `Decimal` end to end (property tests over multiple currencies); a EUR txn in a EUR
  account rolls up to the correct base-currency net worth using that date's rate; split children sum to parent
  (percentage rounding lands on the cent); provenance precedence enforced; filter API paginates deterministically.
- **INV:** the consolidated allocation view sums the same security across accounts into one row and its
  `%` allocations total 100%; investment-account balance equals Σ(holding market values) in base currency;
  a dividend appears in income reporting.
- **R:** rules apply in priority order; auto-split balanced; "apply to existing" is idempotent and never
  overwrites a `user` field.
- **SYNC:** re-running a sync produces zero duplicates; a pending txn that posts (same *or* new id, or after
  >3 days) reconciles in place; a **human-set** category survives sync but an improved **rule** re-applies over
  a provider value; a **reconnect (remove + re-add connection) loses no transactions or history**; `con.auth`
  flips status to `auth_error` and fires a notification; every run is visible in the admin dashboard.
- **Manual parity:** for every capability sync/import provides, an equivalent manual UI action exists and is tested.
- **Tenancy (P0):** an automated test proves household B cannot read or mutate household A's data.
- **Transfers:** a checking→savings pair links and is excluded from cash-flow/Sankey while still appearing in the list.
- **UT:** swipe works on iOS Safari PWA and Android Chrome; keyboard review works on desktop; categorize feels
  instant (optimistic).
- **UR:** Sankey and donut reconcile to the transaction-list totals for the same filter **with transfers
  excluded**, all in base currency.
- **OPS/perf:** transaction list + reports meet latency budgets on the 50k-txn seed; a from-scratch
  `docker-compose up` yields a working app reachable only over Tailscale; a restore-from-backup drill succeeds.

## Open questions to revisit later (not blocking)

- Budgeting (Monarch-style category budgets + rollover) — deferred to v1.1 (schema stub present).
- Goals / recurring detection / cash-flow forecasting — v1.2.
- Second aggregator (Plaid adapter) — only if SimpleFIN coverage gaps bite; interface already supports it.
- **Truly private accounts** (member-hidden, not just view-filtered) — v1 ownership drives views, not access
  (confirmed acceptable). Would need a real access-control model to add later.
- Attachments/receipts, bulk-edit, manual reconciliation screen, automatic security-price fetch — deferrable.

_In scope for v1 (moved up from earlier deferrals): **first-class multi-currency**, **first-class investments
+ consolidated allocation view**, **admin sync observability**, **manual parity**, **manual-first build**._
