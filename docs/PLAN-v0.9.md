# MetalMark v0.9 beta — the plan to "everything on the list, done"

> Written before implementation. Every load-bearing choice below gets an ADR in the PR that lands it
> (`docs/adr/README.md`: new decisions land with the change they justify). This file is the working
> plan; the ADRs are the record.

## Where we are

M1a (the manual ledger) and M2's SimpleFIN vertical are done and green: ledger, FX, rules, transfers,
CSV import, sync engine, job queue, worker, admin panel, reports (net worth, cash-flow bars, spending
donut).

What is *not* built, in the order this plan addresses it. Status as of `e2578c9`:

| # | Item | State today |
|---|---|---|
| 4 | OFX/QFX import, auto-split rules | **Done** (`ea80d13`) |
| 1 | Investments (M1b) | **Models, services, API and the write path done** (`391eaf0`, `af92b83`, `cafc01e`); the allocation/portfolio read UI is written and in review |
| 3 | Cash-flow Sankey | Nothing. Reports has the data layer it needs |
| 2 | Portable export/import + encrypted backups | Nothing. `pg_dump` appears only in prose |
| — | Admin panel discoverability | **Done** — it has a nav item, a Settings tab and a document title, all of which say "Admin" |
| — | Reports time filters, review deck, account marks, pretty dates, desktop layout, desktop notifications, app icon | Foundation laid (`cafc01e`: date vocabulary, account monogram). The range control, review deck, desktop layout and notifications remain |
| — | **Bulma as the design system** (appended mid-plan, 2026-09-20) | Nothing. The app is hand-rolled Tailwind on a CSS-variable token layer. See decision M |

**Two notes on the order, both raised rather than taken unilaterally.**

- Decision M argues the Bulma shell swap should land *before* the unbuilt UI (the Sankey, the review deck,
  the export screens) so none of it is written twice. The stated order puts Bulma last, and the stated
  order stands — but the investments read UI was the first casualty and has already been written in
  Tailwind. The question is worth answering again before the review deck, which is the next unbuilt
  surface of any size.
- The identity in decision A is **superseded in one respect by ADR-0032**: `market appreciation` is now
  computed from positions and prices rather than taken as the residual, and the residual became a fourth
  term, `unexplained`, which is *reported* rather than absorbed. Decision A's reasoning about why
  investments break the two-term identity still holds; only "computed as the residual" is stale.

## Appended after this plan was written

**Bulma.** Requested while item 4's ADRs were being written: *"can we change our design system to use
https://bulma.io/? this should be appended to our todo"*. It is not part of the 4/1/3/2 order — it is a
seventh workstream appended at the end, and decision M below is where it is scoped. Item 4 is
**not blocked** by it: OFX import and auto-split rules are backend + a small surface, so they proceed.

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
already uses for revaluation — **superseded: ADR-0032 made it a computed term and added `unexplained` as
the reported residual, because a term defined as the remainder can never disagree with the others**),
**and** investment `buy`/`sell` are excluded from cash flow exactly as
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

### M. Bulma as the design system (appended; ADR-worthy, and it supersedes DESIGN.md §2's *wiring*, not its values)

Bulma 1.0.4 is **CSS-only** — no JavaScript components — and its theming runs on **CSS custom properties**.
Both facts matter here, and both make this a smaller change than the word "design system" suggests.

**What we actually have today** (measured, not assumed): no component library at all. `package.json` has
React, React Router, TanStack Query, ECharts, framer-motion and `@use-gesture` — there is **no Radix, no
shadcn, no CVA, no `clsx`**. Every control is hand-built. 21 of the 32 `.tsx` files carry `className=` with
Tailwind utilities, and the palette underneath is already a `:root`/`.dark` CSS-variable block wired into
Tailwind via `rgb(var(--x) / <alpha-value>)` (`tailwind.config.js`).

So Bulma would be **adding** a component library, not swapping one — and it lands on a token layer that is
already CSS variables, which is the same mechanism Bulma uses. The parts of DESIGN.md that carry real
value (the measured contrast ratios in §2.1, the money rules in §6, the accessibility floor in §7) are
*values and rules*, and they survive a styling-layer swap intact. What does not survive is §2.5–2.10's
Tailwind wiring, and the design lint, which is written against Tailwind utility classes.

**What Bulma buys, concretely.** `navbar`, `menu`, `panel`, `card`, `box`, `table`, `tabs`, `tag`,
`notification`, `pagination`, `breadcrumb`, `columns` — the exact components this app is missing, and the
exact reason decision I exists: the desktop view reads as a stretched phone partly because there is no
navbar, no table and no panel to reach for, so pages lay out as stacked cards at every width.

**Cost, honestly.** A rewrite of the presentation layer across ~21 files, a design-lint rewrite (rules 1–6
and 8 all name Tailwind classes), a DESIGN.md §2 remap of role tokens (`surface-raised`, `fg-muted`) onto
Bulma's variable names (`--bulma-...`) or a bridge that keeps our roles as the source of truth, and the
loss of Tailwind's utility workflow for one-off spacing. Two class systems must never coexist — a file
using both is the failure mode that makes this kind of migration permanent.

**Decision: adopt Bulma, staged, and *before* the unbuilt UI rather than after.** The review card stack,
the Sankey, the investments screens and the export UI do not exist yet; building them in Tailwind first
and restyling them later means writing each one twice. The sequence is therefore:

1. **Bridge, not replace, the tokens.** Keep DESIGN.md's role vocabulary as the source of truth: map
   `--surface`/`--fg`/`--accent`/… onto Bulma's variables in one `theme.scss` so light/dark, the contrast
   table and the pre-paint theme script in `index.html` keep working unchanged. Bulma's automatic dark mode
   is driven by the same `.dark`-style hook we already set, or it is not used at all.
2. **Shell first** — `AppShell`, the nav, and the page gutter — because that is decision I's territory and
   the highest-value swap. Ship it, look at it, and stop if it is worse.
3. **Component-by-component, one commit each**, starting with the list/table surfaces. Every step keeps the
   app working, so the change is abandonable at any point.
4. **Delete Tailwind last**, in one commit, only once no file imports it.

**Checkpoint required.** Per the user's own rule (*"checkpoint / commit before making larger changes"*),
step 2 lands as its own commit with a clean tree on both sides of it, so the whole migration can be reverted
in one `git revert` without touching anything else.

**Not decided here:** whether to drop Tailwind entirely or keep it alongside Bulma for layout utilities.
Recommendation is to drop it — two class systems is the cost this whole section exists to avoid — but that
is a call for after step 2, when there is something real to look at.

#### M.1 — the staging above does not work, and this is the measurement

Step 1 was attempted. It is **not** achievable as written, and the reason invalidates the sequence rather
than the goal. The assumption it rests on — that the precompiled `bulma.css` can sit alongside Tailwind
while components move over one at a time — is false, because the two frameworks share class names and
Bulma's copies are `!important`.

Measured on the real stack with `bulma-no-dark-mode.css` loaded and a `theme.scss` bridging every token:

| Probe | Expected | Actual |
|---|---|---|
| `p-4 lg:p-6`, at 1440 px | `24px` | **`16px`** |
| `grid gap-3` | `display: grid`, auto columns | **`grid-template-columns: 0px ×9`** |
| `block` (not last child) | no margin | **`margin-bottom: 1.5rem`** |

Three separate failures with one cause:

1. **Bulma ships 39 of this app's class names as its own spacing utilities, with `!important`** —
   `p-4 { padding: 1rem !important }`, `mt-2 { margin-top: 0.5rem !important }`, and so on. The *values*
   happen to agree with Tailwind's scale, so today they are inert. They are not inert for long: `!important`
   beats any non-important declaration regardless of media query, so **every responsive override in the app
   silently stops working**. That is precisely what decision I is made of — `lg:p-6`, `lg:grid-cols-3`,
   the sidebar breakpoint — so the staged migration would land exactly on top of the desktop-layout work
   and quietly disable it.
2. **`grid` and `block` are Bulma components.** `.grid` sets `grid-template-columns`; `.block:not(:last-child)`
   sets `margin-bottom`. Both are Tailwind display utilities here, used in 12 and 3 files respectively, and
   Bulma's selectors are more specific so source order cannot save them.
3. **The truly neutral state is reachable** — the 16 bare-element rules are enumerable and every one can be
   answered by the bridge, which is what `theme.scss` did — but neutrality reached by luck is not a
   foundation. The 39 `!important` collisions are not fixable by bridging; they need Bulma's utility layer
   to not ship.

**What this changes.** Not the goal — the finding is about the *route*. Two routes are left:

- **Atomic.** Bridge + every component + Tailwind removed in **one** commit, so no mixed state ever exists
  in a running build. Highest risk, but correct, and it is the only route that keeps `!important` out of the
  app's way while the work is in progress.
- **Bulma from Sass**, pulling only the component modules actually used (`@use "bulma/sass/components/card"`)
  instead of the precompiled bundle. This drops the utility layer entirely, which removes all 39 collisions
  *and* most of the 630 KiB — and it is the path Bulma itself documents for configuration. It costs a `sass`
  dependency and a Vite-side compile.

Either way, **decision I must not be built while both class systems are loaded**, and the reverse of §M's
original ordering is now correct: the shell should be *the first thing written in the new substrate*, not
the thing it is migrated under. The original argument for Bulma-first — do not build the unbuilt UI twice —
still holds; what has changed is that "Bulma-first" now means resolving M.1 first, not loading a stylesheet.

#### M.2 — tabled by the user, 2026-09-20

Put to the user with the measurement above, and the answer was to **table Bulma**, because the priority is
getting the app's *functionality and UX* right and the substrate is not what is missing. So §M's steps 1–4
and both routes in M.1 are deferred, not rejected: nothing below depends on them, and a later migration
starts from M.1's measurement rather than from scratch.

What this unblocks is item 7 in full — the review deck, the desktop layout and desktop notifications — which
is now written in Tailwind and may be written once. **If Bulma is ever revisited, the desktop layout is the
one piece likely to be rewritten**, and that is the cost this decision accepts knowingly. It is a smaller
cost than §M assumed: decision I's substance is `AppShell` plus the page containers on four screens, not
every component that happens to use a spacing class.

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
8. **Bulma** — decision M, appended at the user's request after this plan was written.

**Step 8 has an ordering recommendation attached, and it is the user's call, not mine.** Decision M argues
for doing the shell swap (its steps 1–2) *before* item 1, because the investments screens, the Sankey and the
export UI are all unbuilt and would otherwise be written in Tailwind and then restyled. That is a reordering
of the sequence the user set (4/1/3/2), so it is **not taken here** — the default is to follow the stated
order and raise it when item 4 lands. Nothing in items 4/1/3/2 is blocked either way.

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
- **Quantity, cost basis and gain/loss on the investments view** — found while building the read UI, and
  worth its own decision rather than a quiet join. ARCHITECTURE.md §4 names the summed row as "(quantity,
  total market value, cost basis, gain/loss)"; market value, % allocation and the four group-by toggles
  shipped, and these three did not, for three separate reasons — the third is the one that would be wrong to
  guess at:
  1. `/investments/allocation` (the endpoint behind the view's groupable rows) returns value, share and a
     position *count* only — there is no summed-quantity, cost-basis or gain field to render, at any
     grouping.
  2. `cost_basis` does exist on `Holding` (`manual_cost_basis` behind it), so a gain/loss column could be
     joined onto the holdings list — but only for `group_by=security`. A *group* row ("ETF", "Type:
     mutual_fund") has no cost basis to subtract, and a quantity summed across *accounts* mixes different
     securities and means nothing. Summing is a decision about which positions count, and the allocation
     endpoint is the right place to make it, not the client.
  3. **`cost_basis` is recorded in the security's own quote currency, and turning it into a base-currency
     gain needs an FX rate at each acquisition date — a per-lot rate this schema does not store and
     ADR-0032 does not define an accounting policy for.** Converting at today's rate instead silently
     books a currency move as investment performance, which is precisely the confusion ADR-0032's four-term
     identity exists to prevent (the same money would land in `market_appreciation` and in
     `currency_revaluation`, counted twice and explained by neither).
  Until there is an ADR for (3), the view shows position value, share and price age and says nothing about
  gain — a "gain/loss" whose currency policy nobody wrote down is worse than its absence.
  `InvestmentsView.tsx`'s header records the same points where the gap was hit.
