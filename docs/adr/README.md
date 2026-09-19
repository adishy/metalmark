# Architecture Decision Records

We record every architecturally significant decision as an ADR (Michael Nygard format). This is the
authoritative log of *why* Kestrel is built the way it is — agents and humans read it before changing a
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

## Index

| ADR | Title | Status |
|---|---|---|
| [0000](0000-template.md) | Template | — |
| [0001](0001-simplefin-sole-aggregator.md) | SimpleFIN as the sole aggregation provider, behind a pluggable interface | Accepted |
| [0002](0002-poll-based-lan-only.md) | Poll-based sync over LAN/VPN; no webhooks, no public ingress | Accepted |
| [0003](0003-boring-stack.md) | Boring stack: FastAPI + Postgres + React/TS + ECharts | Accepted |
| [0004](0004-postgres-job-queue-no-redis.md) | Postgres-backed job queue; no Redis | Accepted |
| [0005](0005-money-decimal-never-float.md) | Money as NUMERIC/Decimal; never float | Accepted |
| [0006](0006-multi-currency-dated-fx.md) | Multi-currency is first-class, converted at dated FX rates | Accepted |
| [0007](0007-field-provenance.md) | Field-level provenance governs sync-vs-human writes | Accepted |
| [0008](0008-transfers-linked-excluded.md) | Transfers are linked groups, excluded from cash-flow | Accepted |
| [0009](0009-ledger-decoupled-from-connections.md) | The ledger is decoupled from connections | Accepted |
| [0010](0010-manual-first-and-parity.md) | Manual-first build order and manual-parity principle | Accepted |
| [0011](0011-first-class-investments.md) | First-class investments + consolidated allocation view | Accepted |
| [0012](0012-sharing-view-filtering.md) | Household + view-filtering sharing (no per-account access control in v1) | Accepted |
| [0013](0013-auth-sessions-invite-only.md) | Auth: server-side sessions, argon2id, invite-only | Accepted |
| [0014](0014-tenant-isolation-scoping-layer.md) | Tenant isolation via one mandatory scoping layer / RLS | Accepted |
| [0015](0015-test-harness-first-class.md) | Test harness is a first-class workstream (red-green) | Accepted |
| [0016](0016-admin-sync-observability.md) | Admin-grade sync observability | Accepted |
