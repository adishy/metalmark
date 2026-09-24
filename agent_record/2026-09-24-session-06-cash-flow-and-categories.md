# Session 06: Debugging cash flow through the agent API; categories, an auto-categorizer, and interface tweaks

- **Date:** 2026-09-24
- **Agent:** Claude Code (Opus 5.5).
- **Branch:** `fix/cashflow-and-categories` (worktree `.claude/worktrees/fix-cashflow`, branched from `c31c7db`).
  The main checkout's uncommitted login work is untouched.
- **How this session read the app:** entirely through the agent API (ADR-0048), using a `debug:read` +
  `agent:read` token against the owner's instance at `192.168.0.123:8791`. Every name below is a pseudonym;
  amounts and dates are real.

## What was asked

> can you see the expesne vs income graph? it looks a bit wonky to me? why is it all in one place? it says
> cash flow is ~-$100k which is not true - poke around this instance then let's brainstorm fixes. the main
> idea is day to day deltas should be small and understandable so money coming in and going out is
> identifiable

The same message added these tweaks:

1. Default categories, each with an emoji, each typed income / expense / transfer. The owner gave a starter
   list, to be cased and structured properly.
2. The Accounts page laid out like Monarch Money's:
   - filter options at the top right on mobile;
   - tabs per account type;
   - the net-worth chart and total front and centre.
3. The name users see is "MetalMark Money", in the README and all user-visible copy. The internal code name
   stays.
4. The transactions swipe view lets the user change the category.
5. A simple, quick, private auto-categorizer that knows the custom categories, plus an "auto-categorize all"
   button in Admin that warns it overwrites categories.

Two messages mid-turn:

- "login fields should be semantically marked such that a password manager can ID the pwd field and auto
  fill them — look up and implement best practice".
- "connections were added sept 22 or so".

## What the instance showed

- **Data:** 17 accounts, 173 transactions, **0 categories**, 0 rules, 1 SimpleFIN connection. All checks pass.
- **History starts 2026-08-10.** The connection was added about Sept 22, so SimpleFIN backfilled roughly 45
  days at once.
- **Cash flow at the report's default range**, `last-12-months`, auto granularity:
  - 13 monthly buckets; 11 are zero.
  - Aug: net +$2.0k.
  - Sep: income $46.2k, expense −$165.8k, net −$119.7k.
  - The Sankey shows the same: a single "Uncategorized" node on each side, income $53.0k, expense $170.6k.

### Why it looks "all in one place"

Auto granularity is picked from the *requested* span (365 days → month), not from the span that holds
data. Forty-five days of activity therefore become two bars at the right edge of an empty year. A daily or
weekly cut of the same data is what "day to day deltas" asks for.

### Why net is −$120k

Almost all of it is money moving between the household's own accounts. It counts as spending because
nothing marks it as a transfer.

| Moves | Amount | Why it counts |
|---|---|---|
| `other/a4d4ae → investment/a54485`, 9/10 and 9/11 | −$20k ×2, with +$20k ×2 on the other side | **The matcher refused both pairs as ambiguous.** Each −$20k has two exact +$20k candidates, 0 and 1 day apart, and `auto_match_transfers` links only when exactly one candidate matches. Both legs count: $40k of "income" and $40k of "expense". |
| `other/0eb937`, 9/08–9/11 | −$30k, −$20k, −$15.3k | Moves to an account the app doesn't hold. They have no counterpart leg, so only a transfer-typed category can exclude them, and the household has **no categories at all**. |
| `other/a4d4ae`, 9/09 | −$10k | Same. |
| `investment/a54485`, 9/22 | −$40k | Same. It is review-status `ignored`; ignoring a row doesn't exclude it from reports. |

Take those out and September is roughly $6k of income against $10k of spending. The rent-sized payments
(−$3.5k, −$1.5k, −$1.2k) are real spending.

Secondary observations:

- SimpleFIN reports most accounts as type `other`, including the one receiving the ~$3.2k paychecks. This
  matters for the type tabs in item 2.
- The login form already has `autocomplete="username"` / `"current-password"`. It lacks `name`
  attributes and a `method`.

## Plan

**A. Cash flow reads at the scale of the data.**

1. Clamp the cash-flow window's start to the household's earliest activity before resolving `auto`. The
   45 days above then resolve to `day`, and the chart starts where the data does. The echoed `start` says
   where it actually began.
2. Transfer auto-match: when several candidates match exactly, link the **strictly closest in time**. A tie
   is still ambiguous and still refused. Pairing is one-to-one, because a leg already linked in this pass is
   no longer free. This refines ADR-0018's automatic half and is recorded as a new ADR. A re-sync re-offers
   the whole SimpleFIN window, so the existing pairs link on the next sync. The Admin action below also
   re-runs the matcher over every unlinked row.

**B. Default categories.**

- One module defines the starter set: groups, each typed income / expense / transfer, and categories, each
  with an emoji in the existing `categories.icon` column.
- The set includes a **Transfer** group (Transfer, Credit Card Payment, Loan Repayment). That group is what
  lets the unmatched own-account moves above leave cash flow.
- New households get the set. A migration adds it to existing households **that have no categories**, so a
  household that already built its own is never touched (the live-data rule).
- "Uncategorized" is the absence of a category (`category_id IS NULL`), as it is everywhere in the reports.
  It is given an emoji in the UI, not made a row.
- The owner's two per-person family categories name people. They are left out of the repository and seed
  data, which is published. The owner adds them in Settings. The generic "Family" and "Family – Admin" stay.

**C. Auto-categorizer (local, no network).**

It scores each category using:

- keyword patterns for the default categories, matched on merchant + description;
- the household's own history: what merchants a human categorized, and how;
- the amount's sign: income and transfer categories for money in, expense and transfer for money out.

Behaviour:

- It runs on sync for rows with no category, after rules, and marks the category's provenance `auto`.
  Precedence is user > rule > auto > provider.
- Admin → "Auto-categorize all" re-runs the transfer matcher, then categorizes every transaction. It
  overwrites existing categories, behind a confirmation that says so. Splits are left alone.

**D. Swipe view:** a category picker on the card.

**E. Accounts page:**

- Net-worth total and chart at the top.
- Tabs: All, Cash, Credit, Investments, Loans, Other.
- Filters at the top right on mobile.

**F. Login fields:**

- A form with `method="post"`.
- `name`, `id` and `autocomplete` on each field.
- `type=email`, with `autocapitalize=none` and `spellcheck=false` on the username.
- The same on Signup, which uses `new-password`.

**G. Branding:** "MetalMark Money" in the README, the page title, the header and the sign-in pages.
