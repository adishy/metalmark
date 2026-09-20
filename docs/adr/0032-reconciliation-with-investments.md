# ADR 0032: Reconciliation with investments — a third term, and the residual stops being a tautology

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Refines:** ADR-0017
- **Related:** ADR-0005, ADR-0008, ADR-0011, ADR-0020, ADR-0021, `docs/PLAN-v0.9.md` decision A, `services/reports.py`

## Context

Every report is tested against one identity (`services/reports.py:4-9`):

```
Δ net worth (base) = net cash flow (base) + currency revaluation
```

Investments break it twice, in two different ways, and the two failure modes are not equally dangerous.

**The obvious one: a buy is not a cash flow.** Buying $1,000 of stock moves $1,000 from cash into
securities *inside* net worth. Net worth is unchanged; cash flow, if the transaction is counted as an
expense, is −$1,000. The identity needs a compensating term or it will not hold. ADR-0008 already solved
this exact shape for transfers — a movement between two of the household's own asset classes is not income
or expense — so the mechanism exists.

**The dangerous one: a market move has no transaction at all.** A price changes, net worth changes, and
there is no row anywhere in the database that recorded it. It appears in net worth and in nothing else.

And here is why that is the dangerous one. The identity is not *checked*, it is *constructed*: the docstring
says so plainly — *"We compute revaluation as the residual so the identity always holds."* The right-hand
side is defined as whatever makes the left-hand side work. So the identity cannot break. It will keep
holding perfectly while the label on one of its terms becomes a lie.

Today `currency revaluation` is the only unnamed term, and ADR-0017 named it deliberately. Add investments
and the residual silently starts absorbing market moves too — so the report keeps reconciling, the test
keeps passing, and the number labelled "currency revaluation" is now part FX and part stock market. That
failure is invisible for weeks and then presents as a rounding dispute.

**The scarce resource here is not the third term. It is the number of residual terms, and there is room for
at most one.** That is the decision this ADR is really about.

## Decision

**1. The identity gains a third term.**

```
Δ net worth = net cash flow + currency revaluation + market appreciation
```

**2. `buy` and `sell` leave cash flow, exactly as transfers do.**

They are movements between asset classes, not income or expense, and they are excluded by the same
mechanism ADR-0008 established rather than by a second rule that has to agree with it. The four investment
transaction types then split cleanly (ADR-0011):

| Type | In cash flow? | Why |
|---|---|---|
| `buy`, `sell` | **No** | A movement between two asset classes the household owns |
| `dividend`, `interest` | Yes, as income | Money enters the household and stays in |
| `fee` | Yes, as expense | Money genuinely leaves the household, and calling it a transfer would hide a real cost |

**3. `market appreciation` is computed from the price series, not from what is left over.**

This is the load-bearing sentence. `Σ(holdings × price change over the window)`, from `security_prices` and
`holdings` — a number derived from data, which can therefore be **wrong**, and can therefore be *checked*.

Defining it as a second residual would have been the easy move and would have achieved nothing: a residual
absorbs every error you fail to name, so adding a term to a tautology leaves a tautology with more words in
it. The term has to be independently computable or this ADR is decoration.

**4. `currency revaluation` is computed too, and the residual becomes one explicit field: `unexplained`.**

Same argument, applied to the term ADR-0017 left as the residual. Revaluation is computable from foreign
balances and rate movement; it does not need to be the leftover.

So the response gains a field, and the identity becomes a **test rather than a definition**:

```
unexplained = Δ net worth − cash flow − revaluation − appreciation
```

`unexplained` is reported, and the assertion is that it is **zero** for a seeded household (to
quantization). It will not always be zero in the world, and those cases are the point: a missing FX rate
(ADR-0017 §3's "no rate" state) and a stale security price both land here rather than being silently
absorbed by a term that claims to explain them. When it is nonzero the response says why.

**5. Net worth includes securities at the latest price on or before the point's date**, and the report
states how stale that price is.

Prices are manually entered in v1 (ADR-0021's and the README's deferred-prices position), so a household
that stops entering them gets a net-worth series that quietly freezes at the last price forever. Freezing
is the correct *arithmetic* and an unacceptable *silence*: the staleness is surfaced next to the figure,
because a flat line that means "no new data" and a flat line that means "the market was flat" are
different facts and must not render identically.

**6. A `stated` investment account keeps ADR-0021's unaccounted-cash plug**, so allocation and net worth
reconcile for an account whose balance came from the provider rather than from holdings. Appreciation is
**not** computed for a `stated` account — there are no holdings to compute it from, and inventing a plug
holding that appreciates would be inventing a market return.

## Consequences

- **Positive:** the invariant stops being a tautology and starts being able to fail, which is the only state
  in which it is worth having. A buy no longer reads as spending. A market move is named and attributed
  rather than laundered through an FX line. Every one of those is a property the reports did not have.
- **Negative / costs:** three computed terms where there was one residual means **three ways to be wrong**,
  and a bug in `appreciation` now shows up as a nonzero `unexplained` rather than as a silently absorbed
  number — which is better, but it is a new class of visible failure that has to be explained to the user
  rather than smoothed away. `revaluation`'s value will *change* for existing multi-currency households,
  because it stops being the residual; if it differs, that difference is a real finding about the existing
  code and not a regression in this one.
- **The residual stays, deliberately, as exactly one field.** If a future workstream needs a fourth term,
  the term gets computed and `unexplained` stays alone. Two unnamed terms cannot be told apart.
- **Follow-ups:** automatic price fetching is the obvious next step and is out of scope for v1; when it
  lands, the staleness warning becomes an update-freshness indicator rather than a manual-entry nag.
  Position-level FX P&L remains deferred (ADR-0017 §5) — this ADR decomposes the *total*, it does not
  attribute a currency move to a security.
