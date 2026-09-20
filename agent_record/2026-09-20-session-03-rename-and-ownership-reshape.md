# Session 03 — MetalMark rename, ownership reshape, open signup

- **Date:** 2026-09-20
- **Agent:** Claude Code — orchestrator plus parallel subagents (backend, frontend, docs/contract/CI). Each
  session owned a disjoint tree; nobody edited another's files.
- **Working dir:** `/Users/adishy/personal.data.adishy.com/tmp/monarch_clone`
- **Outcome:** commits `1741ede` + `fd9f882` (usable M1a, backend + frontend), `f0938ac` (Kestrel → MetalMark),
  `c8d5a58` (visual-regression tolerance). In the working tree at write time: the **ownership reshape + open
  signup** (ADR-0026/0027) across backend, frontend, `contracts/openapi.yaml`, docs and CI.

> Written by the docs/contract/CI agent from the commits and the repo state; the backend/frontend subagents'
> narration is summarized from their commit messages. Newest material last.

---

## 1. Rename: Kestrel → MetalMark (commit `f0938ac`)

A **behavior-neutral sweep**, verified by running the suites either side of it: 17 pytest (ruff clean),
19 vitest, `tsc`, `vite build`, 8 Playwright e2e — same results before and after.

- **What moved:** env prefix `KESTREL_*` → `METALMARK_*` (11 vars); OpenAPI title; package names
  (`backend/pyproject.toml`, `frontend/package.json` + lockfiles); the **session cookie**
  (`kestrel_session` → `metalmark_session`, so existing sessions log out — intended, not a bug to chase);
  Postgres database/owner/app roles and their dev passwords; the Fernet secret file
  (`secrets/metalmark_secret_key`, same key content); the Caddy site address var; an explicit compose project
  name (`metalmark`), so the network and the Playwright runbook no longer depend on the checkout's directory
  name; frontend brand text normalized to "MetalMark"; the login visual baseline regenerated; README,
  ARCHITECTURE, PLAN, `frontend/e2e/README.md`.
- **The local dev database survived.** It was dumped first (`pg_backups/legacy-dev-2026-09-20.dump`), recreated
  under the new names, and restored **data-only** on top of the migrated schema — same rows, correct app role
  and RLS policies. Renaming still costs data if you rename first and dump second; dump first.
- **Decision — accepted ADRs and `agent_record/` keep saying "Kestrel".** The repo's own rule
  (`docs/adr/README.md`) is that accepted records are immutable history, so ADRs ≤0025 and every session file
  before this one were deliberately left as written; `docs/adr/README.md` carries the name mapping
  (`KESTREL_SECRET_KEY` reads as `METALMARK_SECRET_KEY`, and so on). New records use MetalMark. The cost is
  real and accepted: a reader of an old ADR has to apply the mapping in their head — cheaper than rewriting
  history and losing the trail of what the decision was actually written against.

## 2. Usable M1a (commits `1741ede`, `fd9f882`)

- **Backend:** `transactions.owner_user_id` (nullable, precedence split → txn → account → joint) wired through
  create/update/serialization with provenance; `GET /household` + `GET /household/members` (owner dropdowns,
  settings); `DELETE` for categories, category-groups and tags. OpenAPI re-exported.
- **Frontend:** accounts CRUD (+ per-account currency/FX, owner + filter bar, net-worth header); transactions
  filter bar / create+edit / detail sheet (splits, tags, transfers); settings (household, categories, tags, FX
  rates, members); shared `form.tsx` / `Dialog.tsx` / `TxnDetailSheet.tsx` primitives with unit tests; a
  `usable-m1a.spec.ts` e2e covering edit+owner, splits, category CRUD and FX.
- **Note carried forward:** `1741ede` added an owner column with **no migration** — the `0001` baseline builds
  the schema with `create_all` from live ORM metadata, so dev/test/CI (which recreate cleanly) absorb it. That
  is the same debt ADR-0026 now records explicitly as residual: `0001` is not a frozen schema snapshot.

## 3. Review-deck and visual-baseline fixes (`fd9f882`, `c8d5a58`)

Three separate bugs found while getting the suites green, all worth remembering because each one was *silent*:

1. **Swipe deck skipped cards.** `Review.tsx` decided by array **index**, but the query refetch drops the
   decided transaction from the list — so every approval also skipped the card that slid into the vacated slot.
   Fixed by tracking decided **ids**. A test that only checked "did N approvals happen" would never have caught
   it; only "did *these* transactions get decided" does.
2. **`review.spec.ts` assumed its own card was on top.** The queue is shared and date-ordered, so a pre-existing
   backlog broke the test (which then read as a product bug). Fixed by draining to the target card
   (`helpers.reviewTarget`), and by stopping `usable-m1a.spec.ts` from leaving uncategorised (`needs_review`)
   rows behind for the next test.
3. **The visual tolerance was wide enough to hide a real change.** `maxDiffPixelRatio: 0.01` on a 1280×800 page
   permits 10,240 differing pixels — enough to swallow a visible text change: the `metalmark` → `MetalMark`
   brand fix passed against the *old* baseline without rewriting it. Now `maxDiffPixels: 100` (rendering in the
   pinned Playwright container is deterministic — the same test passes at `0`), so the loose ratio was only
   ever hiding regressions.

Also in `fd9f882`: the worker service's healthcheck is disabled in compose — it serves no HTTP port, so the
image's `/healthz` check was marking it unhealthy.

## 4. Ownership reshape — decisions (ADR-0026)

Owners stop being `users` and become plain household data. Full reasoning in the ADR; the load-bearing points:

- New household-scoped `owners` (`id, household_id, name varchar(80), kind person|shared, sort`), RLS-protected
  like every other household table; exactly one `kind='shared'` per household; case-insensitive-unique name.
- **"Shared" is a real row, not NULL** — a null can't be renamed, sorted or filtered on, and every consumer
  would re-implement the same `IS NULL → "Joint"` special case.
- `accounts.owner_id` is **NOT NULL** (unset ⇒ Shared, resolved server-side) so the attribution chain is total
  and the account filter stays a plain equality; `transactions.owner_id` / `transaction_splits.owner_id` are
  **nullable = inherit** (`split → transaction → account → Shared`) via **one** helper.
- Deleting an owner requires a reassign target (default Shared); the FK is **NO ACTION** — `SET NULL` would
  silently turn "Alex's charge" into "inherit", `RESTRICT` would deadlock household deletion.
- **Report attribution is deliberately asymmetric and the two views are not additive**: `/reports/net-worth`
  filters *the accounts owned by X* (account-scoped, including the cash-flow term of `Δ net worth = cash flow +
  revaluation`, so the identity holds), while `/reports/cash-flow` and `/reports/spending` filter *the rows
  attributed to X* (per split child). The net-worth series carries `attribution: "account"` so the UI can say
  so. Fractional ownership (`share_pct`) is the real fix and is deferred — this asymmetry is the honest cost of
  not having it.
- Sync (Phase 2/3) must **resolve Shared explicitly on insert** and never overwrite a `user`-set owner;
  `field_sources` key stays `"owner"` with values `user|rule|provider`. Display-name drift after a rename is
  accepted (a label relabels history by design).

## 5. Open signup replaces invite-only (ADR-0027)

- `POST /auth/invites`, the `invites` table and the invite token in signup are **gone**.
  `POST /auth/signup {email, display_name, password, household_name?}`: empty users table ⇒ create the
  household (signer becomes its `owner` member and `is_admin`); otherwise join the **oldest** household as a
  `member`. Concurrent first-signups are serialized with a Postgres advisory transaction lock (without it, two
  first signups each see an empty table and create a household). `METALMARK_OPEN_SIGNUP=false` closes signup.
- **The trade-off, plainly:** anyone who can reach the instance can create an account and join the household —
  and since ownership drives *views*, not *access*, that account can read every account, transaction and
  balance. Acceptable **only** because the API is LAN/Tailscale-only (ADR-0002): the network is now the sole
  gate, with no app-layer invite step behind it. Anyone who exposes the API or widens the tailnet must set
  `METALMARK_OPEN_SIGNUP=false` first.

## 6. Docs, ADR index, and the drifting OpenAPI spec

- **ADR index:** 0012 → `Superseded by 0026` (ownership half) and 0013 → `Superseded by 0027` (invite half) in
  both the status lines and the index; 0026/0027 added as Accepted. Both older ADRs' text is untouched.
- **`agent_record/README.md`'s index was missing the session-02 entry** (the file existed since
  2026-09-19 but was never indexed — the index only listed session-01). Fixed here, with session-03 added. The
  index is the only way to find these records, so an unindexed session is effectively lost.
- `docs/ARCHITECTURE.md` §2 (identity/sharing, accounts, transactions, splits, rules actions), §4 (owner model,
  filter semantics, the non-additivity caveat), §5 (auth, privacy consequence, audit) and two risk rows;
  `docs/PLAN.md` (Phase 0 auth bullet, exit criteria, L/UH/SYNC rows, M1a, L acceptance bar, deferred list);
  `README.md` product-decision rows for sharing and auth.
- **CI:** `/owners` and `/household` added to the unauthenticated-401 loop, plus a new **OpenAPI drift check**
  that compares `contracts/openapi.yaml` to the live `/openapi.json` semantically (YAML vs JSON, key order
  irrelevant) and fails with a per-path diff. The checked-in spec was decorative until now — nothing compared
  it to the served API — and **the new check immediately found real drift**: 32 spots where the file says
  `MetalMark Session` and the served spec says `Metalmark Session`, a casing artifact of the rename that had
  gone unnoticed. That is precisely the rot the check exists to catch; the spec is being regenerated as part of
  this reshape, and the check is expected to pass against the regenerated file.

## 7. Follow-ups

- Regenerate `contracts/openapi.yaml` from the reshaped API (the drift check gates it from here on).
- `0002` must be tested both ways: on a legacy DB (`owner_user_id` present → backfill) and on a fresh DB that
  `0001` already builds in the new shape. Convert `0001` to literal DDL before Phase 2 writes real data.
- Owner delete without a reassign target must fail; with one, must leave no orphaned references.
- `METALMARK_OPEN_SIGNUP` belongs in `.env.example` and the ops notes; signup should be recorded in `audit_log`
  as a sensitive action (ADR-0027 follow-ups).
