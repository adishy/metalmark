# ADR 0045: History before an account's first balance is derived, and every point says what it leaves out

- **Status:** Proposed
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh (pending review)
- **Related:** ARCHITECTURE.md §2 (supersedes "there is no backfill"), ADR-0032 (the reconciliation),
  ADR-0035 (reports read once), ADR-0043, ADR-0044, session 04's audit (findings #3 and #5)

## Context

Three ways the net-worth line moved without the household's money moving, all reproduced in session 04:

- **The first-sync cliff.** A first sync pulls ~44 days of transactions and one balance, dated today. The
  series counted an account as absent until its first snapshot, so the line rose from nothing to the full
  balance on the connection date, and the reconciliation called the account's whole pulled history
  "unexplained" ($22,200 on a small example; $38,850.60 in a real one quoted in the code).
- **Unknown drawn as zero.** A position with no price yet, or a balance in a currency with no rate, was
  silently left out of a point — and a point that leaves an account out looks exactly like a point where the
  household lost that much. A holding first priced mid-window was then reported as **market appreciation**
  of its entire value, with nothing unexplained and no warning.
- **No way to tell the two apart.** The response gave one number per point.

## Decision

1. **Before its first snapshot, a stated account's balance is derived backwards**:
   `balance(d) = B_S − Σ amounts of its transactions dated (d, S]`, for `d` from the day before its earliest
   transaction up to `S`. Earlier than that it is `not_started`. After `S`, snapshots are carried forward as
   before. The day of a transaction is `transacted_at::date` in SQL, the same basis as every report bound.
2. **Every transaction counts in the derivation, hidden and pending included.** They moved the bank's
   balance, and the balances after `S` are the bank's own numbers — a derivation that skipped them would make
   a window straddling `S` disagree with itself. A dismissed duplicate is deleted, not hidden, so it is not
   counted; an expired pending phantom is deleted by sync. A hidden row therefore shows as `unexplained` on
   its account, on either side of `S` alike.
3. **Investment accounts are not derived backwards.** Their balance also moves with the market, so
   `B_S − Σ` would assert a flat market and make the reconciliation exact by construction. They start at
   their first snapshot (positioned accounts are valued from holdings, unchanged).
4. **One reader answers "what was this account's balance on day d"** (`reports._BalanceHistory`, loaded by `_balance_histories`), used by
   the net-worth points and the revaluation term's opening balance alike, from two queries per report
   (snapshots; transactions aggregated per account and day).
5. **Each point of `/reports/net-worth` carries `missing`**: the accounts it does not fully count, with a
   reason — `not_started`, `no_balance` (transactions but never a balance: opened blank and imported),
   `no_rate`, `no_price` (the point holds a partial sum). The chart draws the difference.
6. **Unknown is not zero in the reconciliation.** A position unpriced at either end of a window is excluded
   from both ends of `market_appreciation` and from its trades, and a warning names it; its change lands in
   `unexplained`, attributed to its account.

## Consequences

- **Positive:** the chart has no step at a connection date; a window over pulled history reconciles exactly;
  a gap in knowledge is drawn and named as a gap; appreciation is only ever a priced move.
- **Negative / costs:** the derived region is only as good as the transactions: a provider whose balance
  excludes pending rows, or a history with a missing transaction, shifts every derived day before it by that
  amount (visible as `unexplained` when a window crosses a later snapshot). An account whose earliest known
  transaction is recent still starts partway through a long window — now marked `not_started` rather than
  silently zero.
- **Follow-ups:** the same derivation could fill *between* sparse snapshots (roll forward from the earlier,
  compare at the later); not done — carry-forward stays, and the disagreement at the later snapshot is what
  `unexplained` is for.
