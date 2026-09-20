# MetalMark — Self-Hosted Monarch Money

> Named for the butterfly family, like the app it replaces — **MetalMark**, self-hosted.

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
| Sharing model | **Household + per-account/transaction ownership**, where an owner is **household data** (a name + `person`/`shared` kind), not a user account (ADR-0026) | Supports joined finances and individual accounts in one place. Adding a kid, a "House" pot or a non-app partner costs a row, not a login; "Shared" is a real owner so attribution is never null. |
| Auth | Email + password (argon2), httpOnly session cookies, **open signup** — the first signer creates the household, later ones join it; `METALMARK_OPEN_SIGNUP=false` closes it (ADR-0027) | No public registration *and* no invite plumbing to maintain. Honest cost: anyone who can reach the instance can join the household and read all of it — acceptable only because the app is LAN/VPN-only (ADR-0002), so the network is the gate. |
| Currency | **Multi-currency via single-currency accounts** — each account is one currency; a household base currency; dated FX conversion (current for "now", historical for time series); FX drift shown as a **currency-revaluation line** (ADR-0017) | Real foreign-currency accounts exist here; single-currency-per-account avoids the balance contradiction of per-txn currency. Wallet accounts + full FX P&L deferred. |
| Manual parity | **Anything automation can do, a human can do by hand** — manual accounts, transactions, holdings (with types), balances, transfers, FX rates | Fixes Monarch's #1 self-hosting gripe (manual/cash is second-class); also makes sync auditable and recoverable. |
| Build order | **Manual-first, then automate** — build+prove the manual ledger, then add SimpleFIN sync as "just another writer" into the proven model | Correctness before convenience; sync can't corrupt a model it doesn't own. |
| Robustness | **Transactions are decoupled from connections** — removing/re-adding a connection never destroys history | Directly fixes Monarch's flaky-reconnect + duplicate-transaction pain. |
| Correctness | **Test harness is first-class**: red-green TDD, unit + integration (real Postgres, mock SimpleFIN) + e2e (Playwright), realistic fixtures, CI gates | User's hard requirement; financial correctness is non-negotiable. |

## Run it locally (container-first)

Requires Docker + Docker Compose. Everything runs in containers; nothing is exposed beyond localhost.

```bash
cp .env.example .env                     # dev defaults are fine
# a dev secret key already lives at secrets/metalmark_secret_key (gitignored)

docker compose up -d --build             # db + api + worker + web
docker compose run --rm api alembic upgrade head          # create schema + app role + RLS
docker compose run --rm \
  -e METALMARK_SEED_EMAIL=owner@example.com \
  -e METALMARK_SEED_PASSWORD=devpassword123 \
  -e METALMARK_SEED_HOUSEHOLD=Home \
  api python -m app.seed --demo          # first household + owner + default categories
```

Then open **http://localhost:5173** and sign in with `owner@example.com` / `devpassword123`.
The API is at http://localhost:8000 (`/healthz`, `/docs`). The Vite dev server proxies `/api` → the API.

Run the backend test suite (real Postgres via the `db` service, isolated `metalmark_test` DB):

```bash
docker compose run --rm -e METALMARK_TEST_PG_HOST=db -e METALMARK_SECRET_KEY=test-secret api pytest -q
```

Python dependencies are managed with **uv** (`backend/uv.lock`); the image installs from the lock.

## Stack (boring on purpose)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 + Alembic, Pydantic v2. Deps via **uv**.
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
metalmark/
  docker-compose.yml
  Caddyfile
  backend/            # FastAPI app, SQLAlchemy models, Alembic migrations, worker
  frontend/           # Vite + React + TS PWA
  contracts/          # OpenAPI spec (source of truth) + generated TS client
  docs/
    ARCHITECTURE.md    # system design, data model, sync engine, security
    PLAN.md            # phased, multi-agent execution plan
    adr/               # Architecture Decision Records — the "why" log (record as we go)
```

## Read next

1. `docs/ARCHITECTURE.md` — the system design every agent builds against.
2. `docs/PLAN.md` — workstreams, dependencies, agent assignments, acceptance criteria.
3. `docs/adr/` — why each load-bearing decision was made. **New decisions get a new ADR in the same PR**;
   accepted ADRs are immutable and superseded rather than edited (`docs/adr/README.md` has the process).
