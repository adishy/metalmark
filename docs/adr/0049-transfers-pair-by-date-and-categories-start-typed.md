# ADR 0049: Identical transfers pair by date; households start with typed categories and a local categorizer

- **Status:** Proposed
- **Date:** 2026-09-24
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0007/0019 (provenance), ADR-0008 (transfers excluded from cash flow), ADR-0018
  (transfer auto-match), ADR-0048 (the agent API these were found through), agent record session 06

## Context

Read through the agent API, a live household's cash flow for September showed **−$120k**, against
roughly −$4k of real spending. Two causes:

1. **The matcher refused real pairs.** Two $20k moves on consecutive days produced two identical
   deposits. Each withdrawal therefore had two exact candidates, one on the same day and one a day off.
   ADR-0018's "exactly one candidate, or none" refused both, so $40k counted as income and $40k as
   spending.
2. **Nothing could mark a transfer to an account the app doesn't hold.** Only a transfer-typed
   category excludes an unmatched move (ADR-0008), and a household created by signup had **no
   categories at all**. That left about $115k of moves to the household's own brokerage accounts
   counted as spending.

## Decision

1. **The auto-match links the strictly closest matching candidate in time**, measured to the second. A
   tie is still ambiguous and still refused. Links are one-to-one: a leg linked earlier in the pass isn't
   free. This refines ADR-0018's automatic half; the picker is unchanged.
2. **Every household starts with a typed starter set.**
   - `services/default_categories.py` defines groups typed income / expense / transfer, each category
     with an emoji in `categories.icon`. A **Transfers** group (Transfer, Credit Card Payment, Loan
     Repayment) is part of the set.
   - Signup installs it. Migration 0009 installs it for existing households with **no** categories,
     and never beside a household's own. The migration keeps its own copy of the list.
   - "Uncategorized" stays the absence of a category, not a row.
   - Names of people are never in the set: it ships in a public repository.
3. **A local auto-categorizer.**
   - It is in-process, with no network, model or merchant database.
   - It files a row from the household's own hand-made history first, then keywords for the starter
     set, then a custom category's own name. The sign of the amount limits the choice to income or
     transfer for money in, and expense or transfer for money out.
   - A linked transfer is filed as Transfer, or as Credit Card Payment when a leg is a card or loan.
   - What it writes has provenance **`auto`**, ranked below `rule` and `user`: user > rule > auto >
     provider.
   - Sync fills only blank categories, after rules and the matcher.
   - The Admin action "auto-categorize all" re-runs the matcher over every unlinked row, then
     **overwrites** every non-split category. It confirms first, because it replaces choices made by
     hand. A row it has no guess for keeps what it had.
   - It learns only from `user` and `rule` rows, never from its own output.

## Consequences

- **Positive:** the report's picture matches the household's. Transfers between own accounts leave cash
  flow by default, and a new household's first sync lands mostly categorized.
- **Negative / costs:**
  - "Strictly closest" can still pair the wrong twin when the true leg posts later than an identical
    unrelated one. Unlinking is one click, and the same risk exists for any single-candidate match.
  - The keyword table is US-centric and will misfile. Every guess is `auto`, so a human or a rule
    outranks it, and the admin action says it overwrites.
  - "Auto-categorize all" can replace a human's choice with a guess when history is silent on that
    merchant. That is what was asked for, and the confirmation says so.
