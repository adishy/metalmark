# ADR 0053: A recurring series describes transactions; it never owns them

- **Status:** Accepted
- **Date:** 2026-09-25
- **Deciders:** Agent A (implementation), lead engineer (review)
- **Related:** ADR-0005 (money is Decimal), ADR-0026 (owners are household data),
  ADR-0035 (a report reads each thing once), ADR-0036 (portable export),
  ADR-0043 (balances are signed), ADR-0052 (owner income, the shared cadence vocabulary)

## Context

The household wants to see what repeats: the rent, the subscriptions, the insurance that lands
once a year. Two things have to be built and only one of them is a table.

**The list** is something a person states — "Netflix is monthly on the card, and it costs
12.99" — and wants to add, rename, pause and delete without touching the ledger. **The
suggestions** are what the ledger already shows: a merchant charged on the 5th of every month,
four times running. Neither is derivable from the other, and the tempting shortcut — store a
`recurring` flag on the transaction, or store a "next occurrence" row that a job materialises
— is wrong in a way that is hard to undo: it writes an opinion about the future into the
ledger, and the ledger is a record of what happened.

The other requirement is that the two must agree. If a person accepts a suggestion and the
list then counts its occurrences by a different rule than the detector used to propose it, the
person sees a suggestion they already accepted, or a series whose count does not match the
charges on the screen.

## Decision

**One new household table, `recurring_series`** (RLS, like every household table —
ADR-0014/0025; migration `0014`, shape-detecting and policy-before-grant): `name`, an optional
`merchant` filter, `account_id` / `category_id` / `transaction_id`, a signed `amount` and its
`currency` (ADR-0043/0005), a `cadence`, an optional `next_due_date`, and `is_active`.

**A series owns nothing.** There is no join table to transactions and no stored counter. Which
transactions a series covers is answered at read time by **one predicate**, `_matches`: the
account when the series names one, the merchant as a case-insensitive substring of the
transaction's merchant-or-description text, and the direction of the amount. The occurrence
count and the "this pattern is already tracked" check both call it, so they cannot disagree —
which is the requirement above, expressed as a single function rather than as two rules kept in
step by hand. A series that matches nothing reports zero occurrences, which is the truth and is
exactly what a person needs before deleting it.

**The three foreign keys are `SET NULL`, not `CASCADE` and not required.** Deleting the
transaction a series was picked from must not delete the series (provenance is a seed, not a
dependency); deleting an account must not delete the person's list — the series just stops
matching. `transaction_id` is the "pick a transaction" path: a `POST` that carries it fills
whatever the body left blank from that row, while a field the body *did* send always wins, so
accepting a suggestion is posting the suggestion back.

**Detection is a read-time pure function, never a stored judgement.** `detect()` groups the
household's transactions by `(account, match text, direction)`, drops hidden and pending rows,
keeps a three-year window, and asks three questions of what is left: the **median gap** must sit
within 25% of a cadence's nominal length (so "monthly" accepts 23–37 days and a 60-day gap is
nobody's month); the gaps' **spread** must be within 3 days or 35% of the median (a weekly bill
moving a day is fine, a missed week is not); and the **amounts** must stay within 15% or a
dollar of the median (a utility bill varies; a coincidence does not). Three occurrences make a
pattern, two for a cadence a quarter or slower — waiting for a third yearly bill would take
three years. The amount and the category come from the median and the mode, so one winter spike
does not become "what this series is".

**A suggestion a series already covers is not offered — including a paused one.** Pausing means
"stop counting this in my totals", not "ask me about it again", so `is_active` takes a series
out of the totals (and only the totals) while leaving it covering its pattern.

**The monthly figure is derived, not stored:** `amount × annual multiplier ÷ 12`, quantized to
the cent, with a multiplier table that states the same convention as ADR-0052's pay frequency —
a test pins the five shared cadences equal, so "biweekly" cannot come to mean 26 a year in one
feature and 24 in the other. Totals are **per currency**, base currency first, and never add one
currency to another (ADR-0005/0035).

**Shape:** the tab lives at Insights → Recurring, behind `GET/POST/PATCH/DELETE /recurring` and
`GET /recurring/suggestions`, open to any household member like the ledger and categories
(ADR-0026 — owners are attribution, not access). New response fields are registered in
`app/agent/policies.py` (names and free text pseudonymized or labelled, never `Keep`), and the
series are user-written data, so they round-trip through `services/portability.py` (ADR-0036)
with their account, category and picked-from transaction remapped. That last one is the first
reference in the document to name a transaction by id from outside the transaction block, so
the transaction importer's id map joins the import remap; a series whose charge the document
does not carry imports without it and says so in the warnings.

## Consequences

- **Positive:** nothing about the future enters the ledger, so editing or deleting a series is
  a no-op for history and re-importing a statement can never conflict with it. The detector's
  judgement is testable as arithmetic — the unit tests are hand-computed gaps and amounts, not
  fixtures. Adding a series by hand and accepting a suggestion take the same write path.
- **Negative / costs:** the occurrence count is a scan of the household's transactions per
  read (`_transactions` fetches the non-hidden, non-pending rows once per request), which is
  fine at one household's scale and deliberately not fine at a hundred — the fix, if it ever
  matters, is a narrower query per series, not a stored counter. Matching on merchant *substring*
  means a series named too broadly ("Amazon") will count things a person did not mean; that is
  visible in the count they see, and narrowing the merchant is the correction. Deleting a series
  loses the person's list only (the ledger is untouched), which is why `0014`'s downgrade is
  documented as lossy.
- **Follow-ups:** nobody is notified when a series' `next_due_date` passes — the field is
  display-only (ADR-0037's notification machinery is the place that would change, if it ever
  does). Detector scale and any "what changed since last month" view are future work.
