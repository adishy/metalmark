# ADR 0004: Postgres-backed job queue; no Redis

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0002, ADR-0003, ARCHITECTURE.md §1, §3

## Context

Sync needs scheduled runs plus on-demand "Sync now", executed off the request path by a worker. Common choice
is Celery/RQ + Redis. Scale here is a few users and a handful of connections.

## Decision

We will run a **single worker container** using **APScheduler** for cron polls and a **Postgres `sync_jobs`
table as the on-demand queue**, claimed with `SELECT … FOR UPDATE SKIP LOCKED`. A reaper re-queues orphaned
`running` jobs; a partial unique index (or advisory lock) enforces one running job per connection. **No Redis.**

## Consequences

- **Positive:** one fewer moving part to run, back up, and secure; jobs are transactional with the data;
  everything is in one durable store.
- **Negative / costs:** we hand-roll claim/reaper/uniqueness (small, well-understood). Not built for high
  throughput — fine at this scale; revisit if we ever add many users.
- **Follow-ups:** reaper + SKIP LOCKED + one-per-connection are explicit acceptance bars for WS-SYNC.
