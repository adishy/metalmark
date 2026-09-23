# ADR 0044: A balance lands on its own date, and an investment account with no positions is read from its balance

- **Status:** Proposed
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh (pending review)
- **Related:** ADR-0021 (amended: which accounts are valued from holdings), ADR-0030 §6 (OFX balances),
  ADR-0032 §5, ARCHITECTURE.md §2, session 04's audit (findings #2 and #4)

## Context

Two ways the net-worth line and the Accounts headline stopped describing the same money, both found and
reproduced in session 04:

**Balances rewrote the wrong day.** The edit dialog pre-filled the account's *last* balance date and sent
it back with every save, so typing today's balance overwrote that earlier day's snapshot. Sync kept the
account's old date whenever the provider sent none, with the same effect. An OFX statement imported after
a newer one moved `current_balance` backwards. And the create form's default opening balance of `0` was
snapshotted at today, so an account opened to receive an imported statement dropped to $0 on the chart on
the day it was created.

**Investment accounts with nothing to derive from read as $0.** ADR-0021 makes a manual investment
account `derived` — its value is Σ(holdings) — and sync did the same for any account whose payload had
holdings. But sync writes no holdings, and a person can open an investment account with a typed balance
and nothing else. The series valued those accounts from positions that did not exist: the demo capture's
$114,685.51 brokerage account was on the Accounts page and absent from the chart.

## Decision

1. **`ledger.record_balance` is the one rule for where a balance lands**, and every writer calls it —
   account creation and edit, sync, OFX. The snapshot is written at the balance's **own** date; the
   account's `current_balance`/`balance_date` move to it only when that date is not older than the one
   it has. A balance with no date is today's, never the account's previous date. An earlier date is a
   correction to history and lands there, leaving the headline alone.
2. **An opening balance is an observation only when someone gives one.** `POST /accounts` without
   `current_balance` creates the account with no snapshot and no `balance_date`; the form's field is blank
   by default.
3. **An account is valued from holdings only when it is `derived` *and* has a position** — a holding or an
   investment transaction (`reports._valued_from_holdings`, one predicate for net worth, revaluation and
   appreciation). Otherwise it is read from its snapshots like any stated account. This amends ADR-0021,
   whose derived default stands for manual accounts.
4. **Sync creates investment accounts `stated`.** It has no holdings to derive from. Migration 0006 moves
   existing synced, positionless, derived accounts to `stated` and gives each one snapshot from the
   balance it already shows (sync had never snapshotted them). An account with any hand-entered position
   stays derived.

## Consequences

- **Positive:** on the day balances are dated, the series equals the headline — asserted for a synced
  household, a typed-balance investment account and a household with a card
  (`tests/integration/test_net_worth_series.py`). History changes only where someone meant to change it.
- **Negative / costs:** a derived account that gains its first holding switches from its snapshots to its
  positions, and its earlier history is then valued from the positions projected backwards
  (`quantities_at` ignores `holdings.as_of`) — the same behaviour as before for such an account, now
  confined to accounts that actually have positions. A backdated edit no longer changes the headline; the
  dialog says what date the current balance is as of so that is not a surprise.
- **Follow-ups:** a first-position switch could carry the account's stated history up to the holding's
  `as_of` instead (session 04, Phase B); the account-level "unaccounted cash" plug of ADR-0021 is the
  related piece.
