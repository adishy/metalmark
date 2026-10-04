# Debugging a live instance through the agent API

The agent read API (ADR-0048/0061) is a read-only, anonymized mirror of the app's own API: every
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

## Read and debug boundaries

GET and debug routes cannot write (every request runs in a read-only transaction), echo free-text query
parameters (`search` is refused — it would be an oracle), or return e-mail addresses.
Deploying and any write — re-running the categorizer, fetching logos, fixing a bank link —
belongs to the instance's owner.


## Optional entry permissions

An owner can issue a separate token with `transactions:write`, `holdings:write` or `accounts:write` in Admin → Agent access. Existing read tokens gain no permissions. These scopes only open:

- `POST /api/agent/v1/transactions`: add an entry (`AgentTransactionCreate`). `amount` is signed in the account's currency: negative is money out. There is no description or merchant; an optional `note` is stored as `Added by agent: <note>`. A retry creates another transaction, and so does the bank's own copy arriving later on a synced account; check first.
- `POST /api/agent/v1/investments/holdings`: add a position (`AgentHoldingCreate`) using an existing account and security ID. `market_value` is a total in the security's quote currency. Existing bank, manual or history positions are refused with 409.

Inputs are real IDs, numbers and dates from the read API; names and other free text are refused, because a name an agent could submit and read back as a pseudonym would undo the anonymization (ADR-0061). Unknown fields are a 422. Responses remain anonymized. `POST /api/agent/v1/accounts` (`accounts:write`, ADR-0062) creates a manual account from a type, currency, optional name, subtype, opening balance and owner; without a name it is `Added by agent`. The name is the one free-text field an agent may send. All write scopes can be issued only by the household owner and require the issuer's current owner role; no update/delete routes, trades or security management are exposed. Every write is logged as `agent.write`. Request schemas are in `/api/openapi.json`. Production debugging remains read-only unless a separate task explicitly authorizes an entry.

## Account files

Files attached to accounts are not part of the anonymized API. A token with the owner-issued
`documents:read` scope (ADR-0062) can list them at `GET /api/agent/v1/accounts/{account_id}/documents`
and fetch one at `…/documents/{document_id}/content`. **Both return real file names and the file's
bytes exactly as uploaded — nothing is anonymized.** Do not ask for this scope to debug an instance;
it exists for an owner who wants an agent to read their statements. Each access is logged as
`agent.file`.
