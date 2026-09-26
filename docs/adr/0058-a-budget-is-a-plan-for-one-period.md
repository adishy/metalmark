# ADR 0058: A budget is a plan for one category in one period, compared to the spending report

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** Agent (implementation), lead engineer (review)
- **Related:** ADR-0005 (money is Decimal, never float), ADR-0017 (`base_amount` cache, dated
  FX), ADR-0035 (a report reads each thing once), ADR-0049 (households start with typed
  categories), ADR-0007/0019 (provenance), ARCHITECTURE.md §2 "Categories & budget"

## Context

The household asked for "the ability to set and surface per-category wise budget". Nothing in
the app models one today: `ARCHITECTURE.md` §2 carries a one-line stub
(`budgets (v1.1) — id, household_id, category_id, month, amount`) and `categories.rollover`
exists in the schema and is read by nothing. Both were placeholders for this.

The feature has one hard requirement, and it is a correctness one. A budget bar says "$412 of
$600"; the app already prints a Spending report saying what was spent, per category, over a
window. If those two numbers can disagree, the feature is worse than absent — the product's
promise is that two figures about the same money are the same figure. So the comparison side is
not a design choice: it must be `reports.spending_by_category`, the one place the exclusions
live (transfers, hidden rows, hidden accounts, transfer-typed categories, owner scope), and
which already reports a split parent through its children. Everything below follows from that
anchor.

Two things in the tree shape the answer further. *Uncategorized* is not a row — it is
`category_id IS NULL`, deliberately (`services/default_categories.py`) — while a household may
*also* create a real category named "Uncategorized". And `categories.rollover` and a `NOT NULL`
foreign key together decide the `ondelete` behaviour, since `SET NULL` on a `NOT NULL` column
raises.

## Decision

**One new household table, `budgets`** (RLS, like every household table — ADR-0014/0025):
`id, household_id, category_id, period, amount, created_at, updated_at`, with a unique
`(household_id, category_id, period)`.

Departures from the `ARCHITECTURE.md` stub, each deliberate:

- **`month` is named `period`.** The value stored is `periods.Bucket.period` at granularity
  `month` — the *unclipped* first day of the calendar month — which the periods module already
  documents as "the right thing to key a series on". A column named `month` would fold the
  resolution into the column's identity, and it is not part of it: v1 writes month starts only,
  but a later quarter or year plan is a different *value* in the same column, not a schema
  change.
- **`category_id` is `NOT NULL` and `ON DELETE CASCADE`.** `NOT NULL` because a budget for
  *Uncategorized* has nothing to point at: that bucket is the absence of a category, not a row.
  A household that created a real category called "Uncategorized" has a real id and can budget
  it like any other. `CASCADE` rather than the `SET NULL` the transaction and recurring-series
  tables use — `SET NULL` on a `NOT NULL` column raises — and it is the honest outcome anyway:
  the plan was for that category, and there is no longer a category to plan for.
- **`amount` is `MONEY` — `Numeric(19,4)` — not `Numeric(18,2)`.** This column is compared
  against and subtracted from `spending_by_category`'s totals, which are sums of
  storage-quantized ledger amounts, so it takes the ledger's scale (ADR-0005) rather than the
  income tables'. A narrower column would round a subtraction the report can represent.
- **No `rollover`, no `owner_id`.** See "Out of scope".

A CHECK holds `amount >= 0`: a budget is a magnitude, and a negative one is a typo with no
meaning. Zero is allowed — "spend nothing on takeout this month" is a real thing to say, and
the UI tells it from "no budget" by the row's existence, not by the number.

**Budgets are for expense categories only.** `spending_by_category` counts money *out*; an
income category's plan would be compared against a quantity the primitive does not compute (an
income target is a floor to reach, not a cap to stay under). The service refuses a budget whose
category's group is not `expense` — a category's type *is* its group's (ADR-0049) — and the
picker offers expense categories only. The comparison is then always like for like.

**Stated in the household's base currency.** A budget is one number in
`households.settings.base_currency`; the spend beside it is the `base_amount` cache, converted
at the transaction's own date (ADR-0017, dated and not today's rate), summed by the report. A
household that changes its base currency moves its budgets with it, as it does every other
stored figure.

**One read path, and it is the report's.** `GET /budgets?period=…` returns the period's rows —
the union of categories with a plan and categories with spend — each carrying
`budget: Decimal | None` and `spent: Decimal`, plus `total_budget`, `total_spent`,
`budgeted_spent` and `unbudgeted_spent`. `spent` comes from `reports.spending_by_category`
over exactly the period's own window (`period` … `periods.period_end(period, "month")`), and
the totals are arranged so `total_spent` **is** that report's `total` for the same window —
including the rows no budget can cover (uncategorized, investment fees), which land in
`unbudgeted_spent`. Recomputing spend from the ledger any other way is exactly the failure this
ADR exists to prevent.

Writes are `PUT /budgets/{category_id}?period=…` (idempotent upsert on the natural key — the
row's identity *is* the pair) and `DELETE /budgets/{category_id}?period=…`. The source is
`user`: nothing automatic writes a budget, so there is no `field_sources` column and no
provenance ladder to walk.

## Out of scope, and why

- **Rollover is not implemented.** `categories.rollover` stays a column read by nothing. A
  period's budget is that period's budget; carrying an unspent remainder forward changes what a
  period's *effective* allowance is, which is a change to the read path rather than to this
  table — and shipping it without a UI for the flag would be a setting nobody can find.
- **No per-owner budgets.** A budget is a household statement. The spending report can be
  narrowed to one owner, so a per-owner spend against a household plan would compare two
  different populations. `GET /budgets` therefore takes no `owner_id`: it always reports the
  whole household.
- **Monthly only.** Weekly, quarterly and annual plans are a different window resolution with
  different questions ("which week?"). The column can hold them; v1 does not write them.

## Consequences

- **Positive:** the figure beside a budget is the figure the Spending report prints, by
  construction rather than by agreement — one call site, one set of exclusions, pinned by a
  test holding `total_spent` to `spending_by_category`'s total over the same window. The spend
  side needs no new query shape: `budgets` is the only new table.
- **Negative / costs:** the Uncategorized bucket accumulates spend that no budget can cover.
  That is surfaced as `unbudgeted_spent` rather than hidden, and the UI names it, but it is a
  gap a reader has to understand. Budgets are period-keyed and therefore history: editing this
  month does not touch last month's, which is correct and also means "set it once and forget
  it" is not something the app can offer until a plan can be copied forward (deferred).
- **Follow-ups:** rollover (a new ADR, since it changes what a period's budget *means*);
  copying the previous period's plans forward; per-owner budgets; and a budget on an income
  category, which needs a target-vs-actual report of its own rather than the spending primitive.
