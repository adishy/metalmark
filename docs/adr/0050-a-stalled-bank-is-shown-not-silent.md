# ADR 0050: A stalled bank is shown, not silent

- **Status:** Accepted
- **Date:** 2026-09-24
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0016 (sync observability), ADR-0028 (provider seam, error taxonomy),
  ADR-0048 (the agent API it was found through), agent record session 06

## Context

On a live instance, new transactions stopped appearing. Every sync for two days reported `ok`:
it reached all 13 accounts, snapshotted their balances, and inserted, updated and settled
nothing. The bridge (SimpleFIN) was returning a byte-for-byte near-identical ~19 KB payload
each time, with no entry in its `errlist`, and pending rows from days earlier never posted.

Nothing in the app was wrong, and nothing in the app said so either. `status: ok` and a recent
`last_synced_at` described a healthy connection; the only signal was an absence, visible only to
someone who went looking at run counts. To the household it looked like the app losing
transactions.

## Decision

We will report, per connection, when a sync last **brought anything new** — a transaction
inserted, changed or settled — and how many successful syncs have run since without any
(`last_new_data_at`, `quiet_syncs` on `ConnectionOut`, computed from `sync_runs`). Failed runs
are not counted as quiet: they are already reported as failures.

The app **warns** when a connection is enabled, has had at least **4** quiet successful syncs,
and has brought nothing new for at least **36 hours**: on the Accounts page (where missing
money is noticed) and on the connection in Admin (where it is acted on). The warning says what
is happening in plain words — syncs succeed, the bank sends nothing new — and where to fix it
(the bank's link at the bridge).

## Consequences

- **Positive:** an upstream stall reads as a stall. The fix (re-linking the bank at the bridge)
  is named at the moment it is needed, instead of after someone reads the sync runs.
- **Negative / costs:**
  - A household that genuinely spends nothing for a day and a half on every account sees a
    false warning. The thresholds are chosen so a quiet weekend does not trigger it; two days
    of identical answers does.
  - `data_freshness` scans a connection's runs on every connections read; fine at this app's
    scale (a few runs a day), and the place to add a bound if run retention ever grows.
- **Follow-ups:** a notification (ADR-0037) when a connection first crosses the line, rather
  than only a banner; recording a payload fingerprint per run would make "identical payload"
  a fact rather than an inference from the byte count.
