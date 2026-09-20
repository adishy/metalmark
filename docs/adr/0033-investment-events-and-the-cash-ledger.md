# ADR 0033: Investment events and the cash ledger — a buy is not a transaction

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Refines:** ADR-0032 (§2)
- **Related:** ADR-0008, ADR-0011, ADR-0017, ADR-0018, ADR-0020, ADR-0021

## Context

ADR-0032 §2 says investment `buy`/`sell` "leave cash flow, exactly as transfers do (ADR-0008)". That sentence
is satisfied by two different schemas, and nothing written down yet chooses between them:

- **(a)** A buy creates a `transactions` row on the investment account (the cash leg) *and* an
  `investment_transactions` row (the security leg), linked as a transfer pair would be.
- **(b)** A buy creates only an `investment_transactions` row. The investment account has no
  `transactions` rows at all.

The two differ in more than bookkeeping: (a) needs a rule that links and excludes the pair, and a rule for
what the account's transaction-derived balance means; (b) needs neither.

**ADR-0011 already contains the fact that decides it:** *"An investment account's balance is derived as
Σ(holding market values) in base currency."* An investment account's balance does not come from its
transactions. So under (a) the cash leg would be a row in a ledger **nothing reads**, and any report naive
enough to read it anyway would count the same money twice — once as the holding it bought, once as the cash
that left. A row that must never be read is worse than a row that does not exist.

## Decision

**1. Investment accounts have no `transactions` rows.** Not "few" — none. Their state is their holdings;
their history is `investment_transactions`.

Which makes ADR-0032 §2's phrasing wrong in a way worth correcting, because the distinction is load-bearing
later. Buy/sell are not *excluded* from cash flow; they were **never in it**. Exclusion is a filter applied
to a row that exists (the transfer case, ADR-0008); this is non-membership, and a filter that runs over rows
that do not exist is a filter that silently stops working the day someone adds one.

**2. `dividend`, `interest` and `fee` do enter cash flow**, read from `investment_transactions` by the
cash-flow report. They are the three of the five types that are genuinely income or expense (ADR-0032 §2's
table), and a report that only reads `transactions` would miss all three. That is a requirement on the
report, not a property that falls out of the schema.

**3. The boundary between the two worlds is a transfer, and `transfer_groups` already accommodates it.**

Money crossing in or out — a contribution from checking, a withdrawal to it — is one `transfer_groups` row
with a `transactions` leg on the cash account and an `investment_transactions` leg on the investment
account. Nothing about the schema needs to change to allow this: `transfer_groups` is a grouping row with no
count and no kind constraint on its members, and `transfer_group_id` is already nullable on the transaction
side. `investment_transactions` gains the same nullable FK.

The pair is excluded from cash flow by ADR-0008's existing rule, unchanged — which is what makes the
contribution stop reading as an expense. Without it, funding a brokerage account looks like spending the
money, and every household that invests would see a phantom expense equal to their contributions.

**4. Uninvested cash inside an investment account is a holding, not a balance.**

A `derived` account's Σ(holdings) includes a cash position (`security_type = 'cash'`). This is not new
machinery — it is exactly the object ADR-0021's "unaccounted cash plug" already describes, now with a
purpose beyond covering a discrepancy. Under this model the three events line up in one place:

| Event | cash holding | securities | account balance |
|---|---|---|---|
| Contribution from checking | +X | — | +X |
| Buy | −Y | +Y | **unchanged** |
| Dividend | +Z | — | +Z |

A buy cannot move the account's balance, which is the property that makes ADR-0032's appreciation term
computable at all: the only things that change a derived balance are contributions, withdrawals, income,
fees and price moves.

## Consequences

- **Positive:** the balance rule has one implementation instead of two; a buy cannot double-count because
  there is no second row to count; contributions stop reading as expenses; and the "no `transactions` rows"
  invariant is checkable in one query (`SELECT` an investment account's transactions → zero), which makes it
  a test rather than a convention.
- **Negative / costs:** an investment account's transaction list is empty, so the UI must render
  `investment_transactions` where it renders transactions elsewhere — a genuinely different view (quantity,
  price, security) and not a relabelling. And a **synced investment account has nowhere to put a synced
  transaction**: SimpleFIN reports investment balances but no holdings (ADR-0001), and v1 does not ingest
  investment transactions at all. That is a real limit, recorded rather than discovered later: such an
  account stays `stated` and its activity is invisible in v1.
- **Follow-ups:** realized gains (ADR-0020's average cost) read from the same `investment_transactions`
  history that the transfer legs and the unwind in ADR-0032 both depend on — three consumers of one table,
  which is the strongest argument yet for ADR-0020's single-authority rule.
