# ADR 0035: A report reads each thing once, and rounds where it says it rounds

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Refines:** ADR-0032 (the residual's decomposition), ADR-0017 (conversion)
- **Related:** ADR-0026, ADR-0033

## Context

`net_worth_series` computed one point by calling `net_worth_at`, and `net_worth_at`
was written for one account at one date: it read the whole account list, then asked
for one snapshot per account, then converted each with `fx.to_base`, which is up to
four queries on its own. Every one of those reads was correct and none of them could
be cached, because nothing in the signature said which of the three loops it was
inside.

The cost compounds rather than adds, and the residual's decomposition is where it
does. `_unexplained_by_account` attributed the residual by calling `net_worth_at`
**twice per account**, so a household's attribution cost O(accounts²) round trips —
each of those calls re-reading the account list, and each filtered to a single
account *in Python* after reading all of them from the database.

Measured on a synthetic 24-account household with 15 years of monthly balances
(`tests/integration/`-shaped data, 3,982 snapshots, 10,952 rates), before:

| window | points | queries | time |
|---|---|---|---|
| 1 year | 14 | 714 | 150 ms |
| 5 years | 62 | 2,541 | 508 ms |
| 15 years | 182 | 7,108 | 1,379 ms |

That is one of four requests the Reports page makes, against a local database. The
numbers are the smaller half of the problem: the queries are *round trips*, so the
same page over a network is seconds, and the shape of the code made it look like
three independent helpers rather than one quadratic read.

## Decision

**A report's loop over dates is a single pass, and the functions it calls say how
many dates they are for.**

- `_net_worth_parts(session, dates, base, account_ids)` returns every included
  account's value at every requested date, in base, **unquantized**. It reads the
  account list, the snapshots (one query, all accounts, up to the last date asked
  about) and the rates each once. "Latest snapshot ≤ date" becomes a bisect per
  account over `balance_date`, which is unique per account, rather than a
  `ORDER BY … LIMIT 1` per account per date.
- `net_worth_points` rounds **once over the total**; `net_worth_points_by_account`
  rounds **per account**. Both are thin wrappers over `_net_worth_parts`, and they
  are the only two rounding sites. `net_worth_at` remains as `net_worth_points`
  with a list of one, so the "latest snapshot ≤ date" rule still has one definition
  and its 30-odd call sites are unchanged.
- `fx.converter()` builds a `Converter`: the rates for the currencies a report will
  convert from, read once, answering from memory. `_multiplier` — the resolution
  order identity → direct → inverse → triangulate — is shared by the `Converter`
  and the session path, so a second rate source cannot become a second opinion.
- `securities_value_base` is now the sum of `securities_value_by_account`, which
  returns the per-account parts unquantized. The total and its parts cannot
  disagree about a rounding cent, and a decomposition can ask about one account
  without valuing the household to answer.

**The `Converter` is built per request and discarded, never cached.** A rate edited
while a chart is being drawn must not make two points of that chart disagree about
what a currency is worth; an object that outlived the request would be a stale
second source of truth for exactly that.

**`until` bounds the load at the last date that can be asked about**, and is
therefore a *correctness* parameter, not a tuning knob: a caller that asks past its
own bound is answered from the older rates it did load. It is named for the dates
rather than for a limit so that reading the call site says which is which.

## Consequences

- **Positive:** the same measurement, after — 108 / 255 / 622 queries and
  84 / 116 / 234 ms, a 91% reduction in round trips at the long window — with every
  reported number **byte-identical** to before, including the residual, its
  attribution and the row counts. The change is a re-arrangement of reads, not a
  change of answer, and that is what the equivalence tests pin.
- **Positive, and the more durable half:** "how many dates is this for?" is now
  visible at each helper's signature. The quadratic read was not a bug anyone
  wrote on purpose; it was three helpers each written for one thing and called in a
  loop by somebody else.
- **Negative / costs:** `_net_worth_parts` is one longer function where there were
  three short ones, and it holds the two-rounding-site rule that the two wrappers
  exist to keep straight. A reader who wants "net worth at a date" now has to
  follow one hop (`net_worth_at` → `net_worth_points` → `_net_worth_parts`) rather
  than read it in place.
- **Negative / costs:** the batched form loads every snapshot up to the last date
  asked about, where the per-account form loaded one row per account per date. For
  a household with a long history that is more rows over the wire in exchange for
  far fewer round trips. It is the right trade at these sizes (3,982 rows for 15
  years); it would not be if a household ever held hundreds of thousands of
  snapshots, and a keyset-paged version is the answer then.
- **Not done, deliberately:** the per-account quantization is *not* unified into
  the total's. They answer different questions — "what is this account worth" is a
  number the report prints, and printing a part that does not sum back to the whole
  would be the worse surprise. The unquantized intermediate is what lets both round
  where they round.
- **Not done, deliberately:** a `stale_balances`/`as_of` field on `NetWorthSeries`.
  It was on the list that produced this ADR, and it should not be: a carried-forward
  balance is *supposed* to be carried forward, the reconciliation already names the
  accounts a gap came from (ADR-0032 §5), and a field nobody renders is a field
  that can only be wrong.
