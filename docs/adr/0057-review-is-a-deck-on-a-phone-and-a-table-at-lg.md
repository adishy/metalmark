# ADR 0057: Review is a deck on a phone and a table at `lg:`

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** household + Claude
- **Related:** DESIGN.md §4.14, §4.17, §9, §9.3, §9.4, §9.6; ARCHITECTURE.md §3; issue #30

## Context

`/review` presents the household's `needs_review` transactions one card at a time: the deck of
DESIGN §4.17, opened at `max-w-2xl` and centred, where a decision is a *throw* — right for
reviewed, left for ignore, up to open the transaction. It was built for a thumb, and on a phone
it is right: the card is the question and the throw is the answer, it sits on a visible stack,
and one decision never requires reading two things at once.

At `lg:` the same page has two problems, and neither is aesthetic.

1. **The deck has no keyboard route of its own.** A deck of cards is a gesture with a
   presentation attached; what its keys (← / → / `e`) drive is a card that is thrown off the
   screen over 240 ms. Someone working a backlog at a desk works it with ↑/↓ and a couple of
   keys, in order, and wants to see what is left.
2. **One card at a time is the wrong unit for a backlog.** The queue is not a stream to be
   decided as it arrives; it is a list of fifty rows that all need an answer, and a reviewer at
   a desk wants to see how many there are and how far they have got.

DESIGN §9's breakpoint rule — "**a page must not have two JSX trees branching on viewport**;
that is how the two drift and the untested one rots" — is the constraint all of this has to move
inside. §9.3 also names the shape each page takes at `lg:`, and §9.4 gives the row vocabulary
(columns, a real header row, `min-h-12` with a 44 px floor, "a real header row appears above the
list") that a desktop list is expected to speak.

## Decision

**We will give `/review` two instruments: the deck below `lg:`, unchanged, and a triage table at
`lg:` — and we will record this as §9's one exception, with both trees covered by tests.**

1. **The switch is a component switch, not a variant.** `Review` renders `<ReviewTable />` when
   `useIsDesktop()` (min-width 1024, `lib/media.ts`) and `<ReviewDeck />` otherwise. Two trees,
   mounted one at a time. The alternative — one tree with `lg:hidden` on the deck and the inverse
   on the table — is not the same thing: **the deck's ← / → / `e` handlers are on `window`**, so
   a CSS-hidden deck keeps listening, and pressing → while the table is on screen would file a
   card nobody can see. It would also leave two elements claiming to be the review queue in the
   accessibility tree, and two live regions announcing the count.
2. **The table is §9.4's row, at the queue's scale.** One row per pending transaction: account
   mark, merchant, date, category, owner, amount, then the two verdicts. Columns sized as the
   ledger's are, a real header row above the list (named for sighted readers, `aria-hidden` to
   the accessibility tree because every row already carries its values as text), rows at
   `min-h-12` with 44 px controls, amounts right-aligned (§6.1), and `max-w-7xl` per §9.1.
3. **One tab stop for the whole queue.** The row's merchant cell is a real `<button>` with a
   roving tabindex (§4.14's model, the same one `ScrollTabs` uses): the current row is
   `tabIndex={0}`, every other row's controls are `-1`, and ↑/↓ move between rows with the arrows
   *clamped* at both ends rather than wrapping. Fifty rows of four controls each is two hundred
   tab stops, which is not a route to the fiftieth row, it is an obstacle course. A focused row
   announces itself and its values: the row's other cells are a `display: contents` wrapper that
   the merchant button names through `aria-describedby`.
4. **The keys are the deck's.** ← ignore, → reviewed, `c` category, `e` open, plus ↑/↓ for the
   row, handled on the list rather than on `window` so nothing survives the breakpoint. A
   modifier (⌘/Ctrl/Alt/Shift) belongs to the browser, and a keystroke a form control is using
   belongs to that control — both are gates in the same handler.
5. **What the throw used to say, three other things say.** A decision is answered by the count,
   by a status line naming the row that was filed, and by **focus landing on the row that took
   its place** (the one below it, or the new last row when it was last). Where the deck can hide
   a failed decision behind the rejection of its own throw, the table cannot: a failed verdict
   puts the row back, keeps focus on it, and raises an alert naming the row.
6. **A category is assigned by the same pill and the same `CategoryPicker` as the deck's**, not
   by a `<select>` in the row. See the cost below.
7. **The transaction detail opens over the table as a slide-over** (§9.6, §4.17) and is still
   never a pane: the table is the whole page, so a section beside it would be paying width it
   does not have.
8. **The shared pieces are shared.** The queue's count and its three states (loading, error,
   empty) live in one module (`pages/ReviewQueue.tsx`) rendered by both trees, so the copy and
   the testids cannot drift apart — the deck's rendered DOM is unchanged by this decision.

## Consequences

- **Positive:** each pointer gets the instrument it has: a thumb gets the throw, a keyboard gets
  ↑/↓ over rows with the whole backlog visible and countable. §9.4's vocabulary makes a queue row
  and a ledger row the same object seen twice. The category, the owner and the account stop
  needing the detail sheet on one page and are shown in the row instead.
- **Positive, and the reason the exception is affordable:** the invariant §9 protects is not
  "one tree" but "no tree rots", and this decision pays for the exception in the currency of the
  rule — **both trees are tested**: `review.spec.ts` is pinned below `lg:` and is now the proof
  that the phone's Review is unchanged (the e2e diff for this change touches no assertion about
  the deck's behaviour), and the table has its own tests in `desktop.spec.ts` plus unit tests
  that mount each tree. DESIGN §9 records the exception and states that a second page wanting it
  must bring the same.
- **Negative / costs:**
  - **Two trees to keep true.** This is a real cost, paid knowingly: every future change to
    `/review` has two places to consider. The two share their copy, their states and their
    `CategoryPicker` precisely to keep the paid cost from growing.
  - **Assigning a category from the table costs a modal** (`c`, or the row's pill). A per-row
    `<select>` was rejected: it would be a second way to assign a category in the app, it would
    put a native dropdown in the middle of a keyboard-triaged row, and — the deciding argument —
    it would need its own optimistic-update and failure path beside the deck's. Inline typing in
    a converted-select row is not what this app's picker does.
  - **"N to review" counts the rows loaded, not the server's total.** The queue is read through
    `useTransactions({review_status: "needs_review"})`, whose page size is the endpoint's (50).
    With more than a page pending, the count is a page rather than a census. The deck had the
    same property before this change, so this ADR records it rather than introducing it: fixing
    it means a count on the endpoint, which is a backend decision of its own.
  - **The table has no motion to reduce**, so §2.8's reduced-motion path is the deck's alone. A
    row leaving a list is a layout change, not an animation.
- **Follow-ups:**
  - DESIGN §9's breakpoint rule now carries the one exception and its price; §9.3's Review bullet
    is the table's shape; §9.1 names the table's width; §4.17 is scoped to the phone (this
    change).
  - `e2e/a11y.spec.ts`'s target-size sweep walks `/review` and sees whatever is in the queue;
    because an empty queue tells it nothing, the table's row controls are measured in
    `desktop.spec.ts`, which seeds its own queue.
  - A future second-tree page needs its own pair of specs, not this ADR as precedent.
