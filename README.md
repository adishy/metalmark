# MetalMark — Self-Hosted Personal Finance

> Named for the butterfly family — **MetalMark**, self-hosted.

A self-hostable personal-finance app for a small household (a few users, some with joined
finances). Feature target: **what the mainstream personal-finance apps do** — auto-syncing
accounts, a bulletproof transaction pipeline with categories/owners/splits, shared accounts,
swipe-to-review, and reporting (Sankey cash flow, category breakdowns, net worth over time).

## Product decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Account aggregation | **SimpleFIN Bridge only**, behind a pluggable provider interface | Self-hostable, private (read-only), $15/yr, no business approval. Interface leaves the door open to Plaid later without a rewrite. |
| Hard accounts (TreasuryDirect, stubborn brokerages) | **Auto-sync where supported, manual/CSV/OFX fallback** | SimpleFIN's protocol has no holdings object and TreasuryDirect coverage is unreliable; manual entry closes the gap. |
| Deployment / network | **LAN/VPN only (Tailscale), poll on a schedule** | No internet exposure, no webhooks to secure. SimpleFIN is poll-based, so this is a natural fit. |
| Sharing model | **Household + per-account/transaction ownership**, where an owner is **household data** (a name + `person`/`shared` kind), not a user account (ADR-0026) | Supports joined finances and individual accounts in one place. Adding a kid, a "House" pot or a non-app partner costs a row, not a login; "Shared" is a real owner so attribution is never null. |
| Auth | Email + password (argon2), httpOnly session cookies, **open signup** — the first signer creates the household, later ones join it; `METALMARK_OPEN_SIGNUP=false` closes it (ADR-0027) | No public registration *and* no invite plumbing to maintain. Honest cost: anyone who can reach the instance can join the household and read all of it — acceptable only because the app is LAN/VPN-only (ADR-0002), so the network is the gate. |
| Currency | **Multi-currency via single-currency accounts** — each account is one currency; a household base currency; dated FX conversion (current for "now", historical for time series); FX drift shown as a **currency-revaluation line** (ADR-0017) | Real foreign-currency accounts exist here; single-currency-per-account avoids the balance contradiction of per-txn currency. Wallet accounts + full FX P&L deferred. |
| Manual parity | **Anything automation can do, a human can do by hand** — manual accounts, transactions, holdings (with types), balances, transfers, FX rates | Fixes the common self-hosting gripe that manual/cash is second-class; also makes sync auditable and recoverable. |
| Build order | **Manual-first, then automate** — build+prove the manual ledger, then add SimpleFIN sync as "just another writer" into the proven model | Correctness before convenience; sync can't corrupt a model it doesn't own. |
| Robustness | **Transactions are decoupled from connections** — removing/re-adding a connection never destroys history | Directly fixes the flaky-reconnect + duplicate-transaction pain of the hosted apps. |
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

On a first run the worker logs `worker.database_not_ready` until the migration has created the app
role; that is the expected order of these two commands, and it waits rather than exiting. Nothing
syncs without it, and a stack whose worker is gone looks exactly like a healthy one.

Then open **http://localhost:5173** and sign in with `owner@example.com` / `devpassword123`.
The API is at http://localhost:8000 (`/healthz`, `/docs`). The Vite dev server proxies `/api` → the API.

**This stack is the development one, so it is a PWA with no service worker.** `web` runs `npm run dev`;
`vite-plugin-pwa` builds the worker only for a production build, and `npm run build` → `frontend/dist/`
is what produces one. Two things follow, and the app says both rather than pretending otherwise:
**desktop notifications cannot be displayed here** (the panel says so on `/admin` instead of offering a
switch that would change nothing), and neither can the app be installed or used offline. Reaching it over
plain `http://` on a LAN hostname is a second, independent block — the browser's notification API sits
behind a secure-context gate, so `https://` or `localhost` is required as well. ADR-0037 records both.
The next section is the deployment that clears them.

Run the backend test suite (real Postgres via the `db` service, isolated `metalmark_test` DB):

```bash
docker compose run --rm -e METALMARK_TEST_PG_HOST=db -e METALMARK_SECRET_KEY=test-secret api pytest -q
```

Python dependencies are managed with **uv** (`backend/uv.lock`); the image installs from the lock.

## Stand up an instance

**The install is one file and one command.** No clone, no Node, no Python, no `.env` to fill in, and no
credential to write down: the images come from GHCR and the deployment generates its own secrets and
applies its own migrations on first boot (ADR-0039).

```bash
mkdir metalmark && cd metalmark
curl -fsSLO https://raw.githubusercontent.com/adishy/metalmark/main/deploy/compose.yaml

echo 'METALMARK_SITE=metalmark.local' > .env    # the name every device will reach it by
docker compose up -d
```

That is the whole thing. `up` does not return until the credentials exist and the schema is current, so
there is nothing to wait for and nothing to run afterwards.

Open **https://metalmark.local:8790** and sign up — **the first signup creates the household** (ADR-0027),
so there is nothing to seed either. From the same machine, **http://localhost:8791** works immediately,
with no certificate to install and still a working service worker.

Then **trust Caddy's CA**, or every device will refuse the connection properly. There is no public DNS
name on a LAN for a real CA to validate, so Caddy issues from its own root:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./metalmark-ca.crt
# macOS — other platforms have their own trust store command
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ./metalmark-ca.crt
```

Clicking through the certificate warning instead is not a shortcut: a certificate error makes the origin
refuse service workers outright, so notifications keep not working and nothing on screen says why.

A few things worth knowing once it is up:

- **`METALMARK_SITE` is the name in the certificate.** A request with any other `Host` fails the TLS
  handshake rather than serving the app under a name the certificate does not cover. Changing it means
  `docker compose up -d` again — Caddy re-issues on start, and every client has to trust the new root.
  **An IP address is a valid value** — Caddy issues a certificate carrying it as an `IP Address` SAN and
  serves it to a client sending no SNI, which is what a browser does for an IP literal. So
  `METALMARK_SITE=192.168.1.50` gives you a trusted `https://192.168.1.50:8790`. It is also the brittle
  choice: the certificate names that exact address, so a new DHCP lease breaks it until you set the new
  one and come back up. A LAN name, or a tailnet name, survives being renumbered.
- **`METALMARK_HTTPS_PORT`** (8790) and **`METALMARK_HTTP_PORT`** (8791) move the two doors. The defaults
  are un-round on purpose — `8080` and `8443` are the ports most likely to already be taken on a machine
  running other self-hosted services, and a collision there fails the very first `up` — so you should not
  need them. Port 80 is not published; the compose file says where to add it if you want an http→https
  redirect.
- **Close signup once you are in.** Anyone who can reach the instance can join the household and read all
  of it, which is fine while it is just you. Add `METALMARK_OPEN_SIGNUP=false` to `.env` and run
  `docker compose up -d` to shut the door. It has to stay open until the first account exists.
- **Connecting a bank** is in the app: Settings → Connections, where you paste a SimpleFIN setup token.
  The token is encrypted at rest with the key generated on first boot; `/admin` is where sync runs, its
  logs and its schedule live.
- **Updating** is `docker compose pull && docker compose up -d`. The deployment tracks the `latest` tag;
  pin `METALMARK_API_IMAGE`/`METALMARK_WEB_IMAGE` in `.env` (both at once) to hold a specific build.
- **Backups.** `scripts/backup.sh` does an encrypted dump, but it lives in the repo and not in the image,
  so a bare install cannot reach it. The unconditional version needs no checkout, and there are **two**
  volumes to take — `db_data` and `metalmark_secrets`:

  ```bash
  docker compose exec -T db pg_dump -U metalmark metalmark | gzip > metalmark-$(date +%F).sql.gz
  ```

  A dump without the secrets volume restores the ledger but not the *bank connections*: the Fernet key
  that decrypts each stored access URL is in that volume, and an access URL whose key is gone cannot be
  recovered — it is a reconnect, not a restore. Back up both or neither.

### What this is, and what it changes

The deployment is `deploy/compose.yaml`, standalone and complete. It is not an overlay on the dev stack,
and the two changes that matter most are not about the deployment at all:

| | dev stack | `deploy/compose.yaml` |
|---|---|---|
| `web` | Vite dev server, HMR, source bind-mounted | `npm run build` → nginx serving `dist/` |
| Service worker | none — `vite-plugin-pwa` builds one only for production | `dist/sw.js`, so **notifications can display** |
| `METALMARK_ENV` | `dev` | `prod`: `Secure` session cookie, no dev CORS, the fake aggregator refuses to exist |
| Credentials | `secrets/metalmark_secret_key`, created by hand | generated on first boot, never rewritten |
| Schema | migrated by hand | applied by a one-shot container before the api starts |
| Reachable at | `http://localhost:5173` | `https://<METALMARK_SITE>:8790` (any device), `http://localhost:8791` (this one) |
| Installable, works offline | no | yes |

**The two doors are two answers to the same problem.** Caddy's is the one for every device, and it is
not decoration: `METALMARK_ENV=prod` sets the session cookie's `Secure` flag, so a browser on another
machine cannot log in over plain http at all. The loopback door exists because plain HTTP is fine on an
interface that never leaves the machine, and `localhost` is a *secure context* by specification — so it
is the one way in that needs no certificate installed and still gets a service worker.

### Deploying from a checkout

Same file, one extra flag, and it builds from your working tree instead of pulling:

```bash
docker compose -f deploy/compose.yaml up -d --build
```

The project is named after the directory the file is in, which for a checkout is `deploy` — so this
stands *beside* the dev stack rather than replacing it, on its own volume and its own database. That is
also what `./scripts/verify.sh prod` does, along with checking it: nginx's cache headers and SPA
fallback, the `/api` proxy, TLS, the login round trip, that the generated credentials actually landed,
that the published image carries no `.env`, and, in a real browser, that the built app registers a
service worker — the only test of the thing no other gate can reach. See ADR-0038 and ADR-0039.

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
- **Ops:** docker-compose (`db`, `api`, `worker`, `web`), Caddy reverse proxy (§"Stand up an instance"),
  Tailscale, nightly **encrypted** `pg_dump` backups, **admin sync-observability** dashboard (per-run
  logs, counts, timings).

## Repository layout

```
metalmark/
  docker-compose.yml        # the dev stack: Vite dev server, source bind-mounted
  deploy/compose.yaml       # the deployment, standalone and complete (ADR-0038, ADR-0039)
                            #   → the whole install; the Caddyfile and the generated
                            #     credentials are inside it, so there is nothing to copy
  backend/                  # FastAPI app, SQLAlchemy models, Alembic migrations, worker
  frontend/                 # Vite + React + TS PWA; Dockerfile has dev/build/prod stages
  contracts/                # OpenAPI spec (source of truth) + generated TS client
  scripts/                  # verify.sh (every gate), walkthrough, backups, the restore drill
  docs/
    ARCHITECTURE.md         # system design, data model, sync engine, security
    PLAN.md                 # phased, multi-agent execution plan
    adr/                    # Architecture Decision Records — the "why" log (record as we go)
```

## Read next

1. `docs/ARCHITECTURE.md` — the system design every agent builds against.
2. `docs/PLAN.md` — workstreams, dependencies, agent assignments, acceptance criteria.
3. `docs/adr/` — why each load-bearing decision was made. **New decisions get a new ADR in the same PR**;
   accepted ADRs are immutable and superseded rather than edited (`docs/adr/README.md` has the process).
