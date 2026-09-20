# ADR 0026: Owners are household data, not users

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Supersedes:** ADR-0012 (**ownership half only** — "ownership drives views, not access" still stands)
- **Related:** ADR-0013/0027, ADR-0014, ADR-0025 (RLS mechanism), ADR-0007 + ADR-0019 (provenance),
  ADR-0017 (report identity), ARCHITECTURE.md §2, §4, §5

## Context

ADR-0012 assigned ownership with `owner_user_id` columns pointing at real `users` rows, and invite-only signup
(ADR-0013) meant every attribution label was also a login. That conflates two different things: **who can log
in** (an email, a password hash, a session) and **whose money is this** (a label). A household legitimately
needs labels with no login behind them — a child, a "House" pot, a partner who never opens the app — and the
old model required minting a user *and* an invite for each one. Open signup (ADR-0027) makes the conflation
worse rather than better: an attribution label would literally *be* a login to the household's financial data.

## Decision

- **Owners become plain household data.** A new household-scoped `owners` table —
  `id, household_id, name varchar(80), kind ('person'|'shared'), sort` — protected by the household RLS policy
  like every other household table (ADR-0014/0025). Two constraints carry the semantics: a unique index giving
  **exactly one `kind='shared'` owner per household**, and a case-insensitive-unique name per household.
- **"Shared" is a real row, not NULL.** A null cannot be renamed, sorted or filtered on, and every consumer
  would re-implement the same `IS NULL → "Joint"` special case — a second, divergent definition of "shared"
  living in the UI, in reports, and in rules. As a row, every attribution is an owner reference and the owner
  filter is a plain equality. The cost: Shared is the codomain of a total function and cannot be deleted (the
  API refuses — a household always has one).
- **Attribution points at `owners.id`**: `accounts.owner_id`, `transactions.owner_id`,
  `transaction_splits.owner_id`. The `owner_user_id` columns are dropped.
- **Nullability is asymmetric on purpose.**
  - `accounts.owner_id` is **NOT NULL**. Unset ⇒ the household's Shared owner, resolved server-side. This keeps
    the attribution chain **total** (it always terminates at a real row) and keeps the account filter a plain
    equality instead of a `NULL OR join-to-shared` dance. Cost: a client cannot tell "explicitly Shared" from
    "never set" — accepted, nothing behaves differently.
  - `transactions.owner_id` and `transaction_splits.owner_id` are **nullable = inherit**: effective owner =
    `split → transaction → account → Shared`. **One** authoritative helper computes this; no caller
    re-implements the precedence.
- **Deleting an owner takes a reassign target** (defaulting to Shared). The FK is deliberately **NO ACTION**:
  `ON DELETE SET NULL` would silently turn "Alex's charge" into "inherit" — attribution changed by a delete,
  with nothing recording that it changed — and `ON DELETE RESTRICT` would make an owner row permanently
  undeletable while any row references it, which deadlocks household deletion (removing a household has to
  remove its owners). The reassignment is an explicit application step; a bare `DELETE` with references fails
  loudly instead of rewriting history.
- **Owner filters** exist on `GET /accounts`, `GET /transactions` and the three report endpoints. Filtering
  transactions must **not** trust the denormalized `is_split_parent` flag: the parent/child split is derived
  from `EXISTS (child)`, so a stale flag cannot double-count or silently drop a split parent's amount.
- **Report attribution is asymmetric, and the two views are not additive.**
  - `/reports/net-worth` — an owner filter means **"the accounts owned by X"**: applied account-scoped end to
    end, *including* the cash-flow term of its `Δ net worth = cash flow + revaluation` decomposition, so that
    identity still holds inside the filtered view.
  - `/reports/cash-flow` and `/reports/spending` — the same filter means **"the rows attributed to X"**,
    evaluated per split child, so Alex's report never totals Beth's share of a split charge.
  - Summing per-owner spending therefore does **not** equal the net-worth movement for the same owners: one
    view is account-scoped, the other row-scoped. The net-worth series carries `attribution: "account"` so the
    UI can say "Accounts owned by X" instead of implying row-level attribution. Fractional ownership
    (`share_pct` — the actual fix for "half the mortgage is mine") is deferred; this asymmetry is the honest
    cost of not having it, and papering over it in the docs would just move the surprise into the UI.
- **Sync (Phase 2/3) resolves Shared explicitly on insert** and **never overwrites a human-set owner**. The
  provenance key stays `"owner"`, values `user|rule|provider` (ADR-0007/0019). Resolving at ingest — rather than
  storing NULL and letting every reader resolve — freezes the decision when the provider row arrives, so a
  later change to the account's owner does not retroactively re-attribute an already-synced charge.
- **Display-name drift after a rename is accepted.** Renaming an owner deliberately relabels every historical
  row that references it — that is what a label is, as opposed to a user, and it is why the name is not
  denormalized onto rows. Anything that snapshotted the *name* rather than the id (an export taken earlier, an
  audit entry, a client-side cache) shows the old name until it is regenerated.
- **Migration `0002`** adds `owners` and drops the `owner_user_id` columns, idempotently and shape-detecting:
  `0001` builds the schema with `Base.metadata.create_all` from *live* ORM metadata, so a brand-new database
  already comes up in the post-0026 shape and never has `invites` or `owner_user_id` at all. `0002` therefore
  asks `information_schema`/`pg_catalog` what it is looking at and backfills legacy rows **only** when the old
  columns actually exist.

## Consequences

- **Positive:** attribution is household data — adding a kid, a "House" pot or a partner costs a row, not a
  login, and can no longer become an access-control mistake; one resolution helper instead of a precedence
  re-implemented per query; a total attribution function removes a class of NULL-handling bugs from filters,
  splits and reports (no "unknown owner" state to render); an owner rename is a single `UPDATE`.
- **Negative / costs:** two attribution semantics coexist in reporting and are deliberately not additive (see
  above); the Shared row is special-cased in constraints and is undeletable; deleting an owner is a two-step
  reassign rather than a one-click delete; every consumer of accounts/transactions/splits joins `owners` to
  display a name (the alternative — denormalizing the name — buys a join and pays with the drift above).
- **Residual debt: `0001` is not a frozen schema snapshot.** It runs `create_all` against live ORM metadata, so
  replaying migrations from zero never reproduces the schema as of any past date — `0001`'s meaning drifts with
  the code, and today it already produces the post-0026 shape. `0002` is what makes the two paths converge,
  which is exactly why it has to stay shape-detecting and idempotent. Converting `0001` to literal DDL is owed
  before Phase 2 writes real data.
- **Follow-ups:** `0002` is exercised both ways — a fresh database built by `0001`, and a legacy database with
  `owner_user_id` (rehearsed against a pre-rename dump: `upgrade head` → Shared exists, owners remapped, no NULL
  account owner → `downgrade 0001` → `upgrade head` is a fixed point). Deleting an owner with **no** target
  reassigns to Shared; with one it must leave no orphaned references, and the FK would reject it if it did.
  Acceptance bar: report filters behave as documented above (see PLAN.md). Fractional ownership, if it is ever
  needed, is a new superseding ADR, not a patch to this one.
