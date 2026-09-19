# ADR 0019: Provenance — the manual-origin boundary for sync merges

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0007 (refines), ADR-0010, ARCHITECTURE.md §2, §3

## Context

ADR-0007's write-path contract lists `amount/description/posted_at/is_pending` as "provider-owned" fields sync
may overwrite. A review noted this is wrong for **manually-entered** rows: a manual txn's amount is *user*
data. "Provider-owned" is not a static field set — it depends on who created the row. Manual parity
(ADR-0010) makes manual rows first-class, so this conflict is live, not hypothetical.

## Decision

Refine ADR-0007: the "provider-owned fields" rule applies **only to provider-origin rows**. Concretely:
- **Sync merges only into rows it owns**, matched by `external_id`. A manual/import row has no `external_id`,
  so **sync never targets or overwrites it** — a hand-entered amount is safe.
- If sync later brings what appears to be the same transaction a user pre-entered, it lands as a **separate
  row** surfaced in the "possible duplicate — review" step. A human links/merges; sync never silently
  reconciles into manual data.

ADR-0007's precedence (`user` > `rule` > `provider`) stands unchanged; this only bounds what "provider may
overwrite" means.

## Consequences

- **Positive:** manual data is never clobbered by sync; the provenance contract is well-defined for every row
  origin; makes "sync is just another writer" safe.
- **Negative / costs:** a pre-entered txn that later syncs produces a duplicate needing manual merge — an
  explicit review step, not silent magic (acceptable; the alternative is fuzzy auto-merge into user data).
- **Follow-ups:** co-design with the manual path in P0; test that a manual amount survives a colliding sync.
