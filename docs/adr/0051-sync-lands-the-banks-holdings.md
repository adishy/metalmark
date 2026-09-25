# ADR 0051: Sync lands the bank's holdings as positions

- **Status:** Accepted
- **Date:** 2026-09-24
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0021 (amended: a synced stated account now has positions), ADR-0033 (amended: the "no
  holdings from SimpleFIN" premise), ADR-0034 (recorded trades still win), ADR-0044, ADR-0007/0019
  (provenance)

## Context

ADR-0021 and ADR-0033 were written on the premise that SimpleFIN reports an investment account's balance and
nothing it holds. That is not true of the bridge as captured: under `version=2` an account carries a
`holdings` list (symbol, description, shares, market value, total cost basis), and the committed capture has
one (550 AAPL). The parser read it, but sync used it only to guess that an account is an investment account.
So a household with a synced brokerage saw its balance and no positions, with "the bank reports this account's
balance, not what it holds" in place of the breakdown the bank had actually sent.

## Decision

1. **Sync writes positions from the bank's holdings**, per account, after the balance.
   - **Security:** by ticker and currency when the bank gives a symbol; by description when it does not; one
     `cash` security per currency for a cash line (a symbol such as `CASH` or the currency code, or an
     unsymbolled line named cash with no share count). An existing security is reused and **never edited** —
     its name and type may be a human's. A new one is `is_manual = false`, typed by a guess from its name.
   - **Price:** market value ÷ shares for the balance date, `source = 'auto'`. A price a human entered for that
     security and day stands.
   - **Holding:** `holdings.source = 'simplefin'`, quantity = shares, `cost_basis` = the bank's total basis,
     `as_of` = the balance date. Several lines for one instrument (lots) are summed into its one position.
2. **Sync mirrors the bank, and only its own rows.** A synced position the bank stops reporting is removed.
   A hand-entered position (`source = 'manual'`) is never written or removed by sync. When the bank also
   reports an instrument a human entered in that account, the bank's line is skipped and counted
   (`hand_entered`). A position whose trades are recorded is history's (ADR-0034), and the bank's line is
   skipped (`has_history`).
3. **A synced position is not editable by hand** — a quantity or basis write is refused (409), as for a
   history-owned one. The API reports its `quantity_source` as `provider`; the UI says "from your bank".
   Deleting one is allowed; the next sync writes it back if the bank still reports it.
4. **The account stays `stated`.** Its value is the bank's balance. The positions explain it, and what they do
   not cover (cash the bank did not list, a line with no share count) is ADR-0021's unaccounted-cash remainder.
   Reports still read a stated account from its balance snapshots, so positions never add to net worth twice.
5. **Not landed, and said:** lines in an account a human re-typed away from investment, in a `derived` account
   (its value is the sum of positions a human entered), with no share count, with no symbol or description,
   or with a negative implied price. Each run records `holdings_seen / written / removed` in `run.finished` and a
   `holdings.synced` event per account with the skip reasons, at `warning` when any line was skipped.
6. **Migration 0011** adds `holdings.source` with default `manual`, which is what every existing row is. The
   downgrade removes synced rows, and the securities only they used.

## Consequences

- **Positive:** a synced brokerage shows what it holds, priced as of each sync, with no hand entry. The run
  log says whether the bank sent holdings at all, which the agent API can read.
- **Negative / costs:**
  - The price series for a synced security is one point per sync day, implied by the bank's market value.
    It is as good as the bank's valuation and no better.
  - A bank that omits a holding for one fetch removes the position until the next fetch reports it. The
    account's value does not move (it is the balance), only its breakdown.
  - A guessed security type can be wrong (an ETF named without "ETF" reads as a stock). It is editable, and
    sync never overwrites it.
  - Investment transactions are still not ingested (ADR-0033's limit stands), so appreciation for a synced
    account remains a stated balance change.
