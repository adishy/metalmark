# MetalMark — Multi-Agent Execution Plan

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
- **Core schema only** (not "all migrations"): identity/tenancy + accounts (single-currency, with
  `external_key` unique) + **transactions with `field_sources` provenance + `transfer_group_id`** +
  `fx_rates`. Rules/holdings/import/budget tables land with their own workstreams under the
  migration-linearization rule. **Two contracts are co-designed and frozen here:**
  1. the **write-path contract** — provenance precedence + the **manual-origin boundary** (sync merges only by
     `external_id`, ADR-0019) + transfer link (co-owned by L/R/SYNC leads);
  2. the **currency model** — single-currency accounts, `base_amount` as an invalidatable cache over
     authoritative `fx_rates`, high-precision rates, dated-lookup + "no rate" rule, immutable base_currency
     (ADR-0017). This sits directly in the frozen transactions/snapshots tables — getting it wrong forces a
     migration through everything.
- **SimpleFIN sandbox spike (before the freeze):** hit the real demo token, capture actual payloads, and
  validate the DTO→ledger mapping + provenance + reconnect-remap assumptions against **real** data. Derive the
  test fixtures from these captures (ADR-0022). Manual-first proves the model is self-consistent, not that it's
  SimpleFIN-compatible — this spike closes that gap before the schema locks.
- **Auth + tenancy**: users, households, members, **server-side sessions**; argon2id; httpOnly cookie
  + CSRF; **open signup** (first signer creates the household, later ones join the oldest; a concurrent
  first-signup race is serialized by a Postgres advisory lock; `METALMARK_OPEN_SIGNUP=false` closes it —
  ADR-0027, no invites); the **mandatory scoping layer / RLS** (tenant isolation lives here, not in each
  handler); `require_household` dependency.
- Encryption helper (`METALMARK_SECRET_KEY` from docker secret) for connection URLs.
- **`contracts/openapi.yaml` v0** covering auth + resource stubs **and the shared transaction filter/query +
  `/reports` query contract** (consumed by UT/UR/UH — pin it here so they don't thrash when L evolves).
  Generated TS client + MSW handlers + schemathesis contract check in CI.
- Frontend skeleton: Vite + TS + Tailwind + Radix/shadcn, TanStack Query, router, auth screens, app shell,
  PWA manifest/service worker (shell-only cache, `no-store` on data), dark mode.
- CI: lint (ruff/eslint), format, type-check (mypy/tsc), pytest + vitest, build, contract check.
- **Security review is a Phase-0 gate, not a Sprint-3 task** — session model, encryption, CSRF, and tenant
  isolation are all decided here.

**Exit criteria:** a user can sign up (the first signer creates the household), log in, see an empty
authenticated app shell; the write-path **and currency** contracts are documented (ADR-0017/0019) + migrated;
the SimpleFIN spike has validated the mapping against real payloads; tenant isolation has a test proving
household B can't read household A (API **and** worker); CI (incl. contract check) green; mock API server runs.

## Parallel workstreams (after Phase 0)

Each is independently assignable. Dependencies noted.

**Phase column** encodes the build order: **1 = manual ledger (prove correctness), 2 = automation layered on
top, 3 = polish/ops.** Sync can only write what the manual model already supports and proves correct.

Phases split: **1a = core single-currency ledger correctness** (prove reconciliation), **1b = investments +
allocation + richer reporting**, **2 = automation (sync/OFX)**, **3 = polish/ops/scale**.

| WS | Title | Phase | Depends | Core deliverables |
|---|---|---|---|---|
| **T** | Test harness & CI (cross-cutting, built first) | 1a→3 | P0 | `testcontainers` Postgres; **mock SimpleFIN server** (fixtures derived from real payloads, ADR-0022); golden/adversarial fixtures (incl. multi-currency, cross-currency transfer, known-allocation portfolio); property-test setup; Playwright scaffold; coverage + schemathesis gates; perf smoke (~50k txns). Red-green default. |
| **FX** | Currency & FX conversion service (BE) | 1a | P0 | The one **dated-conversion service** everyone calls (no ad-hoc conversion); `fx_rates` model + high-precision rates + triangulation; dated lookup + "no rate" flag; daily fetch + manual entry; **cache/rollup invalidation** on rate change. Own module (highest correctness risk; kept out of the L bottleneck). |
| **L** | Ledger core (BE) — the canonical manual model | 1a | P0, FX | Accounts + transactions CRUD; **household `owners`** (attribution labels incl. Shared — a label, not a user, ADR-0026) + the one effective-owner helper + owner filters; **`field_sources` provenance + manual-origin boundary**; splits (+base allocation/rounding); tags; categories/groups; **manual transfers** (incl. cross-currency w/ `fx_cost_base`); `base_amount` cache; the shared **filter/query + `/reports` contract**; review_status. |
| **R** | Rules engine (BE) | 1a (basic) → 2 (auto-split) | L | Conditions/actions evaluator; provenance-aware apply hook; **idempotent** "apply to existing"; priority order; fixtures. **Auto-split deferred to Phase 2**; basic categorization rules in 1a. |
| **INV** | Investments (BE) | **1b** | L, FX | Securities, holdings (derived market value), `security_prices` (manual + optional fetch, staleness flag), **investment_transactions**; **average-cost basis** (ADR-0020); `balance_source` + "unaccounted cash" plug (ADR-0021); **consolidated allocation API** reconciling to net-worth investment total. |
| **IMP** | Import & export (BE) | 1a (CSV) → 2 (OFX) | L, INV | CSV import (mapping + `import_hash` w/ ordinal + "possible duplicate" review + commit); OFX/QFX via **defused XML** (P2); **full data export**. |
| **SYNC** | Aggregation & sync + observability (BE) | **2** | L, INV, T | `AggregatorProvider` + `SimpleFinProvider`; claim + **reconnect remap**; worker + `sync_jobs` (SKIP LOCKED + reaper + 1/conn; worker sets `app.household_id` for RLS); sync algorithm (provenance-aware, **owner resolved to Shared explicitly on insert and never overwriting a `user` owner**, low-water mark, pending reconcile, transfer auto-match) as "just another writer"; `sync_runs`/`sync_run_events`; errlist→status + **failure notifications**. A thin vertical slice (claim→fetch→insert→remap) is spiked in late Phase 1 (ADR-0022). |
| **UA** | Accounts UI (FE) | 1a (manual) → 2 (connect) | L / SYNC | Manual account CRUD + net-worth header (P1a); connect-via-token, status badges, reconnect, "Sync now" (P2). |
| **UT** | Transactions UI + swipe review (FE) | 1a | L | List (filter/search, **virtualized**); detail/edit sheet; splits; tags; **swipe deck** + desktop keyboard review; **optimistic updates**; PWA shell. |
| **UINV** | Investments UI (FE) | **1b** | INV | Holdings/securities/prices/investment-txn entry; **consolidated holdings & % allocation view** (group by security/type/account/currency; staleness flags). |
| **UR** | Reporting & dashboards (FE) | 1a (net worth + category + revaluation) → 1b (Sankey, investment) | L, FX, INV | ECharts: net worth over time **+ currency-revaluation line**; category donut + drill-down; income/expense trend (**1a**); **cash-flow Sankey** + investment reporting (**1b**); shared filter model. |
| **UH** | Household, owners, rules UI, settings, **admin sync dashboard** (FE) | 1a → 2 | L, R, SYNC | Household + member + settings mgmt (signup is self-serve, ADR-0027); **owner management** (add/rename/reassign-on-delete) + "Shared Views" owner filters; category manager; **rule builder**; currency settings; **admin sync-observability dashboard** (P2). |
| **OPS** | Ops, security, perf, docs (cross-cutting) | rolling (security gate in P0) | rolling | docker-compose hardening; Caddy + Tailscale; nightly **encrypted** `pg_dump` (key separate) + restore drill; failure alerting; **precomputed report rollups + FX-change invalidation**; security review at P0 + each milestone; user + admin docs. |

### Coupling caveats (read before assigning)
- **L + R + SYNC are a tight triangle**, not independent lanes — they all write the same transaction columns
  and share the provenance contract (frozen in P0). SYNC is Phase 2, so L+R settle the contract against
  *manual* writes first; SYNC then conforms to it (the P0 spike de-risks that it *can*).
- **FX is its own module (WS-FX), not a sub-bullet of L** — it's the highest-correctness-risk unit and every
  workstream depends on it, so it doesn't live inside the critical-path bottleneck. Nobody converts ad hoc.
- **Transfers span L (model/manual link + cross-currency `fx_cost_base`), SYNC (auto-match), UR (exclusion +
  revaluation)** — give transfer semantics one explicit owner.
- **The filter/query + `/reports` contract is consumed by UT/UR/UH** — pinned in P0 so they don't thrash.
- **The two hardest new areas (multi-currency + investments) intersect in one deliverable** — the
  base-currency consolidated allocation view — which is why it's in **1b**, after 1a proves currency in the
  simpler ledger context.

## Milestones / demos (manual-first)

1. **M1a "Correct single-currency-account ledger" (Phase 1a):** sign up (open, no invites — the first signer
   creates the household, ADR-0027); hand-enter accounts (incl. a foreign-currency one), transactions, splits,
   transfers (incl. cross-currency); categorize/tag/**assign owners** (household labels incl. Shared,
   ADR-0026); basic rules; swipe-review; see net worth over time (with the **currency-revaluation line**),
   category donut + drill-down, income/expense trend — **all reconciling** under a green test suite, CSV import
   working. No sync, already usable.
2. **M1b "Investments & the full picture" (Phase 1b):** securities/holdings/prices/investment-txns by hand;
   dividends in income; the **consolidated cross-account allocation view** reconciling to the net-worth
   investment total; the **cash-flow Sankey**.
3. **M2 "It syncs, safely" (Phase 2):** connect SimpleFIN; transactions flow in as provider-writes that never
   clobber human/rule edits or manual rows; OFX import; auto-split rules; **reconnect keeps all history**; the
   admin sync dashboard shows per-run logs/counts/timings. Sync output is indistinguishable from manual.
4. **M3 "Household-ready" (Phase 3):** perf budgets met on 50k txns; PWA polish; encrypted backups + a passed
   restore drill; security review; the household signs itself up (open signup — ADR-0027); deployed behind
   Tailscale.

## Per-workstream acceptance bars (examples)

- **FX:** conversion uses the latest `rate_date ≤ target`; a missing rate yields a "no rate" flag, never 0;
  correcting a rate invalidates every dependent `base_amount` and rollup (test proves stale reports refresh);
  JPY/other low-value pairs convert without material precision loss (property test).
- **L:** money & FX math use `Decimal` end to end (property tests over currencies); a EUR txn in a EUR account
  rolls up to correct base-currency net worth at that date's rate; split children sum to parent in **both**
  native and base (no convert-then-round drift); provenance precedence + manual-origin boundary enforced;
  owner resolution is total (`split → transaction → account → Shared`, one helper) and an owner delete with
  references fails unless given a reassign target; report owner filters keep their documented per-endpoint
  meaning (account-scoped for net-worth, row-scoped for cash-flow/spending — ADR-0026);
  filter API paginates deterministically.
- **INV:** the consolidated allocation view sums the same security across accounts into one row; its market
  values **reconcile to the net-worth investment total** (a known-fixture portfolio → known allocations golden
  test — not a trivial "%s sum to 100"); a stated-balance synced account + hand-added holdings reconcile via
  the plug; a dividend appears in income reporting.
- **R:** rules apply in priority order; auto-split balanced (Phase 2); "apply to existing" is idempotent and
  never overwrites a `user` field.
- **SYNC:** re-running a sync produces zero duplicates; a pending txn that posts (same *or* new id, or after
  >3 days) reconciles in place; a **human-set** category survives sync but an improved **rule** re-applies over
  a provider value; a **reconnect (remove + re-add connection) loses no transactions or history**; `con.auth`
  flips status to `auth_error` and fires a notification; every run is visible in the admin dashboard.
- **Manual parity:** for every capability sync/import provides, an equivalent manual UI action exists and is tested.
- **Tenancy (P0):** an automated test proves household B cannot read or mutate household A's data.
- **Transfers:** a same-currency checking→savings pair links and is excluded from cash-flow/Sankey while still
  in the list; a **cross-currency** transfer (−100 EUR / +108 USD) auto-matches on base_amount, and its FX
  residual is surfaced as `fx_cost_base`, not hidden.
- **UT:** swipe works on iOS Safari PWA and Android Chrome; keyboard review works on desktop; categorize feels
  instant (optimistic).
- **UR:** donut/trend reconcile to transaction-list totals for the same filter **with transfers excluded**, in
  base currency; net-worth Δ over a period = cash-flow + the shown currency-revaluation line (multi-currency
  reconciliation includes that line; single-currency reconciles exactly).
- **OPS/perf:** transaction list + reports meet latency budgets on the 50k-txn seed; a from-scratch
  `docker-compose up` yields a working app reachable only over Tailscale; a restore-from-backup drill succeeds.

## Open questions to revisit later (not blocking)

- Budgeting (the familiar category budgets + rollover of the hosted apps) — deferred to v1.1 (schema stub
  present).
- Goals / recurring detection / cash-flow forecasting — v1.2.
- Second aggregator (Plaid adapter) — only if SimpleFIN coverage gaps bite; interface already supports it.
- **Truly private accounts** (member-hidden, not just view-filtered) — v1 ownership drives views, not access
  (confirmed acceptable). Would need a real access-control model to add later.
- **Fractional ownership** (`share_pct` — "half the mortgage is mine") — deferred; without it the account-scoped
  net-worth filter and the row-scoped spending/cash-flow filters are **not additive**, which is documented and
  surfaced in the UI rather than hidden (ADR-0026).
- Attachments/receipts, bulk-edit, manual reconciliation screen, automatic security-price fetch — deferrable.

_In scope for v1: **multi-currency** (single-currency accounts + dated FX conversion + a currency-revaluation
line; ADR-0017), **first-class investments + consolidated allocation view**, **admin sync observability**,
**manual parity**, **manual-first build**. True multi-currency wallet accounts and full FX P&L are deferred._
