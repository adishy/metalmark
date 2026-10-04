# ADR 0059: Account-local holding overrides survive sync

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** ADR-0051 decision 3 (manual editing prohibition)
- **Related:** ADR-0007, ADR-0019, ADR-0034, ADR-0036

## Context

A bank's position can lack a useful name or type, or carry an incorrect quantity or valuation.
The household needs to correct these within an account without changing another account
holding the same security or losing its correction on the next sync.

## Decision

Holdings carry optional account-local name, symbol, type and total-value corrections. The
underlying security identity and provider source remain intact for matching, and account-local
metadata and valuation never rewrite shared security metadata or its price history.

1. **A value is a total, pinned to a quantity and a date.** The API accepts a total market
   value in the security's quote currency and stores three things together: the total at the
   money scale, the position's quantity at that moment, and the day it was set (the ledger's
   today, chosen by the server). A check constraint keeps the three all null or all set.
   - Valuation uses it on and after that day; before it, the price series values the position
     as it always did.
   - At the pinned quantity the value is the stored total **exactly** (a total of 1 for
     300,000,000 units is 1, which no eight-decimal unit price can say). At any other quantity
     it is `total × quantity ÷ pinned quantity`, rounded to the money scale: the unit price is
     what was pinned, so a later trade, a quantity edit or a bank quantity update scales the
     value instead of leaving a fixed total that reads as a loss or a gain.
   - The pinned quantity is the position's — the one recorded trades give when they exist
     (ADR-0034) — and the total must have its sign.
   - `as_of` keeps its one meaning, the day the quantity was confirmed. It neither gates the
     value nor moves when a label is edited. A metadata-only edit does not move the value's
     date either; setting the value again does.
   - Reads report the total and its date on the holding and on its valuation, with a derived
     unit price for display, so a hand-set value is never presented as a market quote or
     counted in an account's "oldest price used".
2. **Bank updates stop per field, not per row** (provenance: user > provider, ADR-0007/0019).
   A name, symbol, type or value written to a bank position is the household's and sync never
   writes those columns; the bank keeps updating the quantity and basis, and removes the
   position when it stops reporting it. Writing the quantity, cost basis or `as_of` of a bank
   position sets the override flag: sync then leaves the row alone (counted as
   `user_override`) but still records the bank's price for the security. "Use bank updates"
   clears the flag and every local correction; the next sync refreshes quantity and basis.
   Manually added positions remain manual.
3. **The type correction is a label.** It changes how the position reads and where it groups in
   the allocation. Whether a position is cash for valuation, history and appreciation is the
   security's own type, everywhere — otherwise re-typing a fund as cash in one account takes it
   out of the market values while its trades stay in the buys.
4. **Reports say when a value is the household's.** A position valued by a hand-set total is
   priced for the appreciation term from the day the total applies. When that day falls inside
   the period, the step from the market price to the total is included and a warning names the
   holding and the date.

The provider's stated account balance remains authoritative for net worth. Recorded trade
history still owns quantity and basis (ADR-0034). Portable export and import carry the
corrections exactly and validate them on the way in; an export written before the total
carried its own quantity and date imports with the holding's quantity and its `as_of` (or the
import date). Migration 0016 adds nullable columns, a false flag and the check constraints; it
does not rewrite existing financial data.

## Consequences

Corrections survive refreshes without affecting other accounts, and a label or a value no
longer costs the household its bank-fed quantity and prices. A hand-set value is a unit price
from a day onward, not a replacement for a security's historical price series: it does not
track the market afterwards, and it stays in force until it is cleared or set again. A bank
position with only labels or a value is still removed when the bank stops reporting it, and its
corrections go with it. Downgrading preserves quantity/basis/date/source but removes metadata,
value corrections and the override marker; take a backup before a downgrade.
