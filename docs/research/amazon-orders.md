# Amazon order details on Amazon charges — research and spike

- **Date:** 2026-09-25 (session 09)
- **Branch:** `spike/amazon-orders` — a matcher and a dry-run script, no schema, no UI.
- **Decision record:** ADR-0055 (Proposed).

## The problem

A card statement says `AMZN Mktp US*2K4LJ0XY3  −$48.72`, and nothing else. The
categorizer can only file it under one category, and the person has to remember what it
was. What we want on the transaction: the items (titles, quantities, prices), the
order number, and a suggested split by category when one charge covers items from
different categories.

Two facts shape everything below:

1. **Amazon charges per shipment, not per order.** A three-item order that ships in two
   boxes is two card lines. Matching on the order total finds neither. The order history
   has one row per item with its ship date, so shipments can be rebuilt from it.
2. **Amazon has no consumer API.** Every source is either a file Amazon generates for
   the person, or Amazon's website read as the person.

## The sources

| Source | Automation | Credentials we'd hold | Completeness | Breakage risk |
|---|---|---|---|---|
| **A. "Request your data" export** (Order History CSV in a zip) | Manual: request, wait for the email (hours to days), download, upload | None | Every order since the account opened, per item, with ship dates, tax and `Total Owed` | Low: column renames once (handled by the alias table) |
| **B. Server-side scraping** (e.g. the `amazon-orders` Python library, MIT) | Full, on a schedule | Amazon password **and** the TOTP secret, on the server | Orders, items, and the "Transactions" page (the actual card charges, with order numbers) | High: Amazon changes pages and bot checks; captchas need a paid solver; the account can be locked |
| **C. Browser extension** (Monarch's "Retail Sync" and the open-source `monarch-amazon-sync` work this way) | Automatic whenever a desktop browser is open; nothing on iOS | None: it runs in the person's own signed-in session and posts to our API with an app token | Same pages as B, including the Transactions page | Medium: the extension parses Amazon's HTML, but a human-driven browser does not trip bot checks |
| **D. In-app web view** (Copilot's approach, from its help pages: "login with your Amazon account" inside the iOS app) | Automatic in the app | Session cookie on the device | Same as B/C | Medium; needs a native app, which we don't have |
| **E. Order emails** (JMAP/IMAP on the mailbox, e.g. Fastmail's API token) | Full, server-side | A read-only mail token | Order confirmations carry items and the order total; shipment emails are inconsistent about amounts | Medium: email templates change; per-shipment charges are not reliably in them |

Copilot matches "if the order amount matches and the dates are within 2 days" (their help
centre). Monarch's extension syncs the last three months first, then daily while the
browser is open, and uses a model to split and categorize.

## Recommendation

**Build the matcher and storage once, then feed it from two sources, in this order:**

1. **The export upload (A)** — ships first. It is complete, it needs no credentials, and
   it backfills years. The person does it a few times a year, or once and then relies on 2.
2. **A small browser extension (C)** for the ongoing feed. It reads the Amazon
   *Transactions* page (`/cpe/yourpayments/transactions`), which lists each card charge
   with its amount, date, card and order number — exact matching with no guessing about
   shipments — and the order pages for items. It posts to a new
   `POST /api/imports/amazon` with a scoped token. No Amazon credential ever reaches the
   server.

**Not recommended:** B. Holding someone's Amazon password and TOTP seed on a home server
to run a scraper that needs captcha solving is the highest-risk option here, for the
least benefit over C. D needs a native app. E is a reasonable later add-on because the
owner's mail is on Fastmail (JMAP API tokens are simple), but it can't see per-shipment
charges reliably, so it would supplement A/C, not replace them.

## What the spike built

- `backend/app/services/amazon_orders.py`: parses both header dialects of the export,
  refuses a file that isn't one, skips cancelled rows, and rejects ambiguous money (`21,65`
  is an error, `1,299.00` is read). It groups items into shipments by (order, ship day),
  then matches in two passes: each shipment to a charge of exactly the same amount within
  −1/+5 days of shipping, then orders nobody matched as one charge (digital orders,
  single-charge orders). A tie is reported and left unassigned. Each charge is used once.
- `backend/tests/unit/test_amazon_orders.py` + `tests/fixtures/amazon/order_history.csv`:
  split shipments, a single-charge order, a cancelled order, a tie, a non-Amazon charge
  with the same amount, the window, and both number formats.
- `scripts/amazon_match.py`: a dry run against your own files — the Amazon export and an
  account CSV exported from the app. It prints match counts (and with `--items`, the
  items). Nothing is written.

### Try it on real data

1. amazon.com → Account → *Request your data* → *Your Orders* → submit, confirm the email,
   wait for the "your data is ready" mail, download the zip.
2. In MetalMark Money, Accounts → the card you use at Amazon → Export CSV.
3. `cd backend && PYTHONPATH=. uv run python ../scripts/amazon_match.py "<Order History.csv>" <card.csv>`

The match rate on real data is the one number this spike could not produce. It decides
how much tolerance (gift-card partial payments, promotional credits, split tender) the
real matcher needs.

## Known gaps the real feature must handle

- **Split tender.** When a gift-card balance pays part of an order, the card is charged
  less than `Total Owed`. The export's `Payment Instrument Type` says "Gift Certificate/Card
  and Visa"; the Transactions page (source C) shows the real card amount.
- **Refunds.** In a separate file in the export (`Retail.OrderHistory.Returns` /
  refund details). Match them the same way, as money in.
- **Subscribe & Save, Whole Foods, Fresh, Kindle, Prime Video.** Different files or not
  in the export at all. Out of scope for the first version.
- **Several Amazon accounts in one household.** Store the source account's label with
  each order.

## Sketch of the real feature (for the ADR)

- Tables `external_orders` (household, source `amazon`, order id, order date, source
  account label) and `external_order_items` (order, title, asin, quantity, amount,
  suggested category), and `transaction_order_links` (transaction ↔ order, kind
  shipment/order, matched_by `auto|user`). Items are **annotations**: they never change a
  transaction's amount, and a category suggested from items is written at `auto`
  provenance, so a user or rule category always wins (ADR-0007/0049).
- A transaction's detail sheet shows the items; a one-tap "split by items" turns the
  suggestion into a split (ADR-0031 already has splits).
- Item categories come from the local categorizer first. A model is optional and would
  see only item titles.
- The agent API pseudonymizes item titles (ADR-0048): a title is free text.
