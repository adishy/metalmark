# ADR 0016: Admin-grade sync observability

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0002, ADR-0004, ADR-0009, ARCHITECTURE.md §2, §4

## Context

The users are technical and want more control and insight into sync than the hosted apps offer — logs,
what was synced, how long it took. With poll-based sync (ADR-0002) and no webhooks, silent staleness is a
real risk.

## Decision

Sync is observable as a first-class feature. `sync_runs` records per-run trigger, duration, HTTP timing, and
counts (inserted/updated/reconciled/expired/matched, rules applied, bytes, status, error); `sync_run_events`
holds an ordered, **sanitized** structured log (never the access URL or PII). An **admin dashboard** shows
per-connection run history, timings, and exactly which transactions a run touched, plus manual "Sync now".

## Consequences

- **Positive:** technical users can diagnose and trust sync; makes "sync is just another writer" auditable.
- **Negative / costs:** more write volume and retention to manage (define pruning); must scrub secrets/PII from
  event logs.
- **Follow-ups:** log-retention/pruning policy; ensure no access URL or PII ever reaches `sync_run_events`.
