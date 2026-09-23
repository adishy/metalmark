# ADR 0046: Rates are fetched daily, and the most recently stated rate wins in either direction

- **Status:** Proposed
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh (pending review)
- **Related:** ARCHITECTURE.md §2 ("Rates are pulled daily", which this makes true), ADR-0006, ADR-0017,
  ADR-0002 (LAN-only: this is an outbound call, like SimpleFIN), session 04's audit (finding #5)

## Context

ARCHITECTURE §2 promised a daily fetch; none existed. A rate was whatever a person typed, so a foreign
account counted for nothing before its first rate and at a months-old rate after it — on the chart
(`no_rate` points, ADR-0045) and in every cached `base_amount`.

Adding a second writer of rates exposed a flaw in the resolution rule. `fx._multiplier` preferred the
**direct** pair whenever any direct rate existed on or before the day, and only then tried the inverse. A pair
can be stored either way round — a person types EUR→USD, a fetch writes USD→EUR — so one old typed rate
would have shadowed every fresher fetched rate indefinitely.

## Decision

1. **The worker fetches daily mid-market rates** from Frankfurter's v2 time-series endpoint (ECB and other
   central-bank sources) into `fx_rates` as `source='auto'`, stored `1 base = rate quote` against each
   household's base currency. **On by default only in `prod`**; `METALMARK_FX_FETCH=true|false` overrides,
   and `METALMARK_FX_URL` points it elsewhere. Dev stacks, tests and CI do not reach the network.
2. **Coverage:** for each non-base currency a household uses, from the first day anything is denominated in it
   (a balance, transaction, price or trade — or an account's balance date) to today. Later runs fetch only
   the days outside what is held: after the last auto rate, and before the first when older history has been
   imported.
3. **Never overwrites a row.** Inserted with `ON CONFLICT DO NOTHING`, so a rate a person entered for a day
   stays that day's rate.
4. **Resolution: the more recently stated of the direct and inverse rates wins**, direct on a tie — so a
   person's rate governs from its date until a newer rate exists in either direction, and a fetched rate never
   has to be written the same way round as a typed one to count.
5. **One recompute of cached `base_amount`s per household after the inserts**, counting how many changed and
   logging it (`fx.refreshed`) — not a per-day `upsert_fx_rate`, which recomputes every foreign transaction
   each call. `fx_rates` is shared by every household, so the recompute runs for any household whose
   currencies anyone's run wrote.
6. **The worker recomputes every household's cached amounts once when it starts**, fetch on or off: the cache
   is a function of the rule as well as the rates, and rule 4 changed the rule. Idempotent; the count of
   changed amounts is logged (`fx.recomputed`). A recompute also re-allocates a split parent's children, which
   carry what reports sum, so a rate that arrives after a split reaches them.
7. **One request per currency.** A symbol the source rejects (422) is logged and costs only that currency; a
   failed request is retried on the next run and never raised into the scheduler.

## Consequences

- **Positive:** foreign accounts have a value on every day they existed; the chart's `no_rate` points go away
  for any currency the source covers; the headline and chart agree at today's rate (ADR-0045's invariant
  holds for foreign currency).
- **Negative / costs:**
  - **The first start after deploying rewrites cached amounts.** Every foreign transaction without a rate
    gets one, ones converted at a stale typed rate move to that day's market rate, and a pair stored both ways
    round may convert differently. The counts are logged (`fx.refreshed`, `fx.recomputed`), and the fetch runs
    before the start-up checks (ADR-0047).
  - **The way back is to delete the `source='auto'` rows and recompute** (tested). Typed rates are never
    touched.
  - **A typed rate now stops governing once a newer rate exists in either direction.** Previously, a typed
    rate stored in the direct direction kept winning.
  - **It is an outbound call to a third party**, like SimpleFIN. Only currency codes and dates leave the
    instance.
- **Follow-ups:** currencies the source does not cover (crypto, some exotics) still need typed rates.
