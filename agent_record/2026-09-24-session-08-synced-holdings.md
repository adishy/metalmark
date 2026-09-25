# Session 08: The bank's holdings, landed as positions

- **Date:** 2026-09-24. Continues session 07 after PR #6 (investment accounts with no positions).
- **Agent:** Claude Code (Opus 5.5).
- **PR:** `feat/sync-holdings`.

## What was asked

1. "but why do I see no holdings". Every synced brokerage account showed its balance and the
   line "The bank reports this account's balance, not what it holds".
2. After the explanation and a proposed shape: "lets do this".

## What was found

- `services/simplefin.py` already parsed each account's `holdings` list into `ProviderHolding`
  (symbol, description, shares, market value, total cost basis). The only reader was
  `aggregator.infer_account_type`, which uses it to type an account as investment. Sync wrote
  no security, price or holding.
- ADR-0021 and ADR-0033 were written on the premise that SimpleFIN sends no holdings. The
  committed capture itself disproves it: "SimpleFIN Savings" holds 550 AAPL.
- Whether the live bridge sends holdings could not be confirmed. The agent API does not expose
  the payload, and nothing recorded a count. This PR makes that observable.

## What changed

- **ADR-0051** and `services/sync_holdings.py`: per account, after the balance, sync upserts
  a security (ticker, else description, else a per-currency cash security), a day price
  (market value ÷ shares, `auto`; a manual price for the day stands), and a holding with
  `source = 'simplefin'`. Lots are summed. Synced positions the bank stops reporting are
  removed. Hand-entered and history-backed positions are never touched. The account stays
  `stated`, so its value is still the bank's balance and the rest is the unaccounted-cash
  remainder.
- **Migration 0011** adds `holdings.source` (default `manual`). The downgrade removes only
  synced rows and the securities only they used. It has a live-data test.
- A synced position reads `quantity_source: provider`. A hand write to its quantity or basis
  is a 409. The holdings list says "from your bank".
- `RunCounts` gained `holdings_seen / written / removed` (in `run.finished`). A
  `holdings.synced` event per account carries the skip reasons, at `warning` when any line
  was skipped.
- Export/import carries `holdings.source`. A file without it imports as `manual`.
- ADR-0021 and ADR-0033 are marked amended. ARCHITECTURE.md's holdings row and risk row are
  updated.

## Verification

- Backend: ruff clean; 1,112 tests pass, including 9 new sync-holdings cases and the 0011
  migration case.
- Frontend: typecheck, design lint and 368 unit tests pass.

## After deploy

The next sync's `run.finished` event says `holdings_seen`. If it is 0 for the brokerage
accounts, the bridge is not sending holdings for them, and the "balance only" line is the truth.
