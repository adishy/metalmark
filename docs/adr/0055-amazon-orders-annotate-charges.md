# ADR 0055: Amazon orders annotate the charges they explain

- **Status:** Proposed
- **Date:** 2026-09-25
- **Deciders:** Aditya Shylesh (pending)
- **Related:** ADR-0007/0019/0049 (provenance), ADR-0031 (splits), ADR-0030 (imports refuse what
  they cannot read), ADR-0048 (agent API), research: `docs/research/amazon-orders.md`

## Context

An Amazon card charge carries no information about what was bought. Amazon has no consumer API.
The sources are Amazon's own data export (complete, manual, no credentials), scraping Amazon as
the person (automatic, needs their password and TOTP seed on the server, fragile), a browser
extension in the person's own signed-in session (automatic while a desktop browser is open, no
credentials held), and order emails (automatic, incomplete for per-shipment charges). Amazon
charges the card per shipment, not per order.

## Decision

We will:

1. Store Amazon orders and their items as **annotations** on the transactions they explain,
   linked many-to-one (several shipments of an order are several charges). Items never change
   a transaction's amount or date.
2. Match shipments to charges by exact amount within −1/+5 days of the ship date, then whole
   orders for orders charged once; a tie is shown to the person, never picked.
3. Take orders from **the export upload first**, and from **a browser extension** that reads
   Amazon's Transactions page for the ongoing feed. Both post through one import route.
4. **Not** hold Amazon credentials on the server, and not scrape Amazon server-side.
5. Write a category suggested from items at `auto` provenance; a split by items is a
   one-tap user action (ADR-0031), not automatic.

## Consequences

- **Positive:** no credential risk; the export backfills years; the matcher is pure and tested
  apart from any source; a later email source plugs into the same import.
- **Negative / costs:** the ongoing feed needs a desktop browser; nothing is automatic on iOS
  alone. The extension parses Amazon HTML and will need maintenance.
- **Follow-ups:** measure the real match rate with `scripts/amazon_match.py` before choosing
  tolerances for split tender; returns/refunds file; item titles get an agent-API policy
  (`Label`).
