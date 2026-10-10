# Net-worth chart: an axis that follows its figures, and date labels with room — 2026-10-10

User request: “currently, in net worth change charts, the range starts the same no matter what range of values it is presenting - so values like 100k 101k 97k 105k appear as a straight line because the graph is scaled in 100k increments. let's fix that so the scale adjusts to the change”

## What was found

`netWorthOption` (`frontend/src/lib/netWorthChart.ts`) — the one builder behind the chart on Accounts and on Insights — gave its value axis no range, and an ECharts value axis includes zero unless told otherwise. $97k–$105k was drawn on `$0 … $120k`: the whole movement in the top tenth of the plot.

## What changed

- The value axis sets `scale: true`, so it is ruled around the window's figures (`$96k … $106k` for the figures above). A series that reaches or crosses zero still shows zero.
- `minInterval: 10` (`VALUE_STEP_MIN`) is the floor: without it a window whose only movement is cents is ruled in dimes, drawn as a full-height climb, and labelled `$100,000.10` in the wide layout's fixed 68 px gutter. With it that window draws `$100k`, `$100.01k` and a flat line.
- The area fill stays. It now reaches down to the axis's lowest rule rather than to zero; it is a 15 % tint, not a measured quantity, and the change strip keeps its own zero.
- `docs/DESIGN.md` §2.9 states the rule, and that bar charts keep their zero baseline.
- Tests: three on the *drawn* axis (through the SVG renderer) — ruled around the figures at phone and wide sizes, zero kept when the figures reach or cross it, cents not magnified. The existing drawn-label test asserted the first tick was `$0`; it now asserts every tick is a shortened money label.

No ADR: no accepted decision covers the axis's range, and nothing but the picture changes — no figure, total or tooltip.

## Verification

Run in `node:22-bookworm-slim` with a scratch `node_modules` volume (the host has none):

- `npm run typecheck`, `npm run lint:design`, `npm run lint`: clean.
- `npm test`: 53 files, 647 tests passed.
- Looked at: the option rendered through ECharts' SVG renderer at 310×280 and 1104×260, before and after, with stand-in colours. Ticks read off the drawn chart for seven ranges (the request's, cents, flat, $20–$90 of movement, the demo household's, $1.2M, crossing zero).

With the user's go-ahead, the stale stack's four containers were then stopped (volumes kept) and `./scripts/dev-stack.sh up` brought up the demo household for this tree:

- `review-shots.mjs`: every page, both themes, 390 and 1280 px.
- e2e in the pinned Playwright image: `chart-interaction`, `insights`, `mobile`, `desktop`, `happy-path`, `a11y` — 47 passed.

`npm run lint` (ESLint) is not a gate and does not run in this repo: ESLint 9 finds no flat config, on `main` as well. It was reported as clean in the first pass of this session; it was not.

## Second request: the date labels

User, on seeing the screenshots: “can we also space out the date labels better in the x axis? … it looks very crowded … make sure the fixes look good in every level of time filter”

On a phone card a week read as seven labels touching, and a month as `Sep 13, Sep 21,` a hole, `Oct 01, Oct 05, Oct 09` — ECharts' time-tick search restarts at each month, and `hideOverlap` only drops a label once it touches the next.

- `xLabelPoints` now chooses the labels: every `n`th point of the series counted back from the newest, sized to the plot's width (`LABEL_PITCH`), handed to the axis as `axisLabel.customValues`. The newest point is always named; a month-end that crowds it gives way; a coarse window's opening day is not named.
- The newest label is centred on the plot's right edge, and the phone clipped it (`Oct 1`). The phone layout now reserves `X_LABEL_ROOM` on the right as the wide one already did.
- Looked at: all eight ranges (1D, 1W, 2W, 1M, 3M, 6M, 1Y, All) on Accounts at 390 and 1280 px, before and after, plus the Insights chart at both widths.
- `npm test`: 654 passed; `tsc --noEmit` and `vite build` clean; design-lint clean.

## Third request: review by a separate agent

User: “review the pr with a separate agent, address any comments made and ensure the build completes”

A general-purpose subagent reviewed PR #49 read-only and confirmed its findings by rendering through the SVG renderer. All seven were taken:

1. **A brushed window lost most or all of its date labels** (regression; the labels were chosen once, for the whole series). `brushedAxes` chooses them again for the window, and the brush's handler in `Accounts.tsx` merges that into the drawn chart; `brushEvents` now hands the handler the chart instance. `showMinLabel`/`showMaxLabel` are on, since a time axis hides its end labels by default.
2. **A movement under $10 drew a stray tick at the data's own minimum** (`$123,456.78`) — the label `minInterval` was said to prevent. 3. **`minInterval: 10` was a USD-sized constant.** 4. **Seven rules on a phone.** One fix for the three: `valueExtent` sets the axis's range — a floor relative to the balance, a step never under ten from a thousand up, and the narrowest round range of `valueTicks` steps give or take one — given to ECharts as `min`/`max` functions so a brush re-rules the axis. A first version pinned the range to exactly `valueTicks` steps; on the screenshots that left the All view on `$0 … $80k` with the line in its lower half, which is the complaint this branch exists for, so the count was loosened by one either way and the figures now fill at least half the plot (tested). ECharts derives its interval from the range, so a range is only offered when its own rounding lands on the step (`ruledIn`).
5. **Stale comments and dead code**: `BUCKET_MS` and the x axes' `minInterval` removed (nothing drawn read them once the labels were chosen); `VALUE_GUTTER`, `X_LABEL_ROOM`, `xLabelPoints` and DESIGN §2.9 corrected.
6. **Tests**: the cents test used a minimum that hid finding 2 and now does not; the pitch test that restated the function is replaced by expected labels; added coverage for the labels reaching the right axis, the phone's right margin, week/quarter/year, zoomed windows on the drawn chart, and `valueExtent` directly. The tick parser fails as an assertion.
7. **A young coarse window had one label**: the opening day is named when it would otherwise be alone.

Verified again: `tsc --noEmit`, design-lint, `npm test` (670 passed); the brush dragged in a real browser on 3M and 1Y with the console watched (no ECharts warnings from the merge) and the zoomed charts looked at; e2e and CI as recorded in the PR.

## Left as it is

- A window just above a unit boundary can mix units on one axis (`$1238k`, `$1.24M`), because `formatMoneyTick` shortens only where the result is exact to two decimals. The formatter already did this for any range crossing a boundary; a tighter axis makes it more common above $1M.
- A history that is all zeros is left to ECharts' own range (`$0 … $1.20`).
