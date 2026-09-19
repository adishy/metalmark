# Session 01 — Architecture & multi-agent plan

- **Date:** 2026-09-19
- **Agent:** Claude Code (Opus 4.8)
- **Session:** https://claude.ai/code/session_01Ss1KG6Tk5cNWhHEDPzdnnV
- **Working dir:** `/Users/adishy/personal.data.adishy.com/tmp/monarch_clone`
- **Outcome:** `README.md`, `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `docs/adr/0000–0023`, this record.
  Commits: `670b745` (initial), `ac8d29c` (ADRs), `83b3c1b` (2nd-review revision).

> Reconstructed from the in-session conversation. User messages, the clarifying-question options/answers, and
> the review findings are reproduced faithfully; assistant narration is paraphrased.

---

## 1. Task (user, verbatim)

> the next task is to create a self-hostable version of monarch money. let's make the following high level
> architectural decisions:
> - api backend, containerized, lives on a local lab
> - web app, progressive web app optional, full featured, leverages web apis where needed
> - should match monarch money's core features:
>   - connect and auto-update all accounts, easy to see if account status has updated / synced — easy to add
>     and remove accounts … very important that we have a plaid-like solution for autosyncing latest account
>     state
>     - connect to banks (PNC, BofA), investment accounts (fidelity / schwab / betterment), and treasurydirect
>       — can use a solid, private and secure third party if you can identify any.
>   - a bullet proof transaction handler that lets you assign categories, owners etc.
>   - ability to SHARE accounts / have multiple owners.
>   - made for a few users, some with joined finances, so basic account creation etc.
>   - choose boring, solid off the shelf technologies (fastapi / postgres / react / vanillajs / nodejs etc.)
>   - replicate the "swipe left/right" transaction sorting interface
>   - replicate the reporting with sankey diagram / pie-charts etc. to see gains/loss over time and drill down
> do deep research into each part, break it down, come up with a plan executable by multiple agents, then have
> your advisor review the overall plan. ask clarifying q's as needed. when in doubt, do what monarch did.

Note: the advisor (Fable 5.1) was not enabled on the account (needs usage-credit billing), so review passes
were run via independent reviewer subagents as a stand-in.

---

## 2. Research (grounding the aggregation layer)

- **Monarch** uses three aggregators (Plaid + MX + Finicity), defaulting per-institution to the best link.
- **SimpleFIN Bridge** — self-hosting community standard: $15/yr, read-only, US+Canada, poll-based,
  privacy-friendly; **no investment-holdings object in the protocol**; TreasuryDirect coverage unreliable.
  Amounts are decimal strings; transaction `id` is unique per account; pending→posted is the same id updating.
- **Plaid** — best coverage incl. TreasuryDirect, free trial (10 prod Items) but needs a business application
  and receives your data (hosted third party).
- **Charting** — Apache ECharts covers pie/bar/line **and** Sankey with drill-down (Recharts has no Sankey).
- **Monarch pain points** (forums/reviews): (1) flaky connections & reconnect churn; (2) duplicate
  transactions (esp. two cards on one account / family sharing); (3) weak investment tracking (most-complained
  — no allocation, no dividend category, can't see investment transactions); (4) manual/cash workflow is
  second-class; (5) miscategorization + can't split recurring bills.

---

## 3. Clarifying decisions

**Round 1** (asked before designing):
| Question | Answer |
|---|---|
| Aggregator strategy (SimpleFIN-first / Plaid-first / both) | **"prolly simplefin only"** |
| Hard accounts: auto-sync strict vs manual fallback | **Auto where possible, manual/CSV/OFX fallback** |
| Lab network: LAN/VPN poll vs internet+webhooks | **LAN/VPN only, poll on schedule** |

**Additional requirements** (user, after first plan):
- Multi-currency as first-class, incl. live-rate calculations ("there are multi currency accounts").
- v1 view-filtering sharing model is OK.
- Dislikes about Monarch to fix: more sync control/observability (logs, counts, timings — admin/peer);
  everything automated must also be doable **manually** (esp. holdings); integration health may come and go but
  **transactions must stay consistent/recoverable** (re-adding a connection must not require restoring txns).
- Technical: **correctness first** (red-green unit/integration/e2e, test harness first-class); prioritize
  performance (snappy BE+FE); **manual-first, prove correctness, then automate**; research Monarch pain points.
- Feature request: a **consolidated view of all underlying assets across accounts with % allocation**.

**Round 2** (after 2nd review found a currency contradiction):
| Question | Answer |
|---|---|
| Currency model: separate single-currency accounts vs true multi-currency wallets | **Separate single-currency accounts** |
| FX gain/loss: revaluation line / full P&L / ignore | **Show a "currency revaluation" line** |

---

## 4. Design (as encoded in the docs)

- **Stack:** FastAPI + SQLAlchemy/Alembic + Postgres 16; React+TS+Vite PWA; TanStack Query; Tailwind/Radix;
  Apache ECharts; one worker (APScheduler cron + Postgres `sync_jobs`, no Redis); Caddy + Tailscale.
- **Aggregation:** SimpleFIN only behind a pluggable `AggregatorProvider`; poll-based; claim→access-URL
  (encrypted at rest); reconnect **remap** by `external_key`; provenance-aware ingest; pending reconcile.
- **Ledger:** money is `Decimal`/`NUMERIC`, never float; `field_sources` provenance (user > rule > provider)
  with a manual-origin boundary; transfers linked & excluded from cash-flow (cross-currency surfaces FX cost);
  ledger **decoupled from connections** (transactions survive reconnect).
- **Currency:** single-currency accounts; base currency (immutable); `fx_rates` authoritative + high
  precision; `base_amount` a cache invalidated on rate change; dated lookup with "no rate" flag; net-worth
  reconciliation includes a currency-revaluation line.
- **Investments:** securities/holdings/prices/investment-transactions; average-cost basis; balance_source
  (derived|stated) + plug; consolidated cross-account **allocation view** reconciling to net worth.
- **Security/ops:** invite-only, argon2id, server-side sessions; tenant isolation via RLS / one scoping layer
  (worker sets `app.household_id`); encrypted backups w/ key stored separately; admin sync observability.
- **Testing/perf:** WS-T harness (testcontainers, mock SimpleFIN from real payloads, property tests,
  Playwright, schemathesis, 50k perf smoke); keyset pagination, precomputed rollups, virtualized list.

## 5. Multi-agent plan

Phase 0 (blocking): scaffold + auth/tenancy + core schema + two frozen contracts (write-path provenance,
currency model) + SimpleFIN sandbox spike + OpenAPI/filter/report contract. Then workstreams **T, FX, L, R,
INV, IMP, SYNC, UA, UT, UINV, UR, UH, OPS** across phases **1a → 1b → 2 → 3**. Milestones M1a (single-currency
ledger correct) → M1b (investments + allocation + Sankey) → M2 (sync, safely) → M3 (household-ready).

---

## 6. Review pass 1 (reviewer subagent) — key findings & resolutions

1. **"Sticky edits" had no provenance model** → added `field_sources` (user>rule>provider). [ADR-0007]
2. **No transfer model → phantom income/expense in Sankey** → `transfer_groups` + auto-match + exclusion. [ADR-0008]
3. **Reconnect duplicates accounts/history** → remap by `external_key`; decoupled ledger. [ADR-0009]
4. **pending→posted assumed id stability** → `pending_reconcile`, match on (amount,~date,desc), low-water mark.
5. **Tenant isolation by hand-written filters (IDOR)** → RLS / one mandatory scoping layer. [ADR-0014]
6. **Phase 0 over-stuffed / "freeze all migrations" too early** → trimmed P0; migration linearization.
   Plus: money `Decimal`, encrypted backups + key separate, defused XML, schemathesis, session model decided.

## 7. Review pass 2 (reviewer subagent) — key findings & resolutions

P0-blockers:
1. **Currency granularity contradiction** (per-txn currency vs single-currency balance) → **single-currency
   accounts** decided. [ADR-0017, supersedes 0006]
2. **`base_amount` stored-vs-computed, missing/corrected-rate rules** → cache over authoritative `fx_rates`;
   dated lookup + "no rate"; invalidation on rate change; immutable base_currency. [ADR-0017]
3. **FX rate precision** (4dp wrong for JPY) → `NUMERIC(19,8)` + triangulation in one service.
4. **Provenance wrong for manual-origin rows** → manual-origin boundary; sync merges only by external_id. [ADR-0019]

Multi-currency landmines: net-worth vs cash-flow won't reconcile without FX revaluation → **revaluation line**
[ADR-0017]; cross-currency transfers break equal-magnitude match & hide FX cost → match on base_amount +
`fx_cost_base` [ADR-0018]; splits need currency/base_amount + exact base allocation.

Investments: cost-basis unsupportable as scalar → **average cost, single authority** [ADR-0020]; Σ(holdings)
vs stated balance → **balance_source + plug** [ADR-0021]; allocation currency/staleness → canonical rule +
reconcile-to-net-worth bar.

Sequencing: "sync is just another writer" doesn't de-risk sync-specific logic → **P0 SimpleFIN spike +
Phase-1 vertical slice** [ADR-0022]; Phase 1 ballooned → **split 1a/1b, FX its own module** [ADR-0023].

Doc bugs fixed: `external_key` unique, `balance` vs `current_balance`, worker RLS, `import_hash` occurrence
ordinal, WS-0/Phase-0 label collision (test harness → WS-T).

Affirmed good calls: ledger/connection decoupling, `field_sources`, RLS, sync engineering (low-water mark,
pending_reconcile, reaper, SKIP LOCKED), Decimal + property tests, docker-secret key, defused XML, contract
testing, manual-first for model/reporting.

---

## 8. ADR strategy

Nygard-format ADRs under `docs/adr/`: one decision per file, immutable once Accepted, superseded (not edited)
on change — recorded as we go. Genesis decisions backfilled as 0001–0016; 2nd-review decisions as 0017–0023
(0006 superseded by 0017). See `docs/adr/README.md`.

## 9. Open threads / next steps

- Start Phase 0 (scaffold + core schema + contracts + SimpleFIN spike), or run a 3rd review, or add a git remote.
- Deferred to later versions: budgets (v1.1), goals/recurring/forecast (v1.2), Plaid adapter, wallet accounts,
  full FX P&L, lot/FIFO cost basis, truly-private accounts.
