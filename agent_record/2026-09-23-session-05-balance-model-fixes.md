# Session 05 — Balance-model fixes (Phase A of the session-04 audit)

- **Date:** 2026-09-23
- **Agent:** Claude Code (Opus 5.5), advisor review on the plan before implementation.
- **Asked:** "build fix implementation plan from this audit, verify it with advisor, then start
  implementation" — and, mid-way, "please make all changes / migrations etc. with the assumption that the user
  has valuable data already present (i.e. not empty database)".
- **Branch:** `fix/net-worth-balance-model` (worktree `../metalmark-nw`, off `241419a`; the main checkout's
  uncommitted login work untouched).
- **Outcome:** commits `3b3fcd8` (A2), `b4ed041` (A3), `7cbf540` (A1), `3b0342b` (ADRs 0043/0044).
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
- **ADRs 0043/0044 are `Proposed`**, pending the owner's review.

## Verification

- Backend: 861 passed (baseline 835), ruff clean, `alembic check` clean against a migrated DB.
- Frontend: typecheck, design-lint, build clean; vitest 313 passed + **14 pre-existing failures in
  `lib/notify.test.ts`** (present on the untouched baseline too).
- OpenAPI: regenerated in-process; the only difference was `AccountUpdate.type` (+6 lines).
- **Not run:** Playwright e2e, the CI restore drill, `verify.sh prod`.
- Pre-existing, not fixed: `test_imports.py::test_preview_and_commit_over_multipart_http` fails when run
  after `test_ofx.py` in one session (open signup joins the oldest household, so it sees earlier tests' rows);
  passes in the full-suite order and alone.
