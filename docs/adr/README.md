# Architecture Decision Records

We record every architecturally significant decision as an ADR (Michael Nygard format). This is the
authoritative log of *why* MetalMark is built the way it is — agents and humans read it before changing a
load-bearing choice.

## Rules

- **One decision per file**, named `NNNN-kebab-title.md` (zero-padded, monotonically increasing).
- **ADRs are immutable once `Accepted`.** You don't edit the decision later — you add a *new* ADR that
  **supersedes** it, and set the old one's status to `Superseded by ADR-XXXX`. This preserves the reasoning
  trail.
- **Statuses:** `Proposed` → `Accepted` → (`Superseded by ADR-XXXX` | `Deprecated`). Use `Proposed` for a
  decision under discussion; only `Accepted` decisions are binding.
- **When to write one:** anything that changes the data model, a cross-workstream contract, a technology
  choice, security posture, or a correctness invariant. If a code review would ask "why is it done this way?",
  it needs an ADR.
- **Keep it short** — context, the decision, and the consequences (including the costs). Link related ADRs and
  the relevant `ARCHITECTURE.md` section. Copy `0000-template.md` to start.
- **Process:** a new ADR lands in the same PR as the change it justifies (or ahead of it as `Proposed`).
  Changing an `Accepted` decision without a superseding ADR is a review-blocking error.

## A note on the old codename

ADRs ≤0025 (and everything under `agent_record/`) describe the app by its original working codename,
**Kestrel**. It was renamed to **MetalMark** — code, infra, env vars, cookie, database and docs — in one
behavior-neutral commit; these accepted records were deliberately left as written, per the immutability
rule above. When an older ADR says `KESTREL_SECRET_KEY`, read `METALMARK_SECRET_KEY`, and so on.

## Index

| ADR | Title | Status |
|---|---|---|
| [0000](0000-template.md) | Template | — |
| [0001](0001-simplefin-sole-aggregator.md) | SimpleFIN as the sole aggregation provider, behind a pluggable interface | Accepted |
| [0002](0002-poll-based-lan-only.md) | Poll-based sync over LAN/VPN; no webhooks, no public ingress | Accepted |
| [0003](0003-boring-stack.md) | Boring stack: FastAPI + Postgres + React/TS + ECharts | Accepted |
| [0004](0004-postgres-job-queue-no-redis.md) | Postgres-backed job queue; no Redis | Accepted |
| [0005](0005-money-decimal-never-float.md) | Money as NUMERIC/Decimal; never float | Accepted |
| [0006](0006-multi-currency-dated-fx.md) | Multi-currency is first-class, converted at dated FX rates | Superseded by 0017 |
| [0007](0007-field-provenance.md) | Field-level provenance governs sync-vs-human writes | Accepted |
| [0008](0008-transfers-linked-excluded.md) | Transfers are linked groups, excluded from cash-flow | Accepted |
| [0009](0009-ledger-decoupled-from-connections.md) | The ledger is decoupled from connections | Accepted |
| [0010](0010-manual-first-and-parity.md) | Manual-first build order and manual-parity principle | Accepted |
| [0011](0011-first-class-investments.md) | First-class investments + consolidated allocation view | Accepted |
| [0012](0012-sharing-view-filtering.md) | Household + view-filtering sharing (no per-account access control in v1) | Superseded by 0026 (ownership half) |
| [0013](0013-auth-sessions-invite-only.md) | Auth: server-side sessions, argon2id, invite-only | Superseded by 0027 (invite half) |
| [0014](0014-tenant-isolation-scoping-layer.md) | Tenant isolation via one mandatory scoping layer / RLS | Accepted |
| [0015](0015-test-harness-first-class.md) | Test harness is a first-class workstream (red-green) | Accepted |
| [0016](0016-admin-sync-observability.md) | Admin-grade sync observability | Accepted |
| [0017](0017-currency-single-account-model.md) | Currency & FX: single-currency accounts, base_amount cache, revaluation line | Accepted |
| [0018](0018-cross-currency-transfers.md) | Cross-currency transfers: match on base amount, surface FX cost | Accepted |
| [0019](0019-provenance-manual-origin-boundary.md) | Provenance: the manual-origin boundary for sync merges | Accepted |
| [0020](0020-investment-cost-basis-average.md) | Investment cost basis: average cost for v1; lots deferred | Accepted |
| [0021](0021-investment-balance-source.md) | Investment balance source: derived vs stated + reconciling plug | Accepted |
| [0022](0022-derisk-sync-p0-spike.md) | De-risk sync early: P0 SimpleFIN spike + Phase-1 vertical slice | Accepted |
| [0023](0023-phase1-split-1a-1b.md) | Split Phase 1 into 1a/1b; carve FX into its own module | Accepted |
| [0024](0024-uv-for-python-deps.md) | uv for Python dependency management | Accepted |
| [0025](0025-rls-implementation-mechanism.md) | RLS mechanism: per-transaction GUC, fail-closed, child policies | Accepted |
| [0026](0026-owners-are-household-data.md) | Owners are household data, not users (ownership half of 0012) | Accepted |
| [0027](0027-open-signup-replaces-invite-only.md) | Open signup replaces invite-only (invite half of 0013) | Accepted |
