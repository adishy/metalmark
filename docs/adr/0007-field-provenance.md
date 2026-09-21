# ADR 0007: Field-level provenance governs sync-vs-human writes

- **Status:** Accepted; the provider-owned field list below is widened by ADR-0028, and the rest stands.
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0008, ADR-0010, ARCHITECTURE.md §2, §3

## Context

Three actors write the same transaction columns: the provider (sync), the rules engine, and the human. If we
only null-check, we can't tell a rule-set category from a human-set one — so re-sync either freezes categories
(rules never improve) or silently overwrites human corrections. Both are silent data corruption.

## Decision

Every transaction carries a **`field_sources`** map tagging each editable field's origin
(`provider | rule | user`). **Write precedence: `user` always wins; a `rule` may overwrite `provider` or an
earlier `rule`, never `user`; sync may overwrite only provider-owned fields** (`amount, description,
posted_at, is_pending` — **a floor, not the whole list: ADR-0028 widens it with `transacted_at`, which is the
date field a provider actually supplies, and taken literally this list would clobber a human's corrected
date on every run**)
and never a `user`/`rule` field. This is a shared contract co-owned by WS-L (model),
WS-R (rules), and WS-SYNC (sync), frozen in Phase 0.

## Consequences

- **Positive:** human edits are durable; improved rules still re-apply over provider/rule values; makes "sync
  is just another writer" (ADR-0010) safe and testable.
- **Negative / costs:** every writer must maintain provenance; more columns/logic than naive upsert.
- **Follow-ups:** acceptance tests: human category survives sync; improved rule re-applies; audit records who set what.
