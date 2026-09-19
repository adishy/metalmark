# Kestrel — Self-Hosted Monarch Money

> Working codename: **Kestrel** (Monarch is a butterfly; a kestrel is a small, fast, self-reliant bird). Rename freely.

A self-hostable personal-finance app for a small household (a few users, some with joined
finances). Feature target: **Monarch Money's core** — auto-syncing accounts, a bulletproof
transaction pipeline with categories/owners/splits, shared accounts, swipe-to-review, and
reporting (Sankey cash flow, category breakdowns, net worth over time).

## Product decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Account aggregation | **SimpleFIN Bridge only**, behind a pluggable provider interface | Self-hostable, private (read-only), $15/yr, no business approval. Interface leaves the door open to Plaid later without a rewrite. |
| Hard accounts (TreasuryDirect, stubborn brokerages) | **Auto-sync where supported, manual/CSV/OFX fallback** | SimpleFIN's protocol has no holdings object and TreasuryDirect coverage is unreliable; manual entry closes the gap. |
| Deployment / network | **LAN/VPN only (Tailscale), poll on a schedule** | No internet exposure, no webhooks to secure. SimpleFIN is poll-based, so this is a natural fit. |
| Sharing model | **Household + per-account/transaction ownership** (Monarch "Shared Views") | Supports joined finances and individual accounts in one place. |
| Auth | Email + password (argon2), httpOnly session cookies, **invite-based signup** | Small trusted user set; no public registration. |
| Currency | **Multi-currency is first-class** — per-account & per-transaction native currency, a base currency per household, conversion at the correct-date FX rate (current for "now", historical for time series) | Real multi-currency accounts exist here; half-modeling would produce wrong net worth. |
| Manual parity | **Anything automation can do, a human can do by hand** — manual accounts, transactions, holdings (with types), balances, transfers, FX rates | Fixes Monarch's #1 self-hosting gripe (manual/cash is second-class); also makes sync auditable and recoverable. |
| Build order | **Manual-first, then automate** — build+prove the manual ledger, then add SimpleFIN sync as "just another writer" into the proven model | Correctness before convenience; sync can't corrupt a model it doesn't own. |
| Robustness | **Transactions are decoupled from connections** — removing/re-adding a connection never destroys history | Directly fixes Monarch's flaky-reconnect + duplicate-transaction pain. |
| Correctness | **Test harness is first-class**: red-green TDD, unit + integration (real Postgres, mock SimpleFIN) + e2e (Playwright), realistic fixtures, CI gates | User's hard requirement; financial correctness is non-negotiable. |

## Stack (boring on purpose)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic, Pydantic v2.
- **DB:** PostgreSQL 16. Money as `NUMERIC` + Python `Decimal` — **never float**.
- **Background work:** a small worker container: APScheduler for cron polls + a Postgres-backed
  `sync_jobs` queue for on-demand "Sync now". **No Redis** at this scale.
- **Frontend:** React + TypeScript + Vite, TanStack Query, Tailwind + Radix/shadcn, **Apache ECharts**
  (pie/bar/line/Sankey), framer-motion + @use-gesture for the swipe deck, `vite-plugin-pwa`.
- **FX rates:** daily pull from a free source (Frankfurter/ECB) into an `fx_rates` table, cached; **manual
  rate entry** supported (manual parity). Outbound-only, fits the LAN/VPN posture.
- **Testing:** pytest + `testcontainers` (real Postgres) + a **mock SimpleFIN server**; vitest; Playwright e2e;
  Hypothesis/fast-check property tests for money & FX. Coverage + contract (schemathesis) gates in CI.
- **Performance targets:** API p95 < 150ms on warm cache; transaction list virtualized at 60fps; swipe uses
  optimistic updates. Keyset pagination, precomputed net-worth/report rollups, cached FX.
- **Ops:** docker-compose (`db`, `api`, `worker`, `web`), Caddy reverse proxy, Tailscale, nightly
  **encrypted** `pg_dump` backups, **admin sync-observability** dashboard (per-run logs, counts, timings).

## Repository layout (target)

```
kestrel/
  docker-compose.yml
  Caddyfile
  backend/            # FastAPI app, SQLAlchemy models, Alembic migrations, worker
  frontend/           # Vite + React + TS PWA
  contracts/          # OpenAPI spec (source of truth) + generated TS client
  docs/
    ARCHITECTURE.md    # system design, data model, sync engine, security
    PLAN.md            # phased, multi-agent execution plan
```

## Read next

1. `docs/ARCHITECTURE.md` — the system design every agent builds against.
2. `docs/PLAN.md` — workstreams, dependencies, agent assignments, acceptance criteria.
