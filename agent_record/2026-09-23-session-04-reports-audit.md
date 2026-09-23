# Session 04 — Reports correctness & understandability audit

- **Date:** 2026-09-23
- **Agent:** Claude Code (Opus 5.5), with an advisor review before and after the audit.
- **Asked:** "take a look at this codebase, understand its purpose etc. and do an audit on correctness /
  intuitive understandability of the reports. one pain point rn is overall networth change graphing - can
  show _wild swings_. review overall architecture / implementation correctness, overall flow etc."
- **Method:** read the reports service, the writers that feed it (sync, manual ledger, OFX, investments,
  FX), and the Reports/Accounts UI; turned each hypothesis into a throwaway integration test against a
  real Postgres (7/7 reproduced — each asserts the *current* behaviour, so a pass confirmed the defect).
  The repro tests were not committed; their scenarios become the regression tests of the fix branch.
- **Outcome:** the audit below, verbatim. Follow-up: user asked for a fix plan + implementation
  (branch `fix/net-worth-balance-model`).

---

## TL;DR — why the net-worth line swings

The chart is honest about its inputs; the inputs are wrong in a few systematic ways. In order of likely
impact on a real household:

| # | Cause | Shape of the swing | Status |
|---|---|---|---|
| 1 | Liability sign convention is undefined; synced cards/loans count **for** you | Sawtooth of ±2× card balance every billing cycle; a one-off 2× flip where one account's history mixes conventions | code conflict **confirmed**; that real bridges send negative card balances is **inferred** from the spec's txn-sign rule |
| 2 | Synced accounts with holdings (and manual investment accounts) become `derived` with no holdings → **0 in the chart**, full value in the headline | Chart sits far below the Accounts number; jumps when anyone adds a holding | **confirmed** |
| 3 | No balance history before the first snapshot; sync pulls 44 days of txns but 1 balance | Cliff from ~0 to full balance at connection date; huge "unexplained" | **confirmed** |
| 4 | Balance edit dialog pre-fills the *old* date → overwrites a past point | History silently rewritten; windows change retroactively | **confirmed** |
| 5 | Missing price / missing FX rate → account silently counted as 0 | Dip-then-jump; phantom "market appreciation" | **confirmed** (price) / inferred (FX) |
| 6 | Account identity: vanished accounts carry forward forever; same-named accounts at one bank collapse into one row | Permanent step; or a balance that flip-flops between two cards | inferred |

Demo-data repro of #1 + #2 together: after syncing the committed capture plus one card at −$1,000,
**Accounts headline = $140,721.02, chart endpoint = $26,035.51** (same day). Both are wrong: the
right answer is $138,721.02.

---

## 1. Liability sign convention (critical) — confirmed

The codebase holds two incompatible conventions at once:

- **"Positive = amount owed"**: `schemas/ledger.py:25` (manual create comment), `reports._net_worth_parts`
  (`backend/app/services/reports.py:344`, `conv if a.is_asset else -conv`) and `ledger.net_worth`
  (`services/ledger.py:173-176`, `assets - liabilities`).
- **"Signed ledger balance" (debt < 0, balance moves with txn sign)**: SimpleFIN. Its spec leaves balance
  sign undefined but defines txn amounts as "positive = deposited", so a balance consistent with its own
  transactions must be negative for debt (inferred — not observed on a live card here). `_apply_balance`
  (`services/sync.py:556`) and OFX `_statement_balance` (`services/imports.py:731`) store the raw number.
  `_revaluation` (`reports.py:947`) also *assumes* this convention (`B_end = B_start + Σamounts`) and then
  multiplies by `sign` anyway.

Consequences (repro'd):
- Synced card at −$1,000 contributes **+$1,000** to net worth. `ledger.net_worth` reports
  `liabilities: -1000` and adds it back.
- Paying the card off (checking 5000→4000, card −1000→0): chart drops **−$2,000**, true change $0;
  reconciliation reports `unexplained −2000`. Every statement cycle does this → the "wild swings".
- Manual positive-owed convention with a **foreign** card: flat FX rate, €50 purchase →
  `currency_revaluation +110`, `unexplained −110` (should be 0/0). The formula is wrong for the one
  convention the manual form uses.
- Aggravators: `infer_account_type` (`services/aggregator.py:300`) types anything whose name lacks
  "credit"/"card"/"loan" as `other` = **asset**, which happens to be correct for a signed balance — so the
  same bank card is right or wrong depending on its name. `AccountUpdate` (`schemas/ledger.py:32`) has
  **no `type` field**, so the user can't fix a mistyped account.
- No report, reconciliation or sync test includes a liability. Credit accounts appear in tests only for
  owner attribution and one headline assertion (`tests/integration/test_ledger.py:56-61`, positive-owed).
- The `_TYPE_HINTS` comment (`aggregator.py:~280`) says a wrong type is "always correctable by hand through
  `PATCH /accounts/{id}`" — it isn't; that endpoint cannot change `type`.

**Fix:** pick one storage convention — recommend **signed ledger balance everywhere** (debt negative;
matches SimpleFIN, OFX, and the txn sign, and makes `B_end = B_start + Σtxns` true for every account).
Then net worth = Σ converted balances (no `is_asset` flip); `is_asset` becomes presentation only
(Assets/Liabilities grouping); `_revaluation` drops the `sign`; the manual form accepts "amount owed" and
stores its negation.

**This is not a small migration.** `balance_snapshots` has no source column, and one account can already
hold *both* conventions (a synced card whose balance was then edited in the dialog; OFX imported into a
manual liability). There is no clean selector for "rows to negate". A practical path: add
`balance_snapshots.source` going forward; for existing liability rows, infer per-row sign from
continuity with the account's transactions (or from which writer's date pattern it matches) and have the
user confirm per account in a one-time review. Mixed-sign history within one account is itself a swing
source (a 2× jump at the point the writer changed) — triage query A below finds it.
Add `type` to `AccountUpdate`. Add a property test: for every account, `Δbalance == Σtxn amounts` between
snapshots when no txns are missing, and a reconciliation test with a card and a loan.

## 2. `derived` investment accounts with no holdings read as 0 (critical) — confirmed

- Sync marks any account whose payload has holdings as `investment` + `balance_source="derived"`
  (`sync.py:450-457`) and then **skips the snapshot** (`sync.py:562-569`) — but **sync never writes
  holdings** (no `Holding` writes in `sync.py`). The series values derived accounts from holdings only
  (`reports.py:346-365`), so the account is **0 at every point**. In the demo capture, "SimpleFIN Savings"
  ($114,685.51 of AAPL) is absent from the chart and present in the Accounts headline.
- Same for a **manual** investment account created with a typed balance (`ledger.create_account` sets
  `derived`, `ledger.py:103`): repro shows headline $80,000 / series $0. The snapshot it writes is ignored.
- Then the first time someone adds a holding, the whole account appears at once — and `quantities_at`
  (`investments.py:748`) back-projects today's quantity to all past dates, ignoring `holdings.as_of`, so a
  position entered today appears to have been held for as long as price history exists.

**Fix:** a derived account with no positions should fall back to its stated snapshots (ADR-0021 already
has the "unaccounted cash plug" concept — apply it here), or sync should default synced investment
accounts to `stated` until holdings exist. Add the invariant test the whole class fails:
**series(last point = today) == `/net-worth` headline** for the same scope.

## 3. No history before the first snapshot (high) — confirmed

`_net_worth_parts` counts an account as 0 before its first snapshot (`reports.py:335-337`). First sync
pulls 44 days of transactions (`sync.py:108`) but writes one snapshot (today). `earliest_activity`
(`reports.py:180`) opens "All time" at the *earliest transaction*, i.e. 44 days of ~$0 followed by a
cliff. Repro: txns in Feb, first snapshot Mar 15 → points `[0, 0, 25000]`, `unexplained 22,200`
attributed to "Checking". (The `$38,850.60` in the `_unexplained_by_account` docstring is this.)
OFX imports have the same shape (history of txns, one LEDGERBAL).

**Fix:** derive backwards: `balance(d) = first_snapshot − Σ txns in (d, first_snapshot]` for dates before
an account's first snapshot (and optionally between sparse snapshots). Requires #1 first (signed
balances). Caveats: include hidden and pending rows when unwinding (they moved the real balance), and
stop at the earliest transaction — before that, the account should be **absent** (not 0) and the chart
should say so. Until then, at least clip "All time" to the first snapshot rather than the first txn.

## 4. Balance edit rewrites history (high) — confirmed

`EditAccountDialog` initialises `balanceDate` to `account.balance_date` (`frontend/src/pages/Accounts.tsx:301`)
and always sends it (`:324-325`); `update_account` then upserts the snapshot **at that old date**
(`services/ledger.py:144-155`). Updating "what the balance is now" overwrites the old point — repro:
Jan 1 snapshot 1000 → edit to 3000 → only snapshot is Jan 1 = 3000. Every window starting after Jan 1
changes retroactively. Also, picking an *earlier* date moves `account.balance_date` backwards, so the
headline shows an old balance as current.

Note: `upsert_balance_snapshot` also runs on every save because the dialog always sends
`current_balance`, even for a rename.

**Fix:** default the date to *today* when the balance field changes; only send balance fields if
changed; server-side, never move `account.current_balance/balance_date` backwards (snapshot the past
date, keep the latest as current). Make "correct a past balance" an explicit action in a balance-history
view.

## 5. Silent zeros: missing price, missing FX rate (medium) — price confirmed, FX inferred

- No price on/before a date → `value_base=None` → dropped with **no warning** in the series
  (`investments.py:207-210`, `securities_value_by_account` sums only non-None). Repro: 100 VTI, first
  price Mar 1 → `[0, 0, 0, 25000]`, **`market_appreciation +25,000`, `unexplained 0`, `warnings []`**.
  The reconciliation confidently tells the user the market gave them $25k. ADR-0032 §4 says stale/missing
  prices must be reported; that is only true for the headline valuation, not the series.
- No FX rate before the first stored rate → account dropped from the points (`reports.py:342-343`).
  The only signal is indirect: `_revaluation` warns "currency move is not attributable" when a window
  endpoint has no rate, which doesn't tell the reader the *line itself* is missing an account. **The daily FX fetch in ARCHITECTURE §2 / PLAN "FX" row is not implemented** (no Frankfurter/ECB
  code anywhere), so rates exist only when typed in. Any foreign account's history before the first manual
  rate is 0, and between manual rates the value is flat at a stale rate.

**Fix:** return per-point coverage from `_net_worth_parts` (which accounts were counted, which were
excluded and why) and surface it (see §8). Treat "no price" / "no rate" as *unknown*, not 0 — e.g. carry
the position at the earliest known price with a flag, or exclude the account from *both* endpoints of the
reconciliation and name it. Implement the FX fetch with a historical backfill.

## 6. Account identity — carried-forward ghosts and collapsed twins (medium) — inferred

- **Accounts that disappear from the payload** (closed, or the bridge drops them) get no handling in sync
  that I could find; `_net_worth_parts` carries their last snapshot forward indefinitely. A closed card's
  last non-zero balance, or a closed savings account, stays in net worth forever → a permanent step.
  Same outcome if a reconnect misses the `external_key` remap and the old account (now `is_manual`)
  keeps its last balance alongside the new one → the balance is double-counted from that day.
- **Two accounts at one institution with the same name** (`external_key_for`, `aggregator.py:258` =
  org + normalised name) map to **one** ledger row: `_upsert_account` adopts any key match (`sync.py:~395`).
  Both payload accounts' transactions land on it, and each sync's snapshot is whichever was written
  last → the balance alternates between the two cards' balances depending on payload order.
- Reconnect + rename in the same step misses both lookups (key changed, id changed) → duplicate.

**Fix:** mark accounts absent from a successful full fetch as `stale`/`closed` (and stop carrying them
forward after a user-confirmed close date); detect key collisions *within one payload* and disambiguate
(append the provider id or last-4); surface "possible duplicate account" when a new synced account
appears at the same institution with a similar balance as a recently orphaned one.

## 7. Other correctness notes (lower)

- **Headline vs series use different rates.** `ledger.net_worth` converts at each account's `balance_date`
  rate (`ledger.py:170`); ARCHITECTURE says "current views use the latest rate"; the series uses the
  point date's rate. Headline ≠ last point for a stale foreign account.
- **Two notions of "today".** `ledger._today()` is UTC; `recompute_derived_balance` uses `date.today()`
  (server local, `investments.py:1049`); architecture says snapshots bucket in the household timezone.
  Late-evening edits can land on different days depending on path.
- **Revaluation reads hidden transactions** (`reports.py:925-933` has no `is_hidden` filter) while cash
  flow excludes them → a hidden row in a foreign account shows up as revaluation. For balance continuity
  that's arguably right, but then cash flow is wrong for the same row; pick one and document it.
- **Performance:** `cash_flow_series` reruns `_load_entries` per bucket (`reports.py:1194-1197`), each
  reloading the category map + owner map + investment rows. Fine for 12 months, poor for daily/weekly
  over years. `_revaluation` and `_appreciation` also do per-account queries. Load once, bucket in memory.
  (ARCHITECTURE/PLAN "precomputed rollups" are also not implemented — not needed yet.)

## 8. Understandability of the reports

**Net worth panel (`frontend/src/pages/Reports.tsx:414-437`, `components/Reconciliation.tsx`)**
- The panel's headline is the *delta*, labelled "Net worth change"; the chart is the *level*. There's no
  current-level number next to the chart, and no link between it and the Accounts headline — which, per #2,
  disagree. Show "Net worth $X (as of …)" and the delta together.
- **No per-point completeness signal.** "Account not yet tracked", "no price", "no rate" and a real dip all
  render identically. Add per-point coverage from the API and mark points (hollow symbol / dashed segment
  / band) where coverage is partial; add "Checking starts here" markers.
- `smooth: true` (`Reports.tsx:103`) overshoots at cliffs and draws curves through carried-forward flat
  segments, implying movement between points that the data doesn't have. Use straight segments (or
  `step: 'end'` for carried-forward values).
- The x-axis is a category axis, and the first point is the window start (e.g. "Jan 15") followed by
  month-ends, so spacing is uneven in time but drawn even. Use a time axis.
- Reconciliation copy — "Net worth moves for four reasons… the fourth is what is left over, and it should
  be nothing" — is precise but reads as "something is broken" whenever #1–#5 fire, which is most of the
  time. Say *why* in the row itself: "Checking: $22,200 — balance history starts Mar 15; earlier change
  can't be explained". Stop computing "market appreciation" from a position whose price appeared mid-window
  (§5): today it hides the problem in the one row the reader trusts.
- There's no way to drill from a point to the accounts that made it. A per-account stacked area (or an
  accounts table under the chart for the hovered point) would make every swing self-explaining — and
  `net_worth_points_by_account` already exists.

**Owner filter semantics.** Net worth is account-scoped; cash flow, Sankey and spending are row-scoped
(ADR-0026). The small-print attribution notes explain this correctly, but the same chip drives both on one
page, and the numbers won't add up across owners. Consider a visible "(accounts owned by X)" vs
"(entries attributed to X)" label in each section title, not just when the filter is on.

**Cash flow / Sankey / spending.** Solid: one `_load_entries` feeds all three; transfers, hidden rows and
transfer-typed categories are excluded in one place; refunds aren't netted; investment fees are labelled.
Two readability issues:
- An unlinked card payment shows up as −X "expense" in checking and +X "income" on the card. With #1
  fixed that's still true until the transfer is linked — worth a "possible transfer" hint in the Sankey
  when an income and an expense of equal magnitude sit on opposite accounts within a few days.
- Cash-flow bars are dated by bucket *start* while net-worth points are dated by bucket *end*; side by side
  in the same row, the two x-axes label the same month differently.

## 9. Architecture & flow — overall

The architecture is sound and unusually well-reasoned: manual-first ledger with sync as another writer,
connections decoupled from history, provenance (`field_sources`), RLS tenant isolation, one FX resolution
path, a four-term reconciliation designed to *fail* when a term is wrong. The report code is careful about
rounding, half-open date bounds, hidden-account exclusion and identity-vs-label keys.

The gap is the **balance model**, not the report math. Every issue above traces to three things the
design never nailed down: (a) what sign a balance has, (b) what an account's value is *before and between*
observations, and (c) what "unknown" looks like (it currently looks like 0). The reconciliation noticed —
that's what the `unexplained` line with its accounts list is doing — but the UI presents it as a residual
rather than as the diagnosis.

Code-quality note: the docstrings are thorough to the point of burying the code (e.g. `reports.py` is
~60% prose). The invariants they state are good; many would be better as tests (e.g. "series last point ==
headline", "Δbalance == Σtxns between snapshots", "no liability counted with the wrong sign").

Plan-vs-code drift: daily FX fetch, report rollups, and holdings from sync (M3) are all described as
existing or planned but are absent; the docs should say "not yet" where the code does.

## Triage — which cause is yours (read-only SQL against the live DB)

```sql
-- A. Liability accounts: sign mix of their history (neg>0 with pos>0 = mixed conventions → 2× jumps)
SELECT a.name, a.type, a.is_manual, a.connection_id IS NOT NULL AS synced,
       count(*) FILTER (WHERE s.balance < 0) AS neg, count(*) FILTER (WHERE s.balance > 0) AS pos,
       min(s.balance_date), max(s.balance_date)
FROM accounts a JOIN balance_snapshots s ON s.account_id = a.id
WHERE NOT a.is_asset GROUP BY a.id ORDER BY neg DESC;

-- A2. Synced accounts typed 'other' (asset) that are probably cards/loans (negative balances)
SELECT name, institution, type, current_balance FROM accounts
WHERE type = 'other' AND current_balance < 0;

-- B. Derived investment accounts the chart values from nothing
SELECT a.name, a.current_balance, a.connection_id IS NOT NULL AS synced
FROM accounts a
WHERE a.type = 'investment' AND a.balance_source = 'derived' AND NOT a.is_hidden
  AND NOT EXISTS (SELECT 1 FROM holdings h WHERE h.account_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM investment_transactions t WHERE t.account_id = a.id);

-- C. Accounts whose transactions start before their balance history (the first-sync cliff)
SELECT a.name, min(t.transacted_at)::date AS first_txn, s.first_snap,
       s.first_snap - min(t.transacted_at)::date AS gap_days
FROM accounts a
JOIN transactions t ON t.account_id = a.id
JOIN (SELECT account_id, min(balance_date) AS first_snap FROM balance_snapshots GROUP BY 1) s
  ON s.account_id = a.id
WHERE NOT a.is_hidden GROUP BY a.name, s.first_snap HAVING min(t.transacted_at)::date < s.first_snap
ORDER BY gap_days DESC;

-- D. Ghosts: visible accounts whose last snapshot is old but still non-zero (carried forward)
SELECT a.name, a.institution, a.is_manual, max(s.balance_date) AS last_snap,
       (array_agg(s.balance ORDER BY s.balance_date DESC))[1] AS carried_balance
FROM accounts a JOIN balance_snapshots s ON s.account_id = a.id
WHERE NOT a.is_hidden GROUP BY a.id
HAVING max(s.balance_date) < current_date - 30
   AND (array_agg(s.balance ORDER BY s.balance_date DESC))[1] <> 0
ORDER BY last_snap;

-- E. Biggest single-step moves per account (points straight at the swing)
SELECT a.name, s.balance_date, s.balance,
       s.balance - lag(s.balance) OVER w AS step
FROM balance_snapshots s JOIN accounts a ON a.id = s.account_id
WINDOW w AS (PARTITION BY s.account_id ORDER BY s.balance_date)
ORDER BY abs(s.balance - lag(s.balance) OVER w) DESC NULLS LAST LIMIT 20;
```

(Run as the DB owner or with RLS context set; the app role only sees rows under a household scope.)

## Suggested order of work

1. Liability sign convention + `type` editable (#1). Highest impact; the code change is small, but
   the data migration needs per-account review because the history may already mix conventions.
2. Invariant test "series(today) == headline" and fix derived-without-holdings (#2).
3. Edit-dialog date default + never-move-current-backwards (#4). Tiny.
4. Per-point coverage in `/reports/net-worth` + render it; drop `smooth`; time axis (§5, §8).
5. Backward-derived balances before the first snapshot (#3).
6. FX fetch with backfill; price/rate gaps become "unknown", not 0.
