# MetalMark v0.9 beta — the plan to "everything on the list, done"

> Written before implementation. Every load-bearing choice below gets an ADR in the PR that lands it
> (`docs/adr/README.md`: new decisions land with the change they justify). This file is the working
> plan; the ADRs are the record.

## Where we are

M1a (the manual ledger) and M2's SimpleFIN vertical are done and green: ledger, FX, rules, transfers,
CSV import, sync engine, job queue, worker, admin panel, reports (net worth, cash-flow bars, spending
donut). Working tree clean at `7e52c2e`, CI green on all four jobs.

What is *not* built, in the order this plan addresses it:

| # | Item | State today |
|---|---|---|
| 4 | OFX/QFX import, auto-split rules | OFX is a comment (`api/imports.py:29`); auto-split deferred at `models/ledger.py:400` |
| 1 | Investments (M1b) | Nothing. No `securities`/`holdings`/`prices`/`investment_transactions`. Three accepted ADRs specify it |
| 3 | Cash-flow Sankey | Nothing. Reports has the data layer it needs |
| 2 | Portable export/import + encrypted backups | Nothing. `pg_dump` appears only in prose |
| — | Admin panel discoverability | **Shipped but the user cannot find it.** A defect in what we just built |
| — | Reports time filters, review deck, account marks, pretty dates, desktop layout, desktop notifications, app icon | Nothing; layout is mobile-first with no desktop section in DESIGN.md |

## Decisions taken

### A. Reconciliation with investments — a new term (ADR-worthy, refines ADR-0017)

The invariant every report is tested against is `ΔNW = net cash flow + currency revaluation`
(`services/reports.py:4-9`). Investments break it, and not by a rounding error: buying stock moves value
from cash to securities *inside* net worth with no cash flow, and a market move changes net worth with
neither cash flow nor any FX rate behind it.

We will extend the identity to

```
ΔNW = net cash flow + currency revaluation + market appreciation
```

where `market appreciation` is computed as the residual (the same honest-decomposition trick ADR-0017
already uses for revaluation), **and** investment `buy`/`sell` are excluded from cash flow exactly as
transfers are (ADR-0008) — they are a movement between asset classes, not income or expense. Only
`dividend` and `interest` count as income. This is the single most important correctness decision in the
investments workstream; without it the reports quietly stop reconciling and the failure looks like a
rounding bug for weeks.

### B. The export format (ADR-worthy)

We will add a **versioned JSON export** — `{"format": "metalmark.export", "version": 1, …}` — as the
canonical, lossless, re-importable document, plus a **CSV export of transactions** beside it for other
tools. Not a zip: two endpoints, each independently useful and testable.

- **Money is a string** (`"1234.5600"`), never a JSON number. JSON numbers are IEEE doubles, and
  ADR-0005 is not negotiable — an export that round-trips through a float corrupts the ledger.
- **Credentials never leave.** `account_connections.access_url_encrypted` is a live bank bearer token;
  it is excluded from the format by construction and a test asserts the ciphertext never appears in any
  export byte. A connection exports as metadata only (`provider`, `org_name`, interval, enabled).
- **On import a credential-less connection is `auth_error`**, with `last_error` saying the credential was
  deliberately not exported. That is honest, reuses the existing enum, and the existing UI affordance
  ("Reconnect") is already exactly the right next action. No new status value.
- **UUIDs are remapped**; entities carry stable natural keys (account `external_key`, tag/category name,
  security symbol, `(account, external_id)` for transactions) and import is idempotent against them.
- **Referenced FX rates are included**, and `base_amount` is recomputed on import — so a converted
  number reproduces exactly rather than depending on whatever rates the target instance happens to hold.
- **Topological insert order** (owners → categories/groups → tags → accounts → securities → holdings →
  transactions → splits → tags → transfer links → rules), because the FKs are real.
- Excluded entirely: users, sessions, membership, sync jobs/runs/events (operational, not household data).

### C. Backups are not exports (ADR-worthy)

They are different guarantees and we will ship both, because neither substitutes for the other:

- **Backup** = disaster recovery of the whole instance. `scripts/backup.py` (chunked AES-GCM via the
  `cryptography` dependency the app already has — no new binary in the image), keyed by
  `METALMARK_BACKUP_KEY`, refusing to run without it. `scripts/restore.py` to restore.
- **The drill is the deliverable.** A `restore-drill` gate in `scripts/verify.sh` and CI: dump the dev
  database, restore into a scratch database, assert row counts match, drop the scratch. A backup script
  nobody has restored from is a belief, not a capability.
- The export is **not** a backup: it omits users and sessions, and cannot rebuild an instance.

### D. Report ranges and granularity (ADR-worthy)

- **Presets resolve client-side** to an explicit `start`/`end` (`last week|month|quarter|YTD|all|custom`).
  The API stays a pure function of the range it is given.
- **`start` becomes optional** on the three report endpoints; omitted means "from the earliest
  transaction". That is what "all time" needs and it belongs in one place, not three.
- **Granularity becomes a server concern** (`auto|day|week|month|quarter`), returned on the response so
  the chart can label itself. `auto` derives from the span: day ≤ ~92d, week ≤ ~2y, month beyond.
- **This fixes a live bug**: `_month_ends` (`reports.py:47`) emits month *ends* inside the range, so
  `last week` yields a single point dated *after* the range — a one-point chart labelled with the wrong
  month. Every short range is affected today.
- **And an N+1**: `net_worth_at` runs one query per account per point (`reports.py:138-155`), so "all
  time" is ~60 months × N accounts round trips. The series needs one pass.

### E. Desktop notifications: in-app, not Web Push (ADR-worthy)

Web Push requires a browser push service, which means internet egress and handing account activity
metadata to a third party — that contradicts ADR-0002 (LAN/VPN only, poll-based, no public ingress).

We will use the **Notification API + the existing service worker**, driven by polling an authenticated
endpoint, and be honest about the scope: notifications arrive **while the app is open in a tab**. Events
are transition-only, reusing the rule `services/notifications.py::should_notify` already encodes (a
revoked credential must not alarm every cron). The notification icon is the app icon — which today does
not exist.

### F. App icon and the butterfly

`vite.config.ts:18` ships the PWA manifest with `icons: []` and `index.html` has no favicon link, so the
app has no icon anywhere. We will add a **metalmark butterfly** mark (Riodinidae — the family the app is
named for; the metallic sheen on the wings is the design hook) as SVG, and generate the favicon + PWA
icon set from it. Same asset serves as the desktop-notification icon and the install prompt icon.

### G. Account identity without phoning anyone

The request was favicons per account. Fetching real favicons means either a third-party favicon service
or a request per institution, both of which leak which institutions this household banks with — the same
ADR-0002 problem as Web Push, and a trademark question besides.

We will render a **deterministic monogram**: initials from the institution name over a colour derived
from hashing that name, with a small curated map giving a handful of well-known institutions their
recognisable hue. No network, no third party, stable forever, and it reads at a glance in a list — which
is the actual requirement ("show which account a credit or debit is from"). Transactions gain the
account's mark and name in the list and the detail sheet.

### H. Dates (DESIGN.md, not an ADR)

`lib/dates.ts` has only ISO helpers today. We add a formatting layer with a fixed vocabulary —
`long` ("Jan 02 2026"), `medium` ("Jan 02"), `weekday` ("Mon"), `compact` ("Today"/"Yesterday"/"Jan 02"),
and `relative` ("2h ago") where recency is the point. **The ISO form is never the visible text**: it
appears as the `title`/accessible value on hover and focus, which is where a precise timestamp belongs.
One helper, one test file, and a DESIGN.md rule so it does not drift back.

### I. Desktop layout (DESIGN.md §9, new)

**Diagnosed, and it is one line.** `AppShell.tsx:93`:

```tsx
<main className="mx-auto w-full max-w-4xl flex-1 p-4">{children}</main>
```

`max-w-4xl` is 896px, constant above that width — at 1440px the app paints **864px of content and 272px
of dead margin each side (60%)**. There is no responsive tier above 640px: `sm:` has 33 uses, `lg:` has
**three** in the whole codebase and exactly **one** in app code (`Transactions.tsx:323`, inside a filter
bar), and `md:`/`xl:`/`2xl:` have none. The design system half-anticipated this and then never collected:
`DESIGN.md:278` documents "Page gutter | 16 px (`p-4`); **24 px at `lg:`**" and the shell has no `lg:p-6`.

Worst offenders, in order: **Reports** (three stacked full-width cards, each chart fixed 280px tall
stretched across 864px, zero breakpoints in the file), **Review** (a swipe deck 864×256), **Accounts**
(rows with ~800px between description and amount), **Admin**. Settings is the most responsive page and
still stretches its form fields to 864px.

Constraints any change must respect, all pre-existing: §4.13's **five-item nav cap** and its "desktop nav
stays `hidden sm:flex`; **do not show both**" (`DESIGN.md:836,857`) — so a sidebar *replaces* a nav, it
does not add a third; `scroll-padding` parity in `index.css:89-90` if header or tab-bar height moves;
§2.5's 4/8/12/16/24/32/48 spacing scale (lint rule 3); no truncation on any money column (lint rule 2);
no hardcoded hex in `Reports.tsx`/`Chart.tsx` (lint rule 5). The only sanctioned wide-screen pattern is
`Dialog.tsx`'s right slide-over (`DESIGN.md:734`), already used by `TxnDetailSheet`.

We will add **DESIGN.md §9 Desktop rules** — breakpoints, maximum content widths, and the multi-column
patterns — then apply it: a wider content container with the `lg:` gutter the spec already promised,
**Transactions** as list + detail (promoting the existing slide-over from overlay to pane), **Reports**
as a two-up grid, **Settings** as rail + content, **Accounts/Admin** as card grids. That last item is a
genuine navigation-IA change (sidebar for sidebar, not sidebar-plus-nav), lands on its own commit, and
is the one place where DESIGN.md §4.13 has to be re-read as a constraint rather than a description.

### J. OFX/QFX scope

- **OFX 2.x / QFX only** (well-formed XML, parsed through `defusedxml` — no entity expansion, no
  billion-laughs, no external fetch). **OFX 1.x is SGML, not XML**, and is *detected and refused with a
  precise message naming the version found* rather than half-parsed. A silent misparse of a bank file is
  worse than a refusal; if the real banks here produce 1.x, it becomes its own ticket with real samples.
- Dedupe reuses `import_hash`, the same key the CSV importer uses, so the same transaction arriving by
  CSV and then by OFX does not double.
- **Banking transactions only in v1.** OFX investment types (`BUYMF`, `SELLMF`, `INCOME`, …) are counted
  and reported as skipped, because this lands *before* investments exist. Wiring them into
  `investment_transactions` is a follow-up once item 1 is in.

### K. Auto-split rules

- A new rule action (`split`) whose payload is an ordered list of `{match, category_id, owner_id?}` plus
  a remainder leg, so the splits are a template and the parent's amount stays the source of truth.
- **The balance invariant is the whole risk**: splits must sum to the parent (`ec45329` landed exactly
  that rule for manual splits) and "apply to existing" must be **idempotent** — re-running a rule must
  not re-split an already-split transaction or drop a human's manual split.
- Provenance applies as everywhere else: a rule may not overwrite a `user`-owned field, and a
  transaction a human has split is never re-split by a rule.

### L. Admin panel discoverability

**Diagnosed: not a gating bug. A pure findability defect, and a bad one.** Verified against the live
database and the running API — `owner@example.com` really is `is_admin = t` and `role = owner`,
`is_admin` really is on `UserResponse` (`schemas/auth.py:25`) and really is populated on `/auth/me`
(`api/auth.py:39`), the route gate really passes (`App.tsx:52`), and the link really renders. Nothing is
broken. It is simply invisible:

- The **only** link to the panel in the entire app is `Settings.tsx:222`, and Settings opens on
  **Categories** — it is in Connections, the **5th of 8** tabs.
- It is styled as prose, not an action: a `text-xs text-fg-muted` aside (`Settings.tsx:220`) whose text
  reads "sync activity panel".
- **The word "Admin" appears nowhere in the UI** — not the link, not the page `<h1>` ("Sync activity",
  `Admin.tsx:123`), not the document title (`AppShell.tsx:31`). Someone scanning for an admin view has
  no string to match.
- A non-owner sees an owner-only card instead and no hint the panel exists.
- The server gates on `require_owner` (`deps.py:85-88`); `is_admin` is **never consulted server-side** —
  it is a client-side hint only, used in exactly two places (`App.tsx:52`, `types.ts:8`).

The fix is therefore: give it an obvious entry point that says **"Admin"**, and align the two halves.
There is a latent inconsistency worth closing at the same time — the route gates on `user.is_admin`
while the link gates on `household.role === "owner"`. They agree for every user the current code can
produce (`services/auth.py:88,232` set both together) so it is not a live bug, but it becomes one the
moment anyone creates an `is_admin` member or an owner without `is_admin`. Both will read one field.

## Order of work

Admin defect first (it is broken shipped behaviour and it is small), then the four requested items in the
requested order, with the cross-cutting presentation foundations landing before the features that consume
them so nothing is built twice:

1. **Admin discoverability** — diagnose, fix, make obvious.
2. **Presentation foundations** — `lib/dates.ts` formatting layer, account monogram component, app icon
   and favicon (all three are consumed by every later item).
3. **Item 4** — OFX import + auto-split rules.
4. **Item 1** — investments: models + migration, services, API, allocation view, dividends in income, the
   reconciliation term from decision A.
5. **Item 3** — Sankey, plus the report ranges/granularity from decision D.
6. **Item 2** — export/import, then encrypted backups and the restore-drill gate.
7. **Review deck**, desktop layout, desktop notifications.

Each step: ADR + DESIGN.md updates in the same commit, tests written with the code, `./scripts/verify.sh`
green, CI green, pushed. `reset` between mutating gates.

## Definition of done (v0.9 beta)

- Every item above built, tested and documented.
- `./scripts/verify.sh` green end to end, including the new `restore-drill` gate, and CI green on `main`.
- The export round-trips: export a seeded household, import into an empty one, and the two reconcile to
  the same net worth, the same transaction count, and the same per-category totals.
- No credential, token or raw payload anywhere in a log, an export, a `sync_run_events` row or `last_error`.
- A test proves the reconciliation identity holds **with investments in the ledger**, including a market
  move — the failure mode decision A exists to prevent.
- Ready for the user's end-to-end run against real credentials, reported as counts and statuses only.

## What this plan deliberately does not do

- **Web Push / notifications when the app is closed** — contradicts ADR-0002; see decision E.
- **Fetched institution logos** — see decision G.
- **OFX 1.x (SGML)** — see decision J.
- **Investment lots / FIFO / specific-lot** — ADR-0020 defers this out of v1.
- **Automatic security-price fetch** — prices are manual in v1 (README's deferrable list); the staleness
  display is what makes that honest.
