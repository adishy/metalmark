# Debugging a live instance through the agent API

The agent API (ADR-0048) is a read-only, anonymized mirror of the app's own API: every
number and date is real, every name and free-text field is a stable pseudonym
(`Merchant 9c369d`). It is how an agent — or a person who does not want to look at the
household's names — debugs a running instance. Never debug through the database.

## Setup

An admin issues a token in **Admin → Agent access** (both scopes: `agent:read`,
`debug:read`). Keep it in a file outside the repository and read it into a variable; never
print it, paste it into a log, or commit it.

```bash
# mm <path under /api>: an authenticated GET that never echoes the token
cat > /tmp/mm <<'SH'
#!/usr/bin/env bash
T=$(tr -d '[:space:]' < "$HOME/path/to/metalmark_token.txt")
curl -sS -m 30 -H "Authorization: Bearer $T" "http://<host>:<port>/api/${1#/}"
SH
chmod +x /tmp/mm
/tmp/mm agent        # the catalog: every route, its params, and a link where it needs none
/tmp/mm anon_debug   # the debug views
```

Everything is discoverable from those two roots. Pseudonyms are stable for one token, so
equal pseudonyms mean equal values.

## Symptom → where to look

### A report looks wrong (e.g. cash flow far off)

1. `anon_debug/pages/reports` — every request Insights' Overview tab makes (the backend route
   group is still `reports`; only the frontend destination was renamed), exactly as it gets them.
   Check the window (`start`/`end`) and `granularity` each call used.
2. `agent/v1/transactions?limit=200` (follow `next_cursor`), then sort by `base_amount`: the
   largest rows are nearly always the story. Group by `account_id` for per-account sums.
3. For a large row: is `transfer_group_id` set? If not,
   `agent/v1/transactions/transfer-candidates?txn_id=…` shows what the matcher saw and whether
   each candidate is `within_tolerance`. Two equal candidates at equal distance are refused on
   purpose (ADR-0049).
4. `anon_debug/transactions/{id}/explain` — provenance, every rule condition by condition, the
   owner chain, the transfer.
5. `anon_debug/system` → `counts.categories == 0` means nothing can be marked a transfer; moves
   to accounts the app does not hold will count as spending until they are.

*Session 06 found −$120k of "cash flow" this way: two unlinked $20k transfer pairs plus ~$115k
of moves to own accounts in a household with no categories.*

### Transactions stopped arriving

1. `agent/v1/connections` — `last_synced_at`, `status`, `last_new_data_at`, `quiet_syncs`.
2. `agent/v1/connections/runs?limit=20` — for each run: `txns_inserted/updated/reconciled` and
   **`bytes_fetched`**. Successful runs with zero new rows and a near-identical byte count mean
   the bank bridge is serving the same cached payload — the problem is upstream (the bank's link
   at SimpleFIN Bridge needs a refresh or a new sign-in), not the sync.
3. `agent/v1/connections/runs/{id}` — the run's events: `window.computed` (the window asked
   for), `provider.errmessage` (the bridge's own warnings), per-account balance snapshots.
4. `agent/v1/transactions?limit=20` — the newest `transacted_at`, and whether old pending rows
   ever settled (a pending row that never posts is the same stall seen from the ledger).

The app itself warns on Accounts and in Admin once a bank has been quiet for 4 successful syncs
and 36 hours (ADR-0050).

### A balance or net worth looks wrong

`anon_debug/accounts/{id}/balance` — snapshots, drift since the last one, holdings, and the data
checks that flag the account. `agent/v1/checks` runs every data check now.

### "What does the user see on page X?"

`anon_debug/pages/{accounts|transactions|review|reports|investments|settings|admin}`, or
`anon_debug/view?path=/api/…` for any URL copied from the browser.

## What the API will not do

Write anything (every request runs in a read-only transaction), echo free-text query
parameters (`search` is refused — it would be an oracle), or return e-mail addresses.
Deploying and any write — re-running the categorizer, fetching logos, fixing a bank link —
belongs to the instance's owner.
