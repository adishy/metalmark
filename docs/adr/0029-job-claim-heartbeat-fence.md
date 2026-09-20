# ADR 0029: The job queue's claim mechanism — heartbeat, fence, and per-household enumeration

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Related:** ADR-0004, ADR-0014, ADR-0025, ARCHITECTURE.md §3, §5

## Context

ADR-0004 chose a Postgres job queue, a single APScheduler worker, `SELECT … FOR UPDATE SKIP LOCKED` for the
claim, a reaper that re-queues orphaned `running` jobs, and "a partial unique index (or advisory lock)" for
one-running-job-per-connection. Three of those sentences turned out to be underspecified in ways that matter,
and one of them cannot be implemented as written.

1. **RLS makes the obvious claim impossible.** ADR-0014/0025 put every household-scoped table behind a
   fail-closed policy keyed on the `app.household_id` GUC, and ARCHITECTURE §5 forbids the worker from
   bypassing it. So an unscoped `SELECT` on `sync_jobs` returns **nothing** — the worker cannot *discover* the
   work by querying the queue. "Claim the next job" as written has no subject.
2. **"Past a timeout" does not say since when.** Elapsed time since `started_at` is the natural reading, and it
   is wrong: a slow-but-alive fetch is indistinguishable from a dead worker, so a long run gets reaped
   *because it is working*.
3. **A reaped job's worker does not know it.** A worker that was slow (not dead) comes back, finishes, and
   writes its results over a job another process has since re-run.

## Decision

**1. The worker enumerates households and claims per household, under `scoped_session`.**

Its only unscoped query is `SELECT id FROM households` — the same query `deps.get_context` →
`auth_service.membership_for` already runs, against a table ARCHITECTURE §5 documents as non-RLS and readable
by the app role. It adds zero exposure: any principal who can run SQL as the app role can already read
`households`. 100% of `sync_jobs` / `sync_runs` / `sync_run_events` access stays under RLS.

**`assert_app_role()` is what keeps that true, and it is a startup `SystemExit`, not a comment.** Bypassing RLS
is a *grant*, not a code path — a worker connected as a superuser would read every household's jobs and the
code above would look identical. The worker reads `rolsuper`/`rolbypassrls` for `current_user`, refuses to
start with a non-zero exit, and restart-loops until someone fixes the connection.

*Documented fallback, not chosen:* a `SECURITY DEFINER` function returning the claimable job ids across
tenants. It would work, but it needs a pinned `search_path` (`pg_temp` is the real injection vector, not
`public`) plus `REVOKE ALL … FROM PUBLIC`, which `CREATE FUNCTION` grants by default and `0001`'s
`ALTER DEFAULT PRIVILEGES` does not cover — and its only purpose would be to leak three columns across
tenants. Every future security review would have to re-derive that it is safe. Revisit only if a per-household
scan stops being acceptable.

**2. Liveness is a heartbeat, not elapsed time.** `sync_jobs.heartbeat_at` is stamped at claim and again by the
fenced touch that runs immediately after the HTTP fetch. The reaper's predicate is
`heartbeat_at < now() - 900s`. The fetch is the long part of a run, so the statement that says "I am still
here" belongs at the end of it — and there is then no window in which a worker heartbeats a job it has already
lost. Reaping also closes any run row the dead worker left open, because a run stuck at `running` forever hides
every run after it behind the same lie.

**3. Every write the worker makes after the claim is fenced on a `claim_token`.** Finalize is
`UPDATE … WHERE id = :id AND claim_token = :token`; a stale worker's update touches zero rows and it discards
its results rather than resurrecting a job the user has been told is over. The reaper is the one deliberate
exception — it is *unfenced*, because the token it would check is the dead worker's; its exclusivity comes from
`FOR UPDATE SKIP LOCKED` on the stale-job `SELECT` instead, so the reaper and a live worker are mutually
exclusive either way.

**4. The one-per-connection race is made rare *and* harmless.** The partial unique index
`uq_sync_jobs_connection_running ON (connection_id) WHERE status = 'running'` is declared identically in the
ORM and the migration (or `alembic check` fails). The claim both excludes connections with a live run — which
makes the race rare — and wraps its `UPDATE` in a **savepoint**, catching SQLSTATE `23505` and returning
`None`, which makes it harmless: without the savepoint a lost race aborts the whole transaction, and the
worker's claim loop turns that into a crash loop. The code is read off the driver's `sqlstate`, not parsed from
the message, which is Postgres's wording and changes between versions and locales.

A job for a **paused** connection is cancelled at claim time rather than run. The queue cannot un-queue it when
the pause lands (the job may not exist yet, and a job that is already running must be allowed to finish), so
the moment before it would execute is the last one at which refusing is free. Silently syncing a connection the
user switched off is the one behaviour that would make the pause button untrustworthy.

## Consequences

- **Positive:** RLS stays literally true for every queue table, the isolation is enforced by a *grant check*
  rather than by reviewer attention, a slow run is no longer punished for being slow, and a lost claim race is
  an ordinary `None` rather than an outage. Cancel becomes a database write — it bumps `claim_token` — so it
  needs no signal channel to a process that may be mid-`await`.
- **Negative / costs:** the worker does one extra query per tick and one claim attempt per household, so the
  queue's cost grows with the number of *households* rather than with the amount of work. Fine for a home lab;
  it is the thing to revisit first if this ever hosts many tenants. The savepoint is load-bearing and
  non-obvious: deleting it looks harmless and reintroduces a crash loop under exactly the race it prevents.
- **Supersedes ADR-0004's claim-mechanism half.** The rest of ADR-0004 stands: one worker, APScheduler for
  cron, Postgres as the queue, no Redis.
- **Follow-ups:** a cancelled-while-queued job leaves no `sync_runs` row (nothing ran, so there is nothing to
  report) and is therefore invisible in the panel's run history, which shows only runs. Acceptable while the
  job row is retained; revisit if job retention is ever pruned without a corresponding history view.
