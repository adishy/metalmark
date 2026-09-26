# ADR 0054: The allocation view can include bank cash

- **Status:** Accepted
- **Date:** 2026-09-25
- **Deciders:** household + Claude
- **Related:** ADR-0011 (the consolidated allocation view), ADR-0021 (the stated-balance plug this
  reuses the "cash" group for), ARCHITECTURE.md §2, §4

## Context

`GET /api/investments/allocation` (ADR-0011) answers "how is the household's **portfolio** split" —
every holding across every investment account, grouped by security, type, account or currency. It
never counted a checking or savings balance, because it is an investments view and those are not
investments.

That is the right answer to "how is the portfolio allocated" and the wrong answer to the question
the owner actually asks looking at it, which is closer to "where is all our money". A household
that keeps a large cash buffer outside its brokerage sees a portfolio that looks 100% invested —
Groceries money and Robinhood money read identically — and has to go compute the real picture by
hand from the Accounts page. ADR-0021 already solved a narrower version of this for an investment
account's own uninvested cash (the "unaccounted cash" plug); this extends the same idea to the
household's bank accounts.

## Decision

The allocation endpoint gains `include_cash_accounts: bool = False`. When true, every **asset-side
depository account** (`accounts.type = 'depository'` — checking, savings, cash-like; the vocabulary
is `app/schemas/ledger.py:ACCOUNT_TYPES`) contributes its current balance, converted to base with
the same FX and balance-as-of rules `net_worth_points_by_account` already applies (ADR-0043,
ADR-0044, ADR-0045) — not a reimplementation, a call to that function. It lands in:

- the `cash` group under `group_by=type` and `group_by=security` — the **same key** ADR-0021's plug
  already uses, so a household's real bank cash and an investment account's uninvested cash read as
  one "Cash" line rather than two things a reader has to add themselves. The row's label switches
  from "Unaccounted cash" to "Cash" whenever `include_cash_accounts=true`, because at that point the
  row can hold more than the plug and the narrower name would misdescribe it.
- an account group of its own under `group_by=account`.
- its own currency's group under `group_by=currency`.

Liabilities (`credit`, `loan`) and investment accounts are structurally excluded — the query that
finds depository accounts cannot see either — so nothing here can double-count an investment
account's own cash (already counted as a holding or the ADR-0021 plug) or count a card's debt as
if it were cash on hand.

Every row also gains **`sources`**: `[{account_id, account_name, institution, value_base,
share_of_group}]`, listing which accounts a row is made of, each with its share of that row. A
`group_by=security` row's sources additionally carry `quantity` and `price`/`price_date` — the one
grouping where "one account, one price" is a fact about the row rather than a mix of several
holdings at different prices. This is not cash-specific: it is what lets the frontend's tap-through
detail sheet (feat/insights-allocations) answer "which accounts" for *any* row, cash or not.

Default is `false`. Nothing about the existing view changes unless a caller asks for cash — a
stored link or a screenshot taken before this ADR keeps showing exactly the same total.

## Consequences

- **Positive:** one view, opt-in, answers "where is all our money" without a second read against
  Accounts. Percentages and totals still sum to 100 / the true total with cash folded in, the same
  invariant ADR-0021's plug already guaranteed for the narrower case. `sources` is a small, general
  addition that also improves the view when cash is off.
- **Negative / costs:** the endpoint now does one more query (depository accounts) and, when the
  flag is set, one `net_worth_points_by_account` call — cheap (it is already how net worth answers
  the same question for the whole household) but not free. The "Cash" label change when the flag is
  on is a small behavior difference a reader has to learn once. `sources` grows every response
  somewhat; for a household with many accounts contributing to one group this is the point, not a
  cost worth trimming.
- **Follow-ups:** the frontend toggle defaults *on* (per-viewer, in `localStorage`) even though the
  API defaults off — the two defaults are allowed to differ, because "the household's own choice,
  remembered" and "an API a stored link should not silently reinterpret" are different questions.
  Regenerate `contracts/openapi.yaml` (done in this change). Agent policies for the two new response
  shapes (`AllocationOut.include_cash_accounts`, `AllocationSourceOut`) are registered in the same
  change (`app/agent/policies.py`).
