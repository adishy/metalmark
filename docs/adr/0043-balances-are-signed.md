# ADR 0043: Every stored balance is signed — a debt is a negative number

- **Status:** Accepted
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh
- **Related:** ARCHITECTURE.md §2 ("Net worth correctness", which this supersedes in part), ADR-0017,
  ADR-0032 (the revaluation term), ADR-0036 (the export format, bumped to version 2), session 04's audit
  (`agent_record/2026-09-23-session-04-reports-audit.md`, finding #1)

## Context

A card's or loan's balance meant two things at once. The manual form documented "liabilities entered as a
positive amount owed", and `ledger.net_worth` and the net-worth series both subtracted liability balances
(`is_asset` flipped the sign). SimpleFIN and OFX, meanwhile, report a balance that moves with its
transactions — a purchase (negative amount) makes it more negative — and sync and the OFX importer stored
that number as it came. The revaluation term assumed the *second* convention (`B_end = B_start + Σ
amounts`) and then multiplied by `is_asset`'s sign anyway, so it was wrong for both.

What that did, measured against a real Postgres in session 04: a synced card at −$1,000 contributed
**+$1,000** to net worth; paying it off moved the line by **−$2,000** with nothing having happened; a euro
card's spending was reported twice over as currency revaluation. The same bank card was right or wrong
depending on its *name*, because sync types an account `credit` only when its name says "credit" or "card"
and otherwise `other` — an asset, for which the provider's negative number happens to be correct.

The SimpleFIN protocol does not state a sign for `balance`. It does say transaction amounts are positive
for money in, and a balance consistent with its own transactions must then be negative for debt. Actual
Budget's SimpleFIN integration, a widely used consumer of the same bridge, stores the balance unchanged
and shows cards as negative (`app-simplefin.js`: `startingBalance = parseInt(account.balance…)`, no
per-type adjustment). That is corroboration, not proof; the decision below does not depend on it.

## Decision

1. **Every stored balance is the account's signed balance.** Debt is negative; a balance moves by exactly
   the amounts of its transactions, for every account type. `balance_snapshots.balance`,
   `accounts.current_balance` and the API's `current_balance` all mean this.
2. **Net worth is the sum of converted balances.** No reader flips a sign by `is_asset`. `is_asset` is
   presentation: which side of Assets/Liabilities an account is listed on. `/accounts/net-worth` still
   reports `liabilities` as the positive amount owed (the negation of what liability accounts sum to), so
   its shape and `net_worth = assets − liabilities` are unchanged.
3. **Revaluation has no sign term:** `B_start·(R_end − R_start) + R_end·Σamounts − Σbase_amounts`.
4. **People read and type a liability as the amount owed.** The Accounts page is the one place that
   converts (`negateAmount`), in both directions.
5. **An account's type is editable** (`PATCH /accounts/{id}` `type`). Because balances are signed, a
   retype changes the label and never the history — which is the reason it is safe to allow at all. An
   account with holdings or investment transactions cannot stop being an investment account (409).
6. **Sync warns** (`balance.liability_positive`) when a liability reports a positive balance: a card in
   credit, or a bridge using the other sign — which would count every card for the household. It is not
   guessed at.
7. **Existing data is converted per account** (migration 0007; the same rule reads version-1 exports):
   a liability never synced has its positive balances negated; a synced one only if it also has a
   negative balance (the provider's sign is evident, so the positives are hand edits); a synced one that
   was never negative is left alone. The rule lives in `services/balance_sign.py`; the migration carries a
   frozen SQL copy and a test holds the two to the same answers. `available_balance` is not converted —
   for a card SimpleFIN reports the available credit, positive in either convention.
8. **The export format is version 2.** Version-1 documents are still read, upgraded by rule 7 first.

## Consequences

- **Positive:** one meaning for one number, and the one every provider already uses; the reconciliation
  identity holds for liabilities (tests: card payoff, card spending, foreign card at a flat and a moving
  rate); a mistyped account is correctable without touching its history.
- **Negative / costs:** existing data had to be rewritten, with a rule that can misread two shapes: a
  manual card that was genuinely in credit (flipped into a small debt), and a synced card that was only
  ever positive (left alone, and warned about on its next sync). Migration 0007 keeps every value it
  changed in `migration_backup` and its downgrade restores any value still holding what it wrote. Any
  external client that posts a card's balance as a positive amount owed must now post it negative.
- **Follow-ups:** watch for `balance.liability_positive` in Admin after the first sync post-upgrade;
  `balance_snapshots.source` (which writer produced a row) would make any future convention change
  mechanical rather than inferred.
