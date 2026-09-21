# ADR 0028: The aggregator seam, per-connection provider selection, cadence bounds, and the error taxonomy

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Related:** ADR-0001, ADR-0002, ADR-0007, ADR-0009, ADR-0016, ADR-0022, ARCHITECTURE.md §3

## Context

ADR-0001 chose SimpleFIN as the sole aggregator behind a pluggable interface and ADR-0022 required a spike
before committing to it. The spike is done and the interface is real code
(`app/services/aggregator.py`, `simplefin.py`, `fake_simplefin.py`). Four things ADR-0001 and ADR-0002 left
open turned out to be load-bearing, and one of them — the poll interval — was specified in the build plan as
something *other* than what ADR-0002 already mandates.

## Decision

**1. The seam is two methods, and the reconnect key lives in the module, not in the engine.**

```python
async def claim(self, setup_token: str) -> ClaimResult
async def fetch_accounts(self, access_url: str, *, start: datetime) -> AccountSet
```

Both are `async def`: the worker is a single asyncio task and httpx is the only HTTP client
(ARCHITECTURE §3's original sketch was synchronous). Normalized DTOs (`AccountSet`, `ProviderAccount`,
`ProviderTransaction`) mean nothing downstream sees SimpleFIN's wire shape. `external_key_for(account)` is
defined here rather than in `sync.py` so ADR-0009's reconnect key has exactly one definition and is
unit-testable alone; it keys on institution + normalized account name, deliberately excluding account id,
`conn_id` and balance, because those are exactly what a re-claim changes.

**2. The provider is chosen per connection, off the row. The environment variable decides only the claim.**

`AccountConnection.provider` already existed; the worker dispatches on it. A connection is `simplefin` or
`fake` for its whole life, so a demo connection keeps working in a deployment that has real credentials.
`METALMARK_SIMPLEFIN_PROVIDER` is read in exactly one place — the claim endpoint — because a connection that
does not exist yet cannot say which provider it is. `get_provider("fake")` **raises** unless
`METALMARK_ENV` is `test` or `dev`. The reason is not that the fake is dangerous: it is that a connection
syncing against nothing produces clean, empty, *successful-looking* runs, and a silent fake is
indistinguishable from a bank having a quiet day.

**3. Cadence: default 6 h, floor 2 h, ceiling 7 d, per-connection override.**

This preserves ADR-0002's documented "default every 6h". The build plan proposed **24 h** with a **1 h** floor;
that proposal is rejected, and the plan was the departure rather than the ADR. Two reasons, and the second is
the one that binds:

- **SimpleFIN's budget is not the constraint it looked like.** The bridge tolerates roughly 24 requests/day per
  token, and 6 h spends 4 of them. The plan's politeness argument was costed against a figure that does not
  bind at this cadence.
- **The 1 h floor is actively harmful, which is why the floor is 2 h.** The bridge's failure mode for an
  overrun is HTTP 403 — and 403 is `auth_error` (below), which the UI renders as "Reconnect needed". A user who
  sets a 60-minute interval would therefore be told their bank credential is dead when in fact they asked for
  too much. Half the budget (12 requests/day) keeps that trap out of reach.

The ceiling is 7 days: past that, a connection is one the household has effectively stopped watching, and both
the freshness and the `auth_error` signal should degrade to something visible rather than to silence.

**4. Provider errors map onto connection health by *whose problem it is*, and the mapping is deliberately
asymmetric.**

| Signal | `connections.status` | Why |
|---|---|---|
| `con.auth` | `auth_error` | The credential is revoked; re-authorising is the fix and is the only fix. |
| HTTP 403 on a fetch | `auth_error` | Revoked access URL — same fix, same affordance. |
| HTTP 402 on a fetch | **unchanged** | A lapsed bridge subscription is not fixable by reconnecting, so offering "Reconnect" would send the user to a page that fails identically. Recorded in `last_error` and left to the human. |
| other `con.*`, `act.*` | `error` / `partial` | The connection is fine; something about the account or the response is not. |
| timeout, 5xx, malformed payload | **unchanged** | Our problem, or the network's — never the credential's. These set `last_error` only. |

The enum stays at three values (`ok | auth_error | error`). No `payment_required`: the column is
`String(16)` and that value is exactly sixteen characters, which is a fragile place to put a new state.

**5. Failure notification fires on transition, not on every failure.**

A notification is sent when the previous run for that connection did **not** end in `error`, or when more than
24 h have passed since the last attempt. A 6-hourly cron against dead credentials would otherwise alarm four
times a day forever, and an alarm that repeats on a timer is one people learn to ignore. No new column is
needed for this — `sync_runs` already records the previous outcome.

**6. ADR-0007's list of provider-owned fields is a floor, and `transacted_at` is added to it.**

ADR-0007 names `amount, description, posted_at, is_pending`. `transacted_at` is the date field SimpleFIN
actually supplies and is absent from that list; read literally, sync would clobber a human's corrected
transaction date on every run — the exact silent corruption ADR-0007 exists to prevent. Sync writes
`transacted_at` under the same precedence as the rest (never over `user` or `rule`). ADR-0007 is not edited
(it is `Accepted`); this widens its list rather than contradicting it, and any future provider field joins
under the same rule.

## Consequences

- **Positive:** the ledger can be built and tested with no network and no credentials (`fake`), a real
  deployment cannot accidentally sync against a fake, and the cadence has a floor whose *reason* is written
  down — which is what stops someone "simplifying" it back to an hour.
- **Negative / costs:** two providers to keep honest, and the fake agrees with the parser by construction, so
  a wire-format regression must be caught by the captured fixture rather than by the fake. The error taxonomy
  is a hand-maintained mapping from provider codes to three states; a new SimpleFIN code defaults to
  `error`/`partial`, which is safe but not self-describing.
- **Follow-ups:** the `transacted_at` widening is **done** — ADR-0007's status line and its list both carry
  the pointer now, so a reader who stops at that list is told it is a floor. Deferred: ADR-0009's manual
  "possible duplicate" confirmation step (ambiguity currently inserts a new account and records an event).
