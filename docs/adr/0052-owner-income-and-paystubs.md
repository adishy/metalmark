# ADR 0052: Owner income profile and paystubs, as lines that keep a kind

- **Status:** Accepted
- **Date:** 2026-09-25
- **Deciders:** Agent A (implementation), lead engineer (review)
- **Related:** ADR-0005 (money is Decimal), ADR-0026 (owners are household data), ADR-0036
  (portable export), ADR-0007/0019 (field provenance)

## Context

The household wants tax modelling eventually — effective rate, withholding checks, "what if
income changes" — but that is future work. This ADR only lays the foundation: a place to
record what each owner (a person, not a login — ADR-0026) earns annually, and what an actual
paystub says, in enough detail that a later tax model has real inputs instead of a single
"take-home pay" number.

The foundation has to be **modellable**, which is the whole design problem: a paystub is not
one number, it is gross pay minus a handful of *kinds* of deduction (pre-tax retirement and
insurance, taxes withheld, post-tax deductions) plus employer-side amounts that never touch
the employee's pocket (an employer 401k match). A future tax model needs to tell those kinds
apart — pre-tax deductions lower taxable income, taxes withheld are what it is checking, an
employer contribution is not the owner's money at all — so the schema keeps the kind on every
line rather than storing a paystub as an opaque total.

This is explicitly **not** wired into Insights yet. It lives in Settings → Owners, as data
entry only.

## Decision

Three new household tables (RLS, like every household table — ADR-0014/0025):

**`owner_income_profiles`** — one row per owner (unique `owner_id`, FK `owners` ON DELETE
CASCADE): `annual_gross_income` (nullable — a household may not know or want to state it),
`pay_frequency` (nullable enum-as-string: weekly/biweekly/semimonthly/monthly/annual),
`filing_status` and `tax_region` (nullable, free-ish but constrained where we can be:
`filing_status` is a closed set, `tax_region` is a short string like `"US-CA"` because tax
jurisdictions are not a set we can enumerate). `currency` defaults to the household's base
currency at creation time — an owner is a household concept and its income is typically in the
household's own currency, but nothing stops it from differing (an income in a second currency
is stored as its own number, not auto-converted; a later tax model reads `currency` rather than
assuming base).

**`paystubs`** — one row per actual paystub an owner enters: `pay_date` (required — the one
fact you always know about a paystub), `period_start`/`period_end` (nullable — not every stub
states a period explicitly), `employer`, `gross`, `net`. **`paystub_lines`** — the detail:
`kind` (`earning | pre_tax_deduction | tax | post_tax_deduction | employer_contribution`),
`label`, `amount`, `ytd_amount` (nullable — not every stub carries a YTD column), `position`
(display order, since a paystub's own layout order is meaningful to the person reading it and
sorting alphabetically would scramble a document they are transcribing from).

**Validation, kept deliberately simple:** every amount is ≥ 0 (no negative-adjustment lines in
v1 — a correction is a separate earning line, not a signed one, which keeps "sum the earnings"
unambiguous). When lines are present, the server checks `gross == Σ earning lines` and
`net == gross − Σ pre_tax − Σ tax − Σ post_tax` (employer_contribution is excluded from both
sides — it is the employer's money, never the employee's gross or net) and 422s with a clear
message on a mismatch. A paystub with **no lines** is allowed — someone who wants to log "I got
paid $X net on this date" and stop there should be able to, and the header-only shape is not
second-class.

A derived **summary** on `GET .../income-profile` (`annualized_gross`, `ytd` totals by kind for
the current calendar year, `effective_tax_rate`) is computed at read time from the profile and
the owner's paystubs, not stored — the same "read once, round where it says it rounds"
discipline as reports (ADR-0035), and it means there is nothing to keep in sync when a paystub
is added or edited.

## Consequences

- **Positive:** a later tax model has typed, per-kind data to work from rather than a single
  take-home number; the paystub-lines editor doubles as a worksheet that shows its own
  arithmetic (the gross/net check) as someone transcribes a real stub.
- **Negative / costs:** three new tables and a nested replace-all write path (lines are
  replaced wholesale on paystub create/update, like transaction splits) for a feature nothing
  reads yet outside Settings. The `kind` vocabulary is fixed at five values; a household whose
  paystub has a line that fits none of them (rare, but real payroll systems have oddities) has
  to force it into the closest kind or omit it — accepted for v1 rather than open-ended free
  text, because free-text kinds are not modellable, which was the whole point of this ADR.
- **Follow-ups:** Insights wiring (effective rate, "what changed" against a paystub) is
  explicitly future work, not this change. A tax model that needs jurisdiction-specific
  brackets will need more than `tax_region` as a string; that is also deferred.
