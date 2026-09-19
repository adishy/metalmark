# Kestrel — Architecture

This is the contract every workstream builds against. The two hard seams that let agents work in
parallel are **(1) the database schema** and **(2) the OpenAPI spec**. Change them only via a PR
that updates this document.

---

## 1. System overview

```
                    Tailscale / LAN only  (no public exposure)
 ┌──────────┐   HTTPS    ┌───────────────┐        ┌──────────────┐
 │  Browser │◀──────────▶│  Caddy (TLS)  │◀──────▶│  web (nginx) │  static React PWA
 │  (PWA)   │            └───────┬───────┘        └──────────────┘
 └──────────┘                    │ /api
                                 ▼
                          ┌──────────────┐   SQLAlchemy   ┌────────────┐
                          │  api (FastAPI)│◀─────────────▶│ PostgreSQL │
                          └──────┬───────┘                └─────▲──────┘
                                 │ enqueue sync_jobs             │
                                 ▼                               │
                          ┌──────────────┐  poll GET /accounts   │
                          │ worker        │──────────────────────┘
                          │ APScheduler + │  HTTPS   ┌────────────────────┐
                          │ job consumer  │─────────▶│ SimpleFIN Bridge    │
                          └──────────────┘           │ (external, read-only)│
                                                      └────────────────────┘
```

- **api**: request/response, auth, authorization, CRUD, reporting queries, enqueues sync jobs.
- **worker**: (a) APScheduler cron → scheduled full sync of every active connection; (b) consumes
  `sync_jobs` rows for on-demand "Sync now"; (c) runs the rules engine on newly ingested transactions;
  (d) snapshots daily balances. Same codebase/image as `api`, different entrypoint.
- **No inbound webhooks.** Everything the worker needs, it pulls.

---

## 2. Data model

Amounts: `NUMERIC(19,4)`, mapped to `Decimal`. Timestamps: `timestamptz`, stored UTC (bucketed to the
household timezone for reporting). All rows scoped to a `household` for authorization.

**Currency (first-class, v1):** every household has a `base_currency` (the rollup/display currency). Accounts
and transactions carry their **own native currency**, which may differ. Money is stored in native currency +
currency code; the base-currency value is computed via an **`fx_rates`** table:
- `fx_rates` — `base_currency, quote_currency, rate_date, rate, source (auto|manual)`. Unique
  `(base_currency, quote_currency, rate_date)`.
- **Which rate:** "current" views (today's net worth, an account balance) convert at the latest rate;
  **time-series** views (net-worth-over-time, cash-flow by month) convert each point at that date's rate, so
  history doesn't rewrite itself when rates move. The rate used is stored alongside computed base amounts for
  reproducibility.
- Rates are pulled daily from a free source (Frankfurter/ECB) by the worker; **manual rate entry** is a
  first-class fallback (manual parity) for currencies the source lacks.
- All conversions use `Decimal` with a documented rounding policy. Never mix currencies without conversion.

**Net worth correctness:** net worth = Σ(asset balances) − Σ(liability balances), using `accounts.is_asset`
(derived from type) rather than a sign guess. Snapshots are keyed on the provider's `balance_date` (not the
worker's wall clock), and the net-worth line **carries forward** the last known balance across days with no
snapshot (sync outages, stale accounts). SimpleFIN gives current balance only, so history builds forward from
first sync — there is no backfill (set that expectation in the UI). A manual investment account's balance is
derived as Σ(`holdings.market_value`) and snapshotted the same way.

### Identity & sharing
- **users** — `id, email (unique), password_hash (argon2), display_name, created_at, is_admin`.
- **households** — `id, name, created_at`. (Also called a "space". A user belongs to exactly one for v1.)
- **household_members** — `household_id, user_id, role (owner|member), joined_at`.
- **invites** — `id, household_id, email, token_hash, role, expires_at, accepted_at`. Signup requires a valid invite.
- **sessions** — **server-side** session table (decided, not "or cookie") so we get revocation /
  logout-everywhere and idle + absolute expiry. `id, user_id, csrf_token, created_at, last_seen_at, expires_at`.

### Accounts & connections
- **account_connections** — one per SimpleFIN claim.
  `id, household_id, provider ('simplefin'), access_url_encrypted, org_name, status (ok|auth_error|error|stale),
   last_synced_at, last_error, created_at`. `access_url_encrypted` holds the Basic-auth SimpleFIN URL,
  encrypted at rest (see §5).
- **accounts** —
  `id, household_id, connection_id (nullable → manual), external_id (SimpleFIN account id),
   external_key (org_id + account number/name, for reconnect remap — see §3), name,
   type (depository|credit|investment|loan|other), subtype, institution, currency,
   current_balance, available_balance, balance_date, is_asset (derived from type: depository/investment=asset,
   credit/loan=liability), owner_user_id (nullable → joint/shared), is_manual (bool), is_hidden,
   created_at, updated_at`.
  - **Ownership is simplified for v1** to a single `owner_user_id` per account (null = joint/shared) plus a
    per-split owner. Fractional per-transaction ownership (`share_pct`) is deferred — it was over-modeled for
    a few users and created three overlapping ownership mechanisms. **Attribution precedence:**
    split.owner_user_id → account.owner_user_id → joint.
- **securities** — a reusable instrument. `id, household_id, name, ticker, security_type
  (stock|etf|mutual_fund|bond|option|crypto|cash|other), currency, is_manual`.
- **holdings** — a position in an account (SimpleFIN has no holdings object → always manual/imported).
  `id, account_id, security_id, quantity, cost_basis, market_value, as_of`. An investment account's balance is
  derived as Σ(market_value).
- **security_prices** — `security_id, price_date, price, currency, source (auto|manual)`. Manual entry is
  first-class; auto price fetch is optional and pluggable.
- **investment_transactions** — individual buy/sell/dividend/interest/fee/transfer events (fixes Monarch's
  "can't see investment transactions / no dividend category" gap).
  `id, account_id, security_id, type (buy|sell|dividend|interest|fee|split|transfer), trade_date, quantity,
   price, amount, currency, notes`. Dividends/interest flow into income reporting; buys/sells adjust cost basis.
  All ent(er)able by hand; sync/import populate the same table.

### Transactions
- **transactions** —
  `id, household_id, account_id, external_id (nullable for manual), import_hash (dedupe key for
   manual/CSV/OFX = sha256(account_id + transacted_at + amount + description)), posted_at, transacted_at,
   amount (Decimal; sign: positive = money in), currency (native, defaults to account currency),
   base_amount (Decimal; amount converted to household base currency at the txn date), fx_rate_date,
   description (raw), merchant (cleaned),
   category_id, is_pending, review_status (needs_review|reviewed|ignored), is_hidden, is_split_parent,
   transfer_group_id (nullable → part of a transfer), field_sources (jsonb), notes,
   source (simplefin|csv|ofx|manual), created_at, updated_at`.
  - Unique index `(account_id, external_id)` where `external_id` not null; unique `(account_id, import_hash)`
    where `external_id` is null (stops re-importing the same CSV/OFX row).
  - **`field_sources` = the provenance model (critical).** A per-transaction map
    `{category: provider|rule|user, merchant: …, is_hidden: …, review_status: …, splits: …}`. It is the
    *only* way to distinguish a human edit from a rule- or provider-set value. **Write precedence:**
    `user` always wins; a `rule` may overwrite `provider` or an earlier `rule`, never `user`; the provider
    sync may overwrite only provider-owned fields (`amount, description, posted_at, is_pending`) and never a
    `user`/`rule` field. This is the **shared contract between WS-A (sync), WS-B (model), WS-C (rules)** and
    must be co-designed and frozen together (see PLAN.md).
- **transfer_groups** — links the two (or more) legs of a money movement (checking→savings, card/loan
  payments). `id, household_id, matched_by (auto|manual), created_at`. Auto-matched on ingest by
  opposite-sign amounts + near dates + cross-account (see §3). **All cash-flow/spend reporting excludes
  transfers** (they are not income or expense); they still appear in the transaction list.
- **transaction_splits** — `id, parent_txn_id, amount, category_id, owner_user_id, notes`.
  A split parent keeps `is_split_parent=true`; children carry the category/owner breakdown. Percentage
  splits round to the cent with the remainder assigned to the largest child (documented rounding policy).
- **tags** — `id, household_id, name, color`. **transaction_tags** — `transaction_id, tag_id`.
- **pending_reconcile** — tracks pending rows so a pending txn that posts under a *new* id (or vanishes)
  is reconciled by `(account_id, amount, ~date, description)` rather than only by SimpleFIN `id`. See §3.

### Categories & budget
- **category_groups** — `id, household_id, name, type (income|expense|transfer), sort`. **The group's `type`
  is the single source of truth** for income/expense/transfer — categories do *not* duplicate it (the earlier
  `is_income`/`is_transfer` flags are dropped to avoid disagreeing sources of truth).
- **categories** — `id, household_id, group_id, name, icon, color, rollover, sort`.
- **households.settings** — `base_currency` (single base currency for v1; see §2 currency note),
  `timezone` (household-local; all month boundaries and balance snapshots bucket in this tz, not UTC).
- **budgets** (v1.1) — `id, household_id, category_id, month, amount`.

### Rules
- **rules** — `id, household_id, priority (int), name, enabled, conditions (jsonb), actions (jsonb), created_at`.
  - `conditions`: `{ merchant_contains?, description_regex?, amount_min?, amount_max?, direction? (in|out),
     account_ids?, category_id?, is_pending? }` (all AND-ed).
  - `actions`: `{ set_category_id?, add_tag_ids?, set_owner_user_id?, rename_merchant?, set_hidden?,
     mark_reviewed?, split?: [{amount|pct, category_id, owner_user_id}] }`.

### History & audit
- **balance_snapshots** — `account_id, balance_date, balance, currency`. Unique `(account_id, balance_date)`.
  Powers net-worth-over-time; converted to base at each date's FX rate.
- **sync_jobs** — the on-demand queue. `id, connection_id, requested_by, status (queued|running|done|error),
   started_at, finished_at, error`. Claimed with `FOR UPDATE SKIP LOCKED`.
- **sync_runs** — per-run record for the **admin observability dashboard**, not just a freshness badge.
  `id, connection_id, trigger (cron|manual|reconnect), started_at, finished_at, duration_ms, http_ms,
   accounts_seen, txns_inserted, txns_updated, txns_reconciled, pendings_expired, transfers_matched,
   rules_applied, bytes_fetched, http_status, status, error`.
- **sync_run_events** — ordered structured log lines for a run (sanitized — never the access URL or PII):
  `sync_run_id, ts, level, event, detail (jsonb)`. This is the "show me exactly what the sync did" feed.
- **audit_log** — `id, household_id, user_id, action, entity, entity_id, at, meta (jsonb)`. Every manual edit
  and every sync-applied change records who/what set a field (feeds `field_sources`).

### Connection ↔ ledger decoupling (robustness requirement)
- **Transactions, accounts, holdings, and snapshots belong to the household, not to a connection.** A
  `connection` is disposable sync metadata. Deleting a connection sets its accounts to `is_manual=true` and
  **preserves all history**; re-adding a connection **remaps** to those accounts (by `external_key`) and
  resumes. Users never "restore transactions" after a reconnect. This is enforced by FK design
  (`accounts.connection_id` is nullable `ON DELETE SET NULL`, transactions never reference `connection_id`).

---

## 3. Aggregation & sync engine

### Provider interface (the seam that keeps us un-Plaid-locked)
```python
class AggregatorProvider(Protocol):
    def claim(self, setup_token: str) -> str: ...           # -> access_url (to encrypt & store)
    def fetch(self, access_url: str, *, start: datetime,
              end: datetime | None, balances_only: bool) -> AccountSet: ...
```
`AccountSet` normalizes SimpleFIN's `{connections, accounts[{transactions[]}], errlist}` into internal DTOs.
Only `SimpleFinProvider` is implemented in v1.

### Claim flow (and reconnect remap)
1. User clicks **Connect** → gets sent to SimpleFIN Bridge `/create`, pastes back the Base64 **setup token**.
2. API decodes token → `POST /claim/:token` → receives `https://user:pass@server/simplefin` **access URL**.
3. Encrypt and store as an `account_connections` row (status `ok`). Never log or return the raw URL.
4. **Reconnect must not duplicate history.** A re-claim (after `auth_error`) yields a *new* connection and
   possibly new account external_ids. Do **not** blindly insert new accounts. On the first fetch of a new
   connection, match each incoming account to an existing one by `external_key` (org_id + account number/name)
   and **rebind `connection_id`** to the existing `accounts` row. Only genuinely new accounts are inserted.
   This keeps transactions/snapshots attached to the stable internal `account_id`.

### Sync algorithm (worker)
- **Scheduled**: APScheduler cron (default every 6h; configurable) enqueues a `sync_job` per active connection.
- **On demand**: API inserts a `sync_job` row; worker consumer claims it with
  `SELECT … FOR UPDATE SKIP LOCKED`. **One running job per connection** is enforced (partial unique index on
  `sync_jobs(connection_id) where status='running'`, or a Postgres advisory lock) so a cron run and a manual
  "Sync now" can't collide. A **reaper** re-queues jobs stuck in `running` past a timeout (worker crash).
- Per job: fetch from a **per-account low-water mark** (earliest unsettled point), not a fixed
  `now − 3d` window, so a pending charge that posts a week later is still reconciled:
  `GET /accounts?start-date=<min(account low-water marks)>&pending=1`.
  - **Upsert accounts** by `(connection_id, external_id)` — or by remap (see claim flow step 4): name,
    balances, `balance_date`, `external_key`.
  - **Ingest transactions** — provenance-aware (uses `field_sources` from §2):
    - new SimpleFIN `id` → insert (`review_status=needs_review`, provider-owned fields tagged `provider`),
      then run the **rules engine** (tags touched fields `rule`), then attempt **transfer matching**;
    - existing `id` → update only provider-owned fields; a field tagged `user` or `rule` is never overwritten.
    - **pending→posted / disappearing pendings**: match by SimpleFIN `id` first; if a tracked pending row is
      absent from the response, reconcile it against a new posted row by `(account_id, amount, ~date,
      description)` and merge (carrying user edits forward); expire pendings that neither post nor reappear
      within a TTL so no phantom row lingers.
  - **Transfer matching**: within a household, pair a new txn with an opposite-sign txn of equal magnitude in
    a *different* account within a few days → create/attach a `transfer_group`. Manual override in the UI.
  - **Balance snapshot**: upsert `balance_snapshots(account_id, balance_date, current_balance)` keyed on the
    provider's `balance_date`.
  - Map SimpleFIN `errlist`: `con.auth` → connection.status=`auth_error` (UI prompts reconnect **and** fires a
    notification, see §5); `act.*` → per-account warning; anything else → `error` with `last_error`.
- **Idempotent & re-runnable.** Amounts parsed as `Decimal(str)`. Conservative backoff on HTTP errors
  (SimpleFIN publishes no rate limits — be polite, exponential backoff, cap concurrency to 1/connection).
- **Tested against adversarial fixtures, not just the benign demo token**: id-instability, disappearing
  pendings, reconnect-remap, transfer pairs, duplicate re-import (see PLAN.md acceptance bars).
- Freshness surfaced everywhere via `connection.last_synced_at` + `status` → account badges
  ("Synced 2h ago", "Needs attention", "Reconnect").

### Manual parity & the "sync is just another writer" model
**Principle: anything sync can do, a human can do by hand** — create accounts, add/edit/split/categorize
transactions, enter holdings + their types + prices, record investment transactions (incl. dividends), set
balances, link transfers, and enter FX rates. The manual write path is the **canonical** one; the SimpleFIN
provider is an adapter that produces the *same* writes a human would (tagging its fields `provider` in
`field_sources`). Consequences:
- The manual ledger is built and proven correct **first**; sync is layered on top and can never write
  anything the model doesn't already support (see PLAN.md build order).
- **CSV import**: upload → column-mapping UI → `import_hash` dedupe preview → commit. `source='csv'`.
- **OFX/QFX import** (defused XML parser): for TreasuryDirect and brokerages SimpleFIN can't reach.
  `source='ofx'`.
- Manual account: user sets type/currency/balance; balance edits snapshot into `balance_snapshots`.
- Investment: securities, holdings, prices, and investment transactions are all hand-enterable and CSV-importable.

---

## 4. Feature mechanics

- **Transaction review**: `review_status` drives the queue. List view + **mobile swipe deck**
  (right = mark reviewed, left = flag/recategorize, up = open detail sheet: category, owner, tags,
  split, notes). Built with framer-motion + @use-gesture; keyboard shortcuts on desktop (j/k/e/…).
- **Splits**: parent → N children by `$` or `%`; children carry category + owner. Reporting reads splits
  when present, else the parent.
- **Rules**: applied on ingest and via "apply to existing" (batch). Ordered by `priority`. Includes
  Monarch-style auto-split. Deterministic + unit-tested against a fixture set.
- **Sharing / Shared Views**: account- or transaction-level ownership; filters ("mine", "partner's",
  "joint"). Authorization = household membership; ownership drives *views*, not access.
- **Reporting** (ECharts, all reading the same `/reports` query API; **everything converted to base currency**
  at the correct-date rate, transfers excluded from cash-flow):
  - Net worth over time (area/line from `balance_snapshots`).
  - **Cash-flow Sankey**: income sources → category groups → categories for a period.
  - Spending by category (donut) with click-through drill-down to a filtered transaction list.
  - Income vs. expense trend (stacked bar), month-over-month.
  - **Investment reporting** (addresses Monarch's weakest area): dividends/interest income, cost-basis vs.
    market value.
  - **Consolidated holdings / allocation view** (explicit feature request): a single page that aggregates
    every holding across *all* accounts — the same security held in multiple accounts is summed into one row
    (quantity, total market value in base currency, cost basis, gain/loss) — with **% allocation** of the
    whole portfolio, and toggles to group/weight by security, `security_type`, account, or currency.
    Reads `holdings` × latest `security_prices`, converts to base currency.
  - Every chart is a lens over the same filter model (date range, accounts, owners, categories, currency).
- **Admin sync observability** (you're technical peers, so this is a feature not an afterthought): a per-run
  view of `sync_runs` + `sync_run_events` — trigger, duration, HTTP timing, counts (inserted/updated/
  reconciled/expired/matched), errors, and a sanitized event log. "Sync now" + per-connection history + the
  ability to see exactly which transactions a given run touched.

---

## 5. Security & ops

- **Secrets at rest**: `access_url_encrypted` uses authenticated symmetric encryption (libsodium secretbox /
  Fernet) with `KESTREL_SECRET_KEY` loaded from a **docker secret file, not an env var** (env leaks via
  `docker inspect`, crash dumps, child processes). Document rotation + re-claim path if the key is lost.
- **Authorization / tenant isolation (biggest ongoing risk)**: hand-written `household_id` filters on every
  endpoint are the classic source of cross-tenant (IDOR) leaks — one missed `.filter()` exposes another
  household. Enforce it in **one place, not per-handler**: Postgres **Row-Level Security** keyed on a
  per-session GUC, or a single mandatory query-scoping layer. The model is multi-tenant even with one
  household today.
- **Auth**: argon2id password hashing; httpOnly + SameSite session cookies; CSRF token for mutations;
  login rate-limit + lockout. Invite-only signup. (Verify SameSite=Strict doesn't break the invite-link
  landing; use Lax there if needed.) No 2FA is an accepted risk given Tailscale + invite-only — logged as such.
- **Privacy caveat**: v1 ownership drives *views*, not *access* — any household member can read a partner's
  accounts. The UI must not imply privacy it can't enforce; truly private accounts are out of scope for v1.
- **Untrusted files**: OFX/QFX/CSV are user-supplied. Parse OFX/QFX with a **defused XML parser** (XXE /
  billion-laughs). Validate/limit CSV size and columns.
- **Transport**: Caddy TLS; reachable only over Tailscale/LAN. No public ingress.
- **PWA**: cache the app *shell* only; data endpoints are `no-store` — the service worker must never persist
  financial API responses on a (possibly shared) device.
- **Backups**: nightly `pg_dump` — which is cleartext PII — is **encrypted at the destination**, with a
  defined retention. Store the backup encryption key / `KESTREL_SECRET_KEY` **physically separate** from the
  dumps (a dump + co-located key = full compromise). Documented restore drill.
- **Failure alerting**: with no webhooks and 6h polling, an `auth_error` or a failed backup can silently rot
  data for days. Fire a notification (email/ntfy/Slack webhook) on `auth_error`, repeated sync failure, or
  backup failure — badges only help if someone looks.
- **Observability**: structured logs, `/healthz`, sync run history, Sentry optional.
- **Audit**: sensitive actions (connect/remove account, invite, rule change) written to `audit_log`.

---

## 6. Known risks (and where they're handled)

| Risk | Mitigation | Owner WS |
|---|---|---|
| SimpleFIN has no holdings/securities data | First-class manual holdings/prices/investment txns; balance-only auto for investment accounts | INV |
| TreasuryDirect coverage unreliable | OFX/manual fallback (already the plan) | IMP |
| Amounts are decimal strings; float drift | `Decimal`/`NUMERIC` everywhere; property tests | L, 0 |
| pending→posted id instability / disappearing pendings | `pending_reconcile` state + match by (amount,~date,desc); low-water mark, not fixed window; adversarial fixtures | SYNC |
| Unknown SimpleFIN rate limits | Scheduled polling, backoff, 1 concurrent req/connection | SYNC |
| Encryption key loss = dead connections | Key from docker secret; backup separate from db dumps; re-claim flow | OPS |
| Sync clobbering user edits | **Field-level provenance (`field_sources`), user > rule > provider** | SYNC, L, R |
| **Transfers double-counted → wrong Sankey/cash-flow** | `transfer_group` + auto-match on ingest; reporting excludes transfers | SYNC, L, UR |
| **Reconnect creates duplicate accounts/history** | Remap by `external_key`; ledger decoupled from connection (transactions survive removal) | SYNC |
| **Re-importing CSV/OFX duplicates rows** | `import_hash` unique index for manual/imported txns | IMP |
| **Cross-household (IDOR) data leak** | RLS or one mandatory scoping layer, not per-handler filters | P0, OPS |
| Silent stale data (no webhooks, 6h poll) | Notification on `auth_error` / sync / backup failure | SYNC, OPS |
| **Multi-currency mis-conversion / wrong net worth** | Per-txn currency + dated FX rates; conversion is `Decimal` with rounding policy; property tests over currencies | L, UR, 0 |

---

## 7. Testing strategy (first-class — correctness is requirement #1)

Built as a workstream from day one (WS-0), not bolted on. Red-green discipline: a failing test defines each
behavior before the code.

- **Unit** — pure logic: money/FX math, provenance precedence, split rounding, rule evaluation, transfer
  matching, dedupe hashing. Property-based (Hypothesis / fast-check) for anything numeric or currency-related.
- **Integration** — real Postgres via `testcontainers`; the sync engine tested against a **mock SimpleFIN
  server** driven by fixtures (the adversarial set: id-instability, disappearing pendings, reconnect-remap,
  transfer pairs, duplicate re-import, multi-currency accounts). Tenant-isolation test (household B ⊄ A).
- **Contract** — schemathesis validates live API responses against `openapi.yaml` so backend can't silently
  drift from the spec the frontend mocks against.
- **E2E** — Playwright over the running stack with a seeded, mocked dataset: connect flow, review/swipe,
  split, reconnect-keeps-history, report totals reconcile.
- **CI gates** — lint, type-check, all suites, coverage floor on core packages, contract check. Red build
  blocks merge. Deterministic fixtures; a golden dataset with known-correct report totals.

## 8. Performance (snappy, back and front)

- **Backend** — async FastAPI; keyset (cursor) pagination on transactions; indexes on
  `(household_id, account_id, posted_at)`, `(account_id, external_id)`, category/date; avoid N+1 (explicit
  joins / selectin loading); **precomputed rollups** for net-worth-over-time and category reports (a
  materialized daily balance + monthly category aggregate refreshed by the worker, not recomputed per request);
  cache FX rates. Target API p95 < 150ms warm.
- **Frontend** — TanStack Query caching + prefetch; **virtualized** transaction list (react-virtual);
  **optimistic updates** on swipe/categorize so review feels instant; code-splitting per route; memoized
  ECharts with downsampled series for long ranges; PWA app-shell cache. Target: 60fps list scroll, review
  action < 100ms perceived.
- **Measured, not assumed** — a perf smoke test seeds ~50k transactions and asserts list + report latency
  budgets in CI.
