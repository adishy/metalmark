# Session 05 — Balance-model fixes (Phase A of the session-04 audit)

- **Date:** 2026-09-23
- **Agent:** Claude Code (Opus 5.5), advisor review on the plan before implementation.
- **Asked:** "build fix implementation plan from this audit, verify it with advisor, then start
  implementation" — and, mid-way, "please make all changes / migrations etc. with the assumption that the user
  has valuable data already present (i.e. not empty database)".
- **Branch:** `fix/net-worth-balance-model` (worktree `../metalmark-nw`, off `241419a`; the main checkout's
  uncommitted login work untouched).
- **Outcome:** commits `3b3fcd8` (A2), `b4ed041` (A3), `7cbf540` (A1), `3b0342b` (ADRs 0043/0044), then a
  final-review commit (recompute through `record_balance`, portfolio fallback, migration preview script).
  Phases B and C of the plan are not started.

---

## The plan, and what the advisor changed

Phase A = the audit's three confirmed swing sources: #1 liability sign, #2 derived investment accounts with
no positions, #4 balance edits rewriting history. Advisor revisions adopted:

- **Order A2 → A3 → A1**, one migration per commit — A2/A3 don't depend on the sign question.
- **A1's data rule is per account, not "negate every positive on a liability"** — a blanket rule would
  create the swing it removes if any bridge sends positive debt. Never-synced → negate positives; synced →
  only if it also has negatives; synced and never negative → untouched, plus a sync warning.
- **A2's migration seeds one snapshot** for each account it switches, or the switch recreates the
  first-snapshot cliff.
- **A3 is one helper for every writer** (`ledger.record_balance`): the dialog, sync and OFX all had the bug.
- Invariant "series at today == headline" asserted in base currency only (the headline's FX date is a
  separate, known gap).

## Live-data constraint — how it shaped the migrations

- Each migration (0006, 0007) records every value it changes in a `migration_backup` schema the app role has
  no grant on (0001's default privileges cover `public` only; `alembic check` ignores it).
- Downgrades restore only rows still holding what the migration wrote, so a balance synced or edited after
  the upgrade is never reverted.
- `tests/integration/test_migrations_live_data.py` (new) migrates a scratch DB to the prior revision, seeds
  the shapes to change *and* to leave alone, upgrades, checks, downgrades, and compares row for row.
- 0006/0007 are data-only (0005 asked for 0001's DDL to be frozen before any schema-changing 0006; not done,
  not needed here).

## Decisions made along the way

- **An opening balance is an observation only when given.** Found via two OFX tests: the create form's
  default `0` was snapshotted at today, which both dropped the chart to $0 and (under the new "only move
  forward" rule) would have blocked an imported statement from becoming current. The form field is now blank
  by default; the API snapshots only a supplied balance.
- **Retyping into `investment` makes the account `stated`**; leaving `investment` is refused while positions
  exist.
- **`available_balance` is not sign-converted** — for a card SimpleFIN reports available credit, positive in
  both conventions.
- **SimpleFIN sign evidence:** the spec is silent on balance sign; Actual Budget's SimpleFIN integration
  stores it unchanged and shows cards negative. Recorded in ADR-0043 as corroboration, not proof.
- **ADRs 0043/0044** were written `Proposed` and accepted by the owner at the end of the session.

## Verification

- Backend: 861 passed (baseline 835), ruff clean, `alembic check` clean against a migrated DB.
- Frontend: typecheck, design-lint, build clean; vitest 313 passed + **14 pre-existing failures in
  `lib/notify.test.ts`** (present on the untouched baseline too).
- OpenAPI: regenerated in-process; the only difference was `AccountUpdate.type` (+6 lines).
- **Not run:** Playwright e2e, the CI restore drill, `verify.sh prod`.
- Pre-existing, not fixed: `test_imports.py::test_preview_and_commit_over_multipart_http` fails when run
  after `test_ofx.py` in one session (open signup joins the oldest household, so it sees earlier tests' rows);
  passes in the full-suite order and alone.

## Final review — what it added

- **Upgrade rehearsal with real writers.** A scratch DB was migrated to 0005 and filled by the *old* code
  (`241419a`, in a second worktree): `app.seed --demo` (manual card stored as +850 owed) plus one sync of the
  demo capture with a card at −1000 (the $114,685.51 savings account created `derived` with no snapshot).
  Upgraded with the new code: card −850, synced card untouched at −1000, savings `stated` with one seeded
  snapshot, and the series equal to the headline over the USD accounts ($174,947.32). Downgraded to 0005:
  `accounts` and `balance_snapshots` byte-identical to before, backup schema gone.
- **The deployment migrates on every `docker compose up`** (its `migrate` service runs `alembic upgrade
  head`), so pulling the image applies 0006/0007 immediately. Hence `scripts/preview_balance_migrations.sql`:
  read-only, prints every row either migration would change (now → after), and the liabilities 0007 leaves
  alone. Run on the rehearsal DB it listed exactly what the upgrade then did.
- **`balance.liability_positive` does not badge or notify:** run status comes from the provider's `errlist`
  and notifications from connection-failure transitions, so the warning is a run-log line only.
- `investments.recompute_derived_balance` now goes through `record_balance` (it set the columns directly and
  used server-local `date.today()`); `ledger.today()` is the one "today".
- The Investments view applies the same no-position fallback, so a typed-balance investment account shows
  its balance (as unaccounted cash) instead of $0.
- `scripts/backup.sh` dumps the whole database, so `migration_backup` survives backup/restore.

## Deploy procedure for an instance with real data

1. `scripts/backup.sh` — an encrypted dump, before anything else.
2. `docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <
   scripts/preview_balance_migrations.sql` — read every row it lists; the third section is the accounts that
   will still count in the household's favour.
3. Pull and `docker compose up -d` (migrates on the way up).
4. After the first sync, check Admin → run log for `balance.liability_positive`, and retype any card listed in
   the preview's last section.

Backend after the review: 862 passed.

## Phases B and C (same session, after the owner accepted 0043/0044)

Asked: "both make sense - accept, implement, continue with phase b, c". Plan reviewed by the advisor first;
its changes adopted: hidden rows **included** in the backward derivation (dismissed duplicates are deleted,
not hidden — checked in `TxnDetailSheet`/`Review`), investment accounts **not** derived backwards, a separate
`no_balance` reason, C1 treated as the one step that rewrites stored data, C4 must not split merged rows,
7-day (not 3-day) staleness.

| Commit | What |
|---|---|
| `abc8bcf` | ADR-0043/0044 accepted |
| `6104332` | B: `_BalanceHistory` — balance before the first snapshot derived backwards; per-point `missing` (`not_started`/`no_balance`/`no_rate`/`no_price`); unpriced positions out of appreciation (ADR-0045, Proposed) |
| `b3255e8` | series stops at today (the "this year" default drew a flat line to December); headline at today's rate; `cash_flow_series` loads once |
| `c97324f` | chart: time axis, straight segments, partial points hollow/amber with tooltip + note naming account and reason |
| `687e7a1` | stale synced accounts (`stale_since`, `account.not_reported`); same-named accounts in one payload kept apart, merged rows reported not split |
| `538d208` | daily FX fetch (Frankfurter v2, prod-only by default) + "most recent rate wins either direction" (ADR-0046, Proposed) |

Found along the way: ECharts' line default symbol is hollow (so partial points were indistinguishable until
`symbol: "circle"`); the dev stack's default report window runs into the future; `upsert_fx_rate`
recomputes every foreign transaction per call (why the fetch batches); `recompute_base_amounts` never
touched `transaction_splits.base_amount` (pre-existing, recorded in ADR-0046).

**Verified:** backend 887 passed; vitest 320+ passed with the same 14 pre-existing `notify.test.ts` failures;
typecheck/design-lint/build clean; OpenAPI regenerated in-process; the chart rendered in Chromium against a
dev stack of this branch and checked by eye (screenshots at rest, on a partial point); **Playwright 53/53**
(sync specs need `METALMARK_SIMPLEFIN_PROVIDER=fake`, as CI sets).

**Deferred, with reason:** `balance_snapshots.source` — a schema change, and 0005 requires freezing 0001's
live-metadata DDL first; not needed by anything shipped.

**Final review (advisor) — fixed before reporting:**
- **C4:** an alike-named row that neither card in the payload owns (a reconnect with re-minted ids) keeps
  the plain key. Otherwise two new accounts appeared beside an orphan still carrying its balance.
- **FX:** the worker recomputes every household's cached amounts once at start, because the resolution
  rule changed. The preview script lists pairs stored both ways round.
- **Splits:** `recompute_base_amounts` now re-allocates split children. Before, a rate arriving after a
  split left them at `None`, out of cash flow.
- Backend: 890 passed.

Known and left:
- **Reconnect-by-name:** accounts given `key:<provider id>` lose it; after a reconnect their old copies
  must be hidden or deleted by hand.
- **Stale accounts under a deleted connection** are not flagged.
- **Window caption:** `/reports/net-worth` still echoes the requested `end`, so the page caption can say
  "to Dec 31" while the series stops at today.

**Deploy notes added by B/C:** the first worker start in prod fetches rates and rewrites cached
`base_amount`s (logged as `fx.refreshed … base_amounts_changed`); undo by deleting `fx_rates` rows with
`source='auto'` and recomputing. The worker now makes an outbound call to `api.frankfurter.dev`
(`METALMARK_FX_FETCH=false` to disable).

## Pull-and-restart deploys (ADR-0047, Proposed)

**Asked:** the user said "there should be some startup flow that can verify things (that doesn't leak PII in
worker logs etc)", and "we can't assume clients have the codebase, just the image — they'll just pull the
latest images and restart, everything should happen automatically (this is an ADR)". Adding a container or
changing compose was allowed.

**Built:**
- **`app/services/checks.py`:** seven data checks.
  - Each runs in its own savepoint. A crash becomes a `fail` result that names only the exception type.
  - `GET /checks` serves them.
  - Admin → **Data checks** shows them, with account names.
- **Worker `startup_checks`:** runs the FX fetch (when on), then `recompute_all`, then the checks for each
  household.
  - It logs `checks.schema` and `checks.completed`: statuses and counts only.
  - The daily fetch no longer also fires at start.
- **`app.worker.failure(exc)`:** every exception the worker logs now carries only the type and the
  file:line. That includes a crashed sync job, whose full message stays on its run row.
  - **Why:** a SQLAlchemy error message includes the statement's parameters, which are names and amounts.
- **Deploy compose `backup` service:** `postgres:16` running `pg_dump -Fc` into `db_backups` or
  `METALMARK_BACKUP_DIR`.
  - The file is written as `.partial` and renamed; mode 600; the newest 10 are kept.
  - `migrate` depends on it.
- **`scripts/preview_balance_migrations.sql` deleted.** The ADR-0044/0046 references and the README now point
  at ADR-0047.

**Verified:**
- **Automated gates:**
  - Backend: 896 passed; ruff clean; contract regenerated.
  - Frontend: vitest shows only the 14 known `notify.test.ts` failures; typecheck, design lint and build are
    clean.
- **Checks tests:**
  - A mixed-currency household (a divided inverse rate, a hidden account) passes `headline_matches_chart`
    exactly.
  - A crashing check leaks no message to the log.
- **Local prod stack**, project `metalmark-nw`: the deployment file on the GHCR `latest` images, demo seed
  plus an old-code sync.
  - Before the upgrade: headline 179,169.54, chart today 64,614.75, liabilities −150.
  - Then image tags were switched to local builds of this branch, followed by `up -d`.
  - The steps ran in order: backup written, then migrations 0006 and 0007, then the FX fetch (264 EUR
    rates), then `checks.completed result=ok`.
  - After the upgrade: headline = chart = 177,300.26; liabilities 1,850.
  - No account name or amount appears in the worker or api logs (grepped every account name).
  - Backup rotation (10 kept) and `pg_restore --clean --single-transaction` were both tested.
  - Playwright against the TLS door: 50/53. The 3 failures are the sync specs, which need the fake provider;
    prod refuses it, so the claim returns 502. That is expected in prod.
  - The Admin Data checks card was screenshotted: all ok, "Schema 0007".
