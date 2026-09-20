# MetalMark — Architecture

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

**Currency model (v1 — decided; see ADR-0017):** accounts are **single-currency**. Each household has one
**immutable** `base_currency`. Each account has a fixed `currency`; a transaction's `currency` **≡ its
account's currency** (a foreign card purchase posts in the account's own currency — the bank already
converted). True multi-currency "wallet" accounts (one account holding several currencies) are **out of scope
for v1**. This removes the single-currency-balance contradiction: an account balance and its snapshots are
always in one currency.
- `fx_rates` — `base_currency, quote_currency, rate_date, rate NUMERIC(19,8), source (auto|manual)`. Unique
  `(base_currency, quote_currency, rate_date)`. **Rates need high precision** (4dp is wrong — 1 JPY ≈ 0.00636
  USD). Storage direction is documented; USD-base households needing e.g. GBP→USD triangulate through EUR in
  the conversion service (one place, controlled rounding).
- **`fx_rates` is the single source of truth; `transactions.base_amount` is a cache.** Reporting reads the
  cache for speed but it is recomputed whenever a rate it depends on changes.
- **`fx_rates` is global, not household-scoped** — the only business table without a `household_id`, so it is
  outside RLS. Market rates are the same fact for everyone, and this install serves one household (ADR-0027),
  so it is right for the deployment it was built for; the `household_id` its writer takes is used to scope the
  `base_amount` recomputation, not the row. The consequence to know: in a database holding two households, a
  rate one of them writes is immediately visible to the other, while only the writer's `base_amount` caches
  are recomputed — the other household's cached values would disagree with it until they are recomputed.
  Making rates household-scoped (column, RLS policy, and a `(household, base, quote, date)` key) is the change
  if a multi-household install ever needs it.
- **Which rate:** convert at the rate for the **latest `rate_date` ≤ the target date** (the transaction's
  household-local date, same date basis as month bucketing). "Current" views use the latest available rate.
  If **no rate exists** for a currency/date, the value is flagged **"no rate"** in the UI — never silently 0
  or left unconverted.
- **Corrections invalidate caches.** Editing a manual rate or a revised auto rate invalidates every
  `base_amount` **and every precomputed rollup** derived from that `(currency, rate_date)`. `base_currency` is
  immutable in v1 (changing it would invalidate every stored base amount).
- **FX revaluation (decided):** a foreign balance's base value moves when rates move, with no transaction
  behind it. v1 does **not** do position-level FX P&L; instead net-worth change is decomposed into cash-flow +
  a distinct **"currency revaluation"** line so base-currency deltas stay honest (ADR-0017).
- Rates are pulled daily (Frankfurter/ECB) by the worker; **manual rate entry** is a first-class fallback.
- All conversions use `Decimal` with a documented rounding policy. Never mix currencies without conversion.

**Net worth correctness:** net worth = Σ(asset balances) − Σ(liability balances), using `accounts.is_asset`
(derived from type) rather than a sign guess. Each account balance is in its own currency and **converted to
base at that date's rate**. Snapshots are keyed on the provider's `balance_date` (not the worker's wall
clock), and the net-worth line **carries forward** the last known balance across days with no snapshot (sync
outages, stale accounts). SimpleFIN gives current balance only, so history builds forward from first sync —
there is no backfill (set that expectation in the UI). A manual investment account's balance is derived as
Σ(`holdings.market_value`) and snapshotted the same way (unless `balance_source='stated'` — see Investments).
- **Reconciliation with FX:** over any period, Δnet-worth (base) = cash-flow (base) + **currency revaluation**
  (the base-value change of foreign balances from rate moves). Reports show revaluation as its own line;
  single-currency views reconcile exactly, multi-currency views reconcile *including* that line.

### Identity & sharing
- **users** — `id, email (unique), password_hash (argon2), display_name, created_at, is_admin`.
- **households** — `id, name, created_at`. (Also called a "space". A user belongs to exactly one for v1.)
- **household_members** — `household_id, user_id, role (owner|member), joined_at`.
- **owners** — `id, household_id, name varchar(80), kind ('person'|'shared'), sort`. **Attribution labels, not
  logins** (ADR-0026): a child, a "House" pot, a partner who never opens the app. RLS-protected like every other
  household table; exactly one `kind='shared'` owner per household; name case-insensitively unique per household.
- **sessions** — **server-side** session table (decided, not "or cookie") so we get revocation /
  logout-everywhere and idle + absolute expiry. `id, user_id, csrf_token, created_at, last_seen_at, expires_at`.

**Signup is open (ADR-0027).** `POST /auth/signup` on a database with **no users** creates the household (that
signer becomes its `owner` member and `is_admin`); every later signup joins the **oldest** household as a
`member`. There are no invites — `POST /auth/invites` and the `invites` table are gone. Concurrent first
signups are serialized with a Postgres advisory transaction lock so exactly one household is created.
`METALMARK_OPEN_SIGNUP=false` closes signup; anyone who can reach the instance can otherwise join the household
(see §5).

### Accounts & connections
- **account_connections** — one per SimpleFIN claim.
  `id, household_id, provider ('simplefin'), access_url_encrypted, org_name, status (ok|auth_error|error|stale),
   last_synced_at, last_error, created_at`. `access_url_encrypted` holds the Basic-auth SimpleFIN URL,
  encrypted at rest (see §5).
- **accounts** —
  `id, household_id, connection_id (nullable → manual), external_id (SimpleFIN account id),
   external_key (org_id + account number/name, for reconnect remap — see §3), name,
   type (depository|credit|investment|loan|other), subtype, institution, currency (fixed; single-currency
   account per ADR-0017), current_balance, available_balance, balance_date, balance_source
   (stated|derived — investment accounts only; see Investments), is_asset (derived from type:
   depository/investment=asset, credit/loan=liability), owner_id (**NOT NULL** → `owners.id`; unset resolves to
   the household's Shared owner, server-side),
   is_manual (bool), is_hidden, created_at, updated_at`.
  - **Unique `(household_id, external_key)`** — the stable identity used by reconnect remap (§3); without it
    remap can't be correct.
  - **Ownership is a household label, not a user** (ADR-0026): a single `owner_id` per account plus a
    per-transaction and per-split owner. Fractional per-transaction ownership (`share_pct`) is deferred — it was
    over-modeled for a few users and created three overlapping ownership mechanisms. **Attribution precedence:
    `split.owner_id` → `transaction.owner_id` → `account.owner_id` → Shared**, computed by **one** authoritative
    helper (the chain is total: `account.owner_id` is never null, "Shared" is a real row). Deleting an owner
    requires a reassign target (default Shared); the FK is `NO ACTION` so a delete can neither silently rewrite
    attribution nor deadlock household deletion.
- **securities** — a reusable instrument. `id, household_id, name, ticker, security_type
  (stock|etf|mutual_fund|bond|option|crypto|cash|other), currency, is_manual`.
- **holdings** — a position in an account (SimpleFIN has no holdings object → always manual/imported).
  `id, account_id, security_id, quantity, cost_basis, as_of`. **Market value is derived**, not stored:
  quantity × latest `security_prices.price`, converted to base. **Cost basis is average-cost for v1**
  (ADR-0020); lot/FIFO tracking is deferred (no lot table). `cost_basis` has one writer per policy: if
  `investment_transactions` exist for the security, basis is computed from them; otherwise the manual scalar
  is authoritative (ADR-0020).
- **Investment account balance source** (`accounts.balance_source`, ADR-0021): `derived` = Σ(holding market
  values) — the default for manual accounts; `stated` = a provider/entered balance kept as-is. When a synced
  account (stated balance, no holdings) later gets hand-added holdings, the difference is reconciled by an
  explicit **"unaccounted cash" plug** holding rather than silently disagreeing.
- **security_prices** — `security_id, price_date, price, currency, source (auto|manual)`. Manual entry is
  first-class; auto price fetch is optional and pluggable. Prices carry an **age**; a stale price (older than a
  threshold) is flagged in the UI the way sync freshness is — a months-old manual price must not silently
  produce a wrong market value / net worth.
- **investment_transactions** — individual buy/sell/dividend/interest/fee/transfer events (fixes Monarch's
  "can't see investment transactions / no dividend category" gap).
  `id, account_id, security_id, type (buy|sell|dividend|interest|fee|split|transfer), trade_date, quantity,
   price, amount, currency, notes`. Dividends/interest flow into income reporting; buys/sells adjust cost basis.
  All ent(er)able by hand; sync/import populate the same table.

### Transactions
- **transactions** —
  `id, household_id, account_id, external_id (nullable for manual), import_hash (dedupe key for
   manual/CSV/OFX = sha256(account_id + transacted_at + amount + description + **occurrence ordinal**)),
   posted_at, transacted_at,
   amount (Decimal; sign: positive = money in), currency (**≡ account.currency**, denormalized + enforced),
   base_amount (Decimal; **cache** of amount converted to base at the txn's household-local date; recomputed
   on FX-rate change — never the source of truth), fx_rate_date,
   description (raw), merchant (cleaned),
   category_id, owner_id (**nullable = inherit** → account → Shared; effective owner via the one resolution
   helper, ADR-0026), is_pending, review_status (needs_review|reviewed|ignored), is_hidden, is_split_parent,
   transfer_group_id (nullable → part of a transfer), field_sources (jsonb), notes,
   source (simplefin|csv|ofx|manual), created_at, updated_at`.
  - Unique index `(account_id, external_id)` where `external_id` not null; unique `(account_id, import_hash)`
    where `external_id` is null. The **occurrence ordinal** in `import_hash` disambiguates two *genuine*
    identical same-day rows (two $5 coffees) so they aren't silently collapsed; a re-import of an
    already-seen row is routed to a **"possible duplicate — review"** step, never hard-dropped.
  - **`field_sources` = the provenance model (critical).** A per-transaction map
    `{category: provider|rule|user, merchant: …, owner: …, is_hidden: …, review_status: …, splits: …}`. It is the
    *only* way to distinguish a human edit from a rule- or provider-set value. **Write precedence:**
    `user` always wins; a `rule` may overwrite `provider` or an earlier `rule`, never `user`. Sync **resolves
    Shared explicitly** for `owner` on insert (never a NULL left for readers to resolve) and never overwrites a
    `user`-set owner.
  - **Manual-origin boundary (ADR-0019):** "provider-owned fields" is *not* static — it applies only to
    **provider-origin rows**. Sync merges **only into rows it owns**, matched by `external_id`; a manual/import
    row has no `external_id`, so **sync never overwrites a hand-entered amount**. If sync later brings what
    looks like the same transaction a user pre-entered, it lands as a separate row surfaced in the
    "possible duplicate" review — a human links/merges; sync never silently reconciles into manual data.
  - This is the **shared contract between WS-SYNC, WS-L (model), WS-R (rules)**, co-designed and frozen in P0.
- **transfer_groups** — links the two (or more) legs of a money movement (checking→savings, card/loan
  payments). `id, household_id, matched_by (auto|manual), fx_cost_base (nullable), created_at`. Auto-matched
  on ingest by near dates + cross-account (see §3). **All cash-flow/spend reporting excludes transfers.**
  - **Same-currency:** legs are opposite-sign, equal magnitude.
  - **Cross-currency (ADR-0018):** e.g. −100 EUR out, +108 USD in — legs are *not* equal in native amount, so
    matching is on **`base_amount` within a tolerance**, or explicit user linking. The residual
    (Σ base_amount of the legs ≠ 0) is the real FX spread/fee — stored as `fx_cost_base` and surfaced (as a
    fee/revaluation), **never hidden** by the transfer exclusion.
- **transaction_splits** — `id, parent_txn_id, amount, base_amount, category_id, owner_id, notes`; `owner_id`
  is nullable = inherit (ADR-0026).
  A split parent keeps `is_split_parent=true`; children carry the category/owner breakdown. Children **inherit
  the parent's currency and `fx_rate_date`**. Rounding policy: split the **native** amount to the cent
  (remainder to the largest child), then convert each child, allocating so **Σ(child `base_amount`) ==
  parent `base_amount`** exactly (no convert-then-round drift).
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
  - `actions`: `{ set_category_id?, add_tag_ids?, set_owner_id?, rename_merchant?, set_hidden?,
     mark_reviewed?, split?: [{amount|pct, category_id, owner_id}] }`.
  - **Shipped in M1a:** everything above except `split?`, which is deferred to the sync milestone (M2) with
    the rest of auto-split. The key sets are **closed** (`extra="forbid"`): a rule is stored as JSONB, so
    nothing in the database would reject a misspelled key, and `{"merchant_contain": "AMZN"}` would store
    cleanly and then match everything forever with no error anywhere. An unknown key is a 422 at write time.
  - A rule may write only fields a human has not already set — `field_sources[field] == "user"` is a hard
    stop (ADR-0007), and each field a rule does write is marked `"rule"`, so a later rule can improve it and
    a human still outranks both. Running the same rule set twice writes nothing the second time.

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
  - **Transfer matching**: within a household, pair a new txn with an opposite-sign txn in a *different*
    account within a few days → create/attach a `transfer_group`. Same-currency pairs match on equal
    magnitude; cross-currency on `base_amount` within tolerance (ADR-0018). Manual override in the UI.
  - **Balance snapshot**: upsert `balance_snapshots(account_id, balance_date, balance, currency)` keyed on the
    provider's `balance_date` (column is `balance`, consistent with §2).
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
- **CSV import**: preview → column-mapping UI → commit. `source='csv'`.
  - `POST /import/csv/preview` reads headers, a sample and a *suggested* mapping, and writes nothing. A file
    that could not be imported at all — too large, too many rows, no column that could carry a date and an
    amount — is rejected there, naming what is missing, rather than leading into a mapping UI that goes
    nowhere. Size (2 MB), row (5 000) and column (100) limits are enforced at the parse, so no path holds an
    unbounded file in memory.
  - `POST /import/csv/commit` takes the file *again* rather than an upload id: the parse is cheap and
    stateless, so there is nothing to store between the two calls and a stale id would be one more thing that
    can disagree with the bytes in front of the user. The mapping rides as a JSON string because its keys are
    the CSV's own header names.
  - **Dedupe is exact, and only exact.** A re-import of the same file is skipped on `import_hash`
    (`(account_id, import_hash)` unique, the race backstop). A *near* match — same amount, close date, same
    account — is **imported and flagged** `needs_review` as a possible duplicate, never merged and never
    dropped: two genuine identical charges are a real thing, and silently binning one is worse than a review
    prompt. The counts say which is which (`inserted` / `skipped` / `suspects`).
  - An owner column resolves **by name** (case- and padding-insensitive); an unknown name is a row error
    naming the name rather than a silent fallback to Shared. An unknown *category* name falls back to the
    commit-time default — a bank's category vocabulary is not the household's, and the balance is right
    either way.
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
- **Rules**: applied when a transaction is created and via "apply to existing" (batch). Ordered by
  `priority`, ties broken on `(created_at, id)` so the order is total and stable — `now()` is the
  *transaction* timestamp, so two rules written in one transaction share a `created_at` exactly and
  `created_at` alone would leave their order to the query planner. Deterministic and tested against a
  fixture set. Writes are provenance-gated (see §2); Monarch-style auto-split is deferred to M2.
- **Transfers, linked by hand**: `GET /transactions/transfer-candidates?txn_id=&days=` offers the rows that
  could be the other leg, `POST /transactions/transfers` links a pair, `GET /transactions/transfers/{id}`
  reads a group back whole, `DELETE /transactions/transfers/{id}` unlinks it.
  - **The exclusion is a property of the link, never of the rows.** Every report asks "is this in a transfer
    group?", so unlinking is the whole of the repair: the legs return to cash-flow and spending with no other
    trace, and the group row goes with them (a group with no legs is not a state this model has). That is what
    makes applying the exclusion safe at all.
  - Candidates are **not filtered** by whether they look like a match. Out-of-tolerance and no-rate rows are
    still offered and flagged (`within_tolerance: false`, `fx_cost_base: null`), because ADR-0018 keeps
    explicit user linking as the override and the ethos is never to hide a row. Every candidate is one the
    link endpoint would accept — the sign predicate is the same one.
  - `fx_cost_base` is Σ `base_amount` of the two legs, computed by the **same helper** the picker uses, so the
    residual shown before committing is by construction the one stored after. It is `null`, not `0`, when
    there is no residual to report (same-currency legs cancel exactly) or when a leg has no rate at all —
    ADR-0017's "no rate" is never silently a zero.
  - Ordering is **time-first** (nearest date, then nearest base-amount gap, then id): a transfer is matched
    within days, and the tolerance is a flag rather than the sort key. Same-currency legs are compared with no
    tolerance (there is no rate between them that could have moved); only cross-currency legs need one.
- **Owners / Shared Views**: attribution lives in the household's `owners` table — a **label, not a login**
  (ADR-0026) — referenced by `accounts.owner_id` (NOT NULL), `transactions.owner_id` and
  `transaction_splits.owner_id` (nullable = inherit). Effective owner = split → transaction → account →
  Shared, from the one resolution helper. Filters exist on `GET /accounts`, `GET /transactions` and the report
  endpoints, and the transaction filter derives the parent/child split from `EXISTS (child)` rather than
  trusting the denormalized `is_split_parent` flag. Authorization = household membership; ownership drives
  *views*, not access.
- **Owner filters do not mean the same thing in every report** (read this before building a UI that adds them
  up): `/reports/net-worth` filters **the accounts owned by X** — account-scoped end to end, including the
  cash-flow term of its `Δ net worth = cash flow + revaluation` decomposition, so that identity still holds —
  while `/reports/cash-flow` and `/reports/spending` filter **the rows attributed to X**, per split child, so
  Alex's report never totals Beth's share of a split charge. The two views are therefore **not additive**; the
  net-worth series carries `attribution: "account"` so the UI can label it honestly. Fractional ownership is
  the real fix and is deferred (ADR-0026).
- **Reporting** (ECharts, all reading the same `/reports` query API; **everything converted to base currency**
  at the correct-date rate, transfers excluded from cash-flow):
  - **A range is inclusive of both of its days, at timestamp precision.** `start`/`end` are calendar dates
    while `transacted_at` is a timestamptz, so the upper bound is written `< end + 1 day` and not `<= end`:
    Postgres reads the latter as `<= end 00:00` and silently drops everything posted later that same day —
    most of a day's activity, from every report, with the reconciliation identity still holding (revaluation
    is a residual and cannot notice a missing row). The lower bound needs no such care, since `>= start`
    already means midnight at the start of the first day.
  - Net worth over time (area/line from `balance_snapshots`), with the **currency-revaluation** contribution
    shown as its own component so multi-currency deltas are explained, not mysterious (ADR-0017).
  - **Cash-flow Sankey**: income sources → category groups → categories for a period.
  - Spending by category (donut) with click-through drill-down to a filtered transaction list.
  - Income vs. expense trend (stacked bar), month-over-month.
  - **Investment reporting** (addresses Monarch's weakest area): dividends/interest income, cost-basis vs.
    market value.
  - **Consolidated holdings / allocation view** (explicit feature request): a single page that aggregates
    every holding across *all* accounts — the same security held in multiple accounts is summed into one row
    (quantity, total market value, cost basis, gain/loss) — with **% allocation** of the whole portfolio, and
    toggles to group/weight by security, `security_type`, account, or currency. **Canonical valuation:**
    market value = quantity × latest `security_prices.price` in the **price's currency → converted to base**;
    a holding's own currency comes from its security, not its account. **Cash** is a `security_type=cash`
    holding so allocation %s are complete; a stale price is flagged. Allocation totals **reconcile to the
    net-worth investment total** (that's the real invariant, not "%s sum to 100").
  - Every chart is a lens over the same filter model (date range, accounts, owners, categories, currency).
- **Admin sync observability** (you're technical peers, so this is a feature not an afterthought): a per-run
  view of `sync_runs` + `sync_run_events` — trigger, duration, HTTP timing, counts (inserted/updated/
  reconciled/expired/matched), errors, and a sanitized event log. "Sync now" + per-connection history + the
  ability to see exactly which transactions a given run touched.

---

## 5. Security & ops

- **Secrets at rest**: `access_url_encrypted` uses authenticated symmetric encryption (libsodium secretbox /
  Fernet) with `METALMARK_SECRET_KEY` loaded from a **docker secret file, not an env var** (env leaks via
  `docker inspect`, crash dumps, child processes). Document rotation + re-claim path if the key is lost.
- **Authorization / tenant isolation (biggest ongoing risk)**: hand-written `household_id` filters on every
  endpoint are the classic source of cross-tenant (IDOR) leaks — one missed `.filter()` exposes another
  household. Enforce it in **one place, not per-handler**: Postgres **Row-Level Security** keyed on a
  per-session GUC, or a single mandatory query-scoping layer. The model is multi-tenant even with one
  household today. **The worker has no user session** yet moves the most cross-household data: it must **not**
  run `BYPASSRLS` (that removes the backstop) — each sync job sets `SET LOCAL app.household_id` for its
  connection's household inside the job transaction, so RLS applies to the worker exactly as to the API.
- **Tables outside RLS, and what that costs**: `users`, `households`, `household_members`, `sessions`
  (identity — a session has to be read before its household is known; ADR-0025), `fx_rates` (global
  reference data: the same fact for everyone) and `alembic_version` carry **no policy**. For these six, RLS
  is not merely absent — it is not a backstop at all: the app role holds ordinary
  `SELECT/INSERT/UPDATE/DELETE` on every one, deliberately, because it has to read `sessions` and write
  `household_members` in order to authenticate. What keeps them isolated is that each is reached by
  something the caller already proved it holds — a session token, or an id read out of a verified session —
  rather than by household. A future query that reaches them by *household filter* would be the leak, and
  nothing in the database would stop it.
- **Auth**: argon2id password hashing; httpOnly + SameSite session cookies; CSRF token for mutations;
  login rate-limit + lockout. **Signup is open (ADR-0027)** — the first signer creates the household (as its
  `owner` + `is_admin`), later signers join the oldest one as members; `METALMARK_OPEN_SIGNUP=false` closes it.
  No 2FA is an accepted risk given Tailscale only. (No invite landing page any more, so no `Lax` carve-out.)
- **Privacy caveat**: v1 ownership drives *views*, not *access* — any household member can read a partner's
  accounts. The UI must not imply privacy it can't enforce; truly private accounts are out of scope for v1.
  Open signup sharpens this: **anyone who can reach the instance can join the household and read all of it**,
  so the LAN/Tailscale boundary (§1, ADR-0002) is the only thing gating access — there is no app-layer invite
  step left to hide behind, and widening that boundary means setting `METALMARK_OPEN_SIGNUP=false` first.
- **Untrusted files**: OFX/QFX/CSV are user-supplied. Parse OFX/QFX with a **defused XML parser** (XXE /
  billion-laughs). Validate/limit CSV size and columns.
- **Transport**: Caddy TLS; reachable only over Tailscale/LAN. No public ingress.
- **PWA**: cache the app *shell* only; data endpoints are `no-store` — the service worker must never persist
  financial API responses on a (possibly shared) device.
- **Backups**: nightly `pg_dump` — which is cleartext PII — is **encrypted at the destination**, with a
  defined retention. Store the backup encryption key / `METALMARK_SECRET_KEY` **physically separate** from the
  dumps (a dump + co-located key = full compromise). Documented restore drill.
- **Failure alerting**: with no webhooks and 6h polling, an `auth_error` or a failed backup can silently rot
  data for days. Fire a notification (email/ntfy/Slack webhook) on `auth_error`, repeated sync failure, or
  backup failure — badges only help if someone looks.
- **Observability**: structured logs, `/healthz`, sync run history, Sentry optional.
- **Audit**: sensitive actions (connect/remove account, signup, owner rename/reassign/delete, rule change)
  written to `audit_log`.

---

## 6. Known risks (and where they're handled)

| Risk | Mitigation | Owner WS |
|---|---|---|
| SimpleFIN has no holdings/securities data | First-class manual holdings/prices/investment txns; balance-only auto for investment accounts | INV |
| TreasuryDirect coverage unreliable | OFX/manual fallback (already the plan) | IMP |
| Amounts are decimal strings; float drift | `Decimal`/`NUMERIC` everywhere; property tests | L, T |
| pending→posted id instability / disappearing pendings | `pending_reconcile` state + match by (amount,~date,desc); low-water mark, not fixed window; adversarial fixtures | SYNC |
| Unknown SimpleFIN rate limits | Scheduled polling, backoff, 1 concurrent req/connection | SYNC |
| Encryption key loss = dead connections | Key from docker secret; backup separate from db dumps; re-claim flow | OPS |
| Sync clobbering user edits | **Field-level provenance (`field_sources`), user > rule > provider** | SYNC, L, R |
| **Transfers double-counted → wrong Sankey/cash-flow** | `transfer_group` + auto-match on ingest; reporting excludes transfers | SYNC, L, UR |
| **Reconnect creates duplicate accounts/history** | Remap by `external_key`; ledger decoupled from connection (transactions survive removal) | SYNC |
| **Re-importing CSV/OFX duplicates rows** | `import_hash` unique index for manual/imported txns | IMP |
| **A date bound silently drops a day** (date vs timestamptz comparison) | Half-open upper bounds on every date-ranged query; regression test pins the whole end day (`test_report_range_covers_the_whole_end_day`); dates on the wire are built from local components, never `toISOString` | L, UR |
| **Cross-household (IDOR) data leak** | RLS or one mandatory scoping layer, not per-handler filters | P0, OPS |
| **FX rates are global, so one household's rate write is another's** | Accepted for a single-household install (ADR-0027); the boundary and the fix are stated in the currency model above | FX, L |
| **Open signup: anyone who can reach the instance joins the household** | LAN/VPN-only posture (ADR-0002) is the only gate; `METALMARK_OPEN_SIGNUP=false` before that boundary weakens (ADR-0027) | P0, OPS |
| **Owner filter read as if it meant one thing across all reports** | Documented asymmetry (account-scoped net worth vs row-scoped cash-flow/spending) + `attribution: "account"` on the series; fractional ownership deferred (ADR-0026) | L, UR |
| Silent stale data (no webhooks, 6h poll) | Notification on `auth_error` / sync / backup failure | SYNC, OPS |
| **Multi-currency mis-conversion / wrong net worth** | Single-currency accounts (ADR-0017); dated FX (latest ≤ date, else "no rate"); high-precision rates; `Decimal` + rounding policy; property tests over currencies | FX, L, UR, T |
| **Stale/corrected FX rate leaves stale reports** | `base_amount` is a cache; rollups + caches invalidated on `(currency, rate_date)` change; base_currency immutable | FX, OPS |
| **Unexplained net-worth vs cash-flow gap (FX revaluation)** | Decompose Δnet-worth = cash-flow + explicit currency-revaluation line (ADR-0017) | UR, L |
| **Cross-currency transfer unmatched / FX cost hidden** | Match on base_amount within tolerance or manual link; store + surface `fx_cost_base` (ADR-0018) | SYNC, L, UR |
| **Sync clobbers hand-entered rows** | Manual-origin boundary: sync merges only by `external_id`; manual rows untouched (ADR-0019) | SYNC, L |
| **Investment balance: Σ(holdings) vs stated disagree** | `balance_source` per account + "unaccounted cash" plug (ADR-0021) | INV |
| **Cost-basis unsupportable / double-written** | Average-cost v1; single authority (investment_transactions else scalar); lots deferred (ADR-0020) | INV |
| **Stale manual security price → wrong net worth** | Price age flag in UI (like sync freshness) | INV, UINV |
| **SimpleFIN payload doesn't fit the model, found late** | P0 sandbox spike on real demo token; fixtures derived from captured payloads; thin vertical slice in Phase 1 (ADR-0022) | T, SYNC |

---

## 7. Testing strategy (first-class — correctness is requirement #1)

Built as a workstream from day one (**WS-T**), not bolted on. Red-green discipline: a failing test defines
each behavior before the code. Fixtures are **derived from real SimpleFIN payloads captured in the P0 sandbox
spike** (ADR-0022), not hand-authored from assumptions.

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
  materialized daily balance + monthly category aggregate refreshed by the worker, not recomputed per request).
  **Rollups and `base_amount` caches are invalidated on FX-rate change**, keyed on `(currency, rate_date)`,
  not only on transaction change — otherwise a corrected rate leaves stale reports. Cache FX rates. Target API
  p95 < 150ms warm.
- **Frontend** — TanStack Query caching + prefetch; **virtualized** transaction list (react-virtual);
  **optimistic updates** on swipe/categorize so review feels instant; code-splitting per route; memoized
  ECharts with downsampled series for long ranges; PWA app-shell cache. Target: 60fps list scroll, review
  action < 100ms perceived.
- **Measured, not assumed** — a perf smoke test seeds ~50k transactions and asserts list + report latency
  budgets in CI.
