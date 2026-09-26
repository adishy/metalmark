# Session 10: Every open issue — three stacked PRs, and two reviewer agents

- **Date:** 2026-09-26. Continues session 09 after PR #41 (`0f62cfe`).
- **Agent:** Claude Code (lead) plus background agents, up to seven at once — each lane in its
  own worktree on its own compose stack. The lead did the surveys, the plan, every integration
  merge, and the review of what the lanes produced.
- **Landed as:** **PR #43** (the correctness audit, `00861ac`) and **PR #14** (recurring,
  `a7cec16`) merged to main; **PR #44** (the desktop stack, 17 issues), **PR #45** (budgets)
  and the session/tooling PR opened behind them. PR #16 (the Amazon spike) was left open
  deliberately — a `Proposed` ADR is the owner's call, not the session's.

## What was asked

One message:

> let's address all open issues. These are a bunch of disparate issues so prioritize, plan and
> bundle - I don't want to have to merge 20 separate PRs so work on it in separate branches and
> stack those into 1-2 manageable larger PRs. let's also do a side task to create a couple of
> reviewer agents, one for correctness and one for visual / UX (visual UX agent should spin up
> the app and look for visual regressions / take screenshots etc., correctness agent looks to
> ensure total numeric and overall info correctness, presented in a consistent and
> understandable manner). break things down and plan, then once happy go ahead and implement

23 issues were open (#18–#40), plus two PRs open by choice from session 09. Two more
instructions arrived mid-session: *"let's fix that pre-existing gate/CI divergence and
flakiness (add an issue and chase it down after this also)"*, and *"once all are merged and the
image is published, pull it in locally and test e2e locally"*.

**Decisions the owner made**, each asked rather than assumed:

| Question | Answer |
|---|---|
| #27 budgets — the one issue needing a migration and an ADR | **Its own third stacked PR**, so a schema change never hides inside a UI pass |
| Where the reviewer agents live | **`.claude/agents/`, committed** |
| PRs #14 / #16 | **Merge #14**, leave #16 — a Proposed-ADR spike is the owner's call |
| #30 — desktop Review contradicted DESIGN §9.3 | **A triage table**, explicitly revising the recorded decision |
| #39 — the palette only reached 1.11:1 page→card | **Ship as measured**; moving content roles would have changed the highlight the owner had just said was fine |
| Who merges | **"Merge as they go green"** |

## How it was run

Two `Explore` agents surveyed the frontend and backend first — which is what turned 23
issue-shaped items into eight lanes and one ADR-numbering problem. The plan is at
`.claude/session10/PLAN.md`.

The surveys changed the work in four ways worth recording:

1. **`e2e/desktop.spec.ts` pins the exact desktop geometry** (column counts and ratios, the
   Settings rail at 224 px, and that each collapses to one column below `lg:`). Every layout
   issue was going to fail it *by design*, so updating an assertion became part of each change
   rather than a nuisance afterwards.
2. **PR #14 needed a merge, not a rebase** — it had two merge bases, because it merges
   `feat/insights`/`feat/owner-income`, which had already landed via #17. A rebase would have
   been a fight; merging main in was clean.
3. **`docs/audits/2026-09-26-correctness-and-robustness-audit.md` is not in the repo**, though
   issues #18–#22 quote it. The findings live in the issues, so that is what the work cites.
4. **`use_pct = any(...)`** in `set_splits` meant a leg carrying both `amount` and `pct` silently
   flipped the whole set to percentage mode — one finding's class, found in its own function.

Eight lanes, each a worktree under `.claude/worktrees/` on `MM_PROJECT=mm-<x>` with a port
overlay:

| Lane | Issues |
|---|---|
| `fix/correctness-audit` | #18–#22, then two more found while verifying |
| `design/foundation` | #39 theming, #37 searchable `Combobox` |
| `design/charts-look` | #38 blink, #40 chart look, #32 net-worth windows, #34 treemap |
| `design/layouts` | #33 grids, #36 bounded runs, #31 allocations, #30 Review + ADR-0057 |
| `feat/command-palette` | #35 |
| `feat/categories-ux` | #23, #24, #25 |
| `feat/txn-desktop` | #26, #28, #29 |
| `feat/budgets` | #27 + ADR-0058 |

Migration order was the one hard constraint: only budgets writes one, and it had to branch
*after* #14 landed, or main would hold two migrations both claiming `down_revision = "0013"`.

## What changed

### 1. Correctness audit — PR #43 (`00861ac`)

Seven commits. The five audit findings (#18–#22) plus two found while verifying them:

- **F1** — a percentage split is validated against the whole. Percentages summing to 60 or 250
  were silently rescaled to fill 100% of the amount; a leg with two shares flipped the set to
  percentage mode and every `amount` was ignored. ADR-0056.
- **F2** — the client adds money in **integer minor units**. The split "balanced" gate was
  `Math.abs(sum - target) < 0.005` against a server requiring exact decimals, so the UI could
  enable Save for a split the API then rejected. This was the one place ADR-0005 was not
  honoured end to end.
- **F3** — five sites computing "today" outside the ledger's UTC `today()`, three named in the
  issue and two the sweep found. `income.compute_summary` was the user-facing one; its existing
  test passed an explicit `today` and so never exercised the default.
- **F4** — a zero-amount transfer leg is refused, **and** `link_transfer` now refuses a leg
  already in a group. The picker excluded those legs and the linker did not.
- **F5** — `auto_match_transfers` fetched candidates one query per row. Now batched, with the
  equivalence proven rather than asserted: identical linked pairs at chunk sizes **1, 2, 3, 4,
  5, 8 and 250** on consecutive-day and same-day near-duplicates, plus a differential harness
  agreeing 40/40 across randomly seeded fixtures.
- **The frame-attribution fix** — see "What went wrong"; it is the reason this PR merged first.
- **The vitest timeout** — see "What went wrong".

### 2. Desktop and design — 17 issues

- **#39** moved only the four background/border roles, in both themes. The measured win is at
  the **edges**: border-on-a-card 1.23 → 1.48 in both themes, and in dark a border that held
  *exactly* the same value as the well it sat on — **1.00:1, invisible by arithmetic** — now
  1.26:1. Page-to-card reached only 1.11, and that is a ceiling, not a choice: at the next
  value down `accent`, `positive` and `warning` all fall under 4.5:1 on the page. The owner
  looked at before/after screenshots and shipped it as measured.
- **#37** added a searchable, APG-complete `Combobox` and migrated **17 of 32** `<Select>` call
  sites, leaving 15 native — closed vocabularies and app-bounded lists, where a native select is
  the right control. The rule that settled it is now in DESIGN §4.4: *the question is where the
  list comes from, not how long it is*.
- **#38** was not a missing animation setting but a race this app was losing: `echarts-for-react`
  builds a throwaway instance, waits for its `finished` event, disposes it and builds the real
  one — so the first option landed in the throwaway, *it* drew and animated, and its finishing
  was the library's signal to build the real instance and animate again. Traced frame by frame:
  ink climbs to 35,785 px, is wiped to 1,788, climbs again. Fixed by mounting the chart only
  once its box is measured, with a regression test **verified to fail against the unfixed
  component**.
- **#40/#32/#34** gave the net-worth chart a hover readout, a range brush and a secondary bar
  strip; eight windows down to a day, opening on two weeks and remembered per viewer; and a
  treemap beside the donut.
- **#33/#31/#36** reworked the desktop grids. The cause was `lg:items-start` on grids whose
  columns grow at different rates, so the shorter column simply ended. Each was restructured
  rather than given a frozen height, and one instance — Accounts — had **no `lg:` classes at
  all** despite §9.3 calling for a card grid.
- **#30** reversed a recorded decision: Review is a deck on a phone and a **triage table** at
  `lg:`. This also required amending §9's structural rule (*"a page must not have two JSX trees
  branching on viewport"*), which a deck and a table cannot satisfy. The exception is named in
  §9 with §9's own rationale answered — the rule exists because *"the untested one rots"* — by
  both trees carrying e2e coverage. ADR-0057.
- **#35** added a ⌘K palette, lazily imported so it is absent from the entry chunk, with chords.
- **#23/#24/#25** made categories editable in place; made deleting one reassign its transactions
  and splits with the count stated first, defaulting to the **sentinel** (`category_id = NULL`,
  which is what "Uncategorized" is — an absence, not a row); and replaced the OS colour dialog
  with a radio-group swatch picker whose palette is read from the tokens at runtime.
- **#26/#28/#29** made the desktop ledger editable in place, pinned the detail pane so a row
  deep in the list opens in view, and turned the filter pills into per-type multi-select panels.

### 3. Budgets — PR C

ADR-0058: one household RLS table keyed unique on `(household_id, category_id, period)`, whose
spend side **is** `reports.spending_by_category` over the period's own window — so the number
beside a plan is the number the Spending report prints, by construction rather than by
agreement. Written in Settings → Categories (the only place a category with no spend yet can be
planned for), read in Insights → Budgets. Migration 0015 is proven on a populated database:
every household table fingerprinted before and after, byte-identical, with the unique key and
CHECK constraints shown to reject at the database and a category delete cascading only its own
plan.

### 4. The reviewer agents

`.claude/agents/correctness-reviewer.md` and `visual-reviewer.md`, committed with the
`.gitignore` change that stops a session's scratch appearing as untracked in every worktree.
Both report findings and change nothing; both are written to be usable on any change, not this
session's. Their bodies are self-contained by construction — a subagent's body *is* its system
prompt, with no Claude Code prompt behind it.

**The correctness reviewer earned its place immediately** (findings in "What went wrong").

## Verification

Every stack: `./scripts/verify.sh` — secrets, lint, pytest, frontend, contract, drill, e2e,
walkthrough, prod. Plus `npm run typecheck`, `npm run lint:design` and the unit suite, and e2e
in the pinned Playwright image. CI ran the same scripts on each PR.

Anything a person sees was **looked at** (`frontend/AGENTS.md`), and the lanes were held to the
same standard. Two of the session's real bugs were caught only by looking: a `/review` page that
went blank white, and a budget dialog opening with a red "Required" under an untouched field.

## How it landed

**PR #43** (correctness) and **PR #14** (recurring) went into main. #43 merged first on purpose:
it carried the fix for the `failure()` path bug, and #14 could not show a green gate until that
fix reached main. It also settled the migration order — main then held 0014, so the budgets work
could chain 0015 off it instead of forking the alembic history at 0013.

The desktop stack (`merge/session-10-design`) is eight lane merges plus current main, with four
conflicts resolved by hand, one of them genuinely semantic: `Admin.tsx`, where the layouts lane
had restructured the cards into columns while main had applied a small `relative` fix to the
jobs scroll region. The branch's structure was kept and main's fix re-applied inside it —
dropping it would have silently reintroduced the 360 px page-widening bug that fix exists to
prevent.

The merge was also where a stale assertion earned its keep: the palette's test asserted 17
destinations and the assembled app produced 18, because the recurring tab had landed in between.
The number and the arithmetic in its comment were both updated, and the count was deliberately
**not** derived from the lists under test — its stated job is to catch a destination going
missing, and a derived count cannot do that.

Budgets was briefly merged into the desktop stack to prove the two compose (a full gate run over
the merged state), then taken back out, so the migration stays in its own PR. The merged-state
run was not wasted: it is the evidence that the two stacks work together.

## What went wrong (recorded because it shaped the run)

- **`scripts/verify.sh` and CI disagreed, and the gate was the wrong one.** `verify.sh pytest`
  runs pytest *inside* the api container where the tree is `/app`; CI runs `uv run pytest` on a
  runner where it is not. `failure()` decided which stack frames were the app's with
  `"/app/" in filename`, so in the container the *test file's* own frame qualified and `at`
  named the wrong file. Diagnosed by copying the identical tree to `/tmp/napp`, where the same
  test passed. It mattered beyond the gate: `at` is all an operator gets, because ADR-0047 keeps
  the message and traceback out of the log. Filed as **#42** with the structural half left open
  deliberately — whether `verify.sh` should match CI or narrow its claim.
- **A later lane found the same gate passing while testing nothing relevant.** Its 401 probes
  hardcode `http://localhost:8000` while everything else uses `docker compose exec`, and on this
  host *another lane's API* answers :8000 — `GET http://localhost:8000/budgets` returns 404. So
  twelve green lines were somebody else's stack.
- **The lead made the ports up wrong.** The per-lane overlays mapped the API to `127.0.0.1:68xxx`,
  above the 65535 maximum, so compose rejected them; two lanes had to work around their stacks.
  Fixed, and every port verified in range.
- **The lead accused a lane of editing the wrong tree, twice wrong.** A stray one-line edit did
  appear in the main repo's `DESIGN.md`, but the chart lane's own worktree was complete and
  correct all along: the check was `grep -c emphasisStrip`, which returns 0 in a *correct* file
  too, because the lint alternation is a single regex. The claim lived in a survey's prose, not
  the file, and it was passed on as fact — to the user and to the agent, which spent an amend on
  it. The lesson is the session's own theme: check for a thing in a way that could actually find
  it, and never read absence of evidence as evidence.
- **Merges were chained while the first was still conflicted**, which git refused and which left
  a confusing tree for a moment. The integration merges were then done one at a time, with the
  resolutions written into each merge message.
- **Load manufactured failures all session.** With five to seven stacks and their test runs on
  eight cores, vitest's 5 s default and Playwright's 30 s default both produced timeouts that
  passed in isolation — `IncomeDialog`, `RuleBuilder`, `a11y.spec.ts`, `review.spec.ts:242`,
  nineteen unit tests once. Every one was re-run before being believed, and none was real. A
  `testTimeout` was made explicit; the rest is environmental.
- **`e2e/mobile.spec.ts`'s sideways sweep passed against a blank white page.** It measures
  overflow and never asserts the route rendered, which is how a `/review` breakage nearly shipped
  — the page went white and the sweep reported nothing wrong. One lane then proved its *own*
  instrument by injecting a wide element and confirming the sweep named it: a clean sweep is a
  measurement, not an absence.
- **The correctness reviewer found the branch's own new figure disagreeing with itself.** The
  net-worth headline said "Up $55,079.24 over the year" while the bars beneath it — added by this
  same branch — summed to $37,707.02, because the headline's baseline was a point on which all
  five accounts were `missing`. The chart already had the honest rule and the headline did not.
- **Two lanes' changes interacted in ways neither could see.** The charts lane put a treemap
  above the allocations list; the layouts lane had bounded that list and asserted the total's
  absolute position against the viewport. Each was correct alone. Only running e2e on the
  *assembled* branch found it — which is the argument for doing that rather than trusting each
  lane's green suite.

## After deploy (for the owner)

The owner asked at the end of the session for the published image to be pulled into the local
deployment and e2e run against it, with any divergence chased down afterwards. That happens
**after** these merges — `latest` moves on main only — so its outcome belongs to the next
session's entry rather than being predicted here. The two things already established: the local
deployment is `/home/adishy/personal.data.adishy.com/tmp/metalmark-nw/deploy`, an upgrade is
`docker compose pull && docker compose up -d` with backup, migration and the data checks all
happening in the images on start (ADR-0047), and the session cookie is `Secure` under `auto`, so
the e2e has to drive the https door rather than the plain-http one (ADR-0041).
