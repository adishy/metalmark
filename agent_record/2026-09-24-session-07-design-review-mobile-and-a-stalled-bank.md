# Session 07: Design review, the phone UI, a stalled bank, and codifying the lessons

- **Date:** 2026-09-24. The same working day as session 06, continuing after PR #3.
- **Agent:** Claude Code (Opus 5.5).
- **PRs:** #3's follow-up commits (design review, account rows), #4 `fix/mobile-ui`, and this
  docs PR.

## What was asked, in order

1. After PR #3: "let's do a quick frontend design review targeting any low hanging fruit
   improvements, button / bg contrast etc". Then "you may improve the design system as needed
   as well".
2. "in the main accounts page, owner, institution, other account metadata is really crowded —
   use emojis or stock avatars for owners … hide [the rest]".
3. A new app icon modelled on a photo of a blue metalmark butterfly. Three drafts:
   - "that's ugly try again";
   - "angle the body, go easy on the black outlines, make it better for dark mode, add more
     gradient detail / grids";
   - "don't go with the 'flat' look … a little mix between flat and skeuomorphic is okay".

   Then **"nvm don't change the icon"**. The drafts were deleted and nothing about the icon was
   committed.
4. After merging #3: "seeing a lot of UI regressions, especially in mobile where there is a lot
   of horizontal overflow". The message listed:
   - the Reports range selector;
   - the Transactions screen;
   - "bottom bar icons and charts are very touch unfriendly";
   - "dropdowns are ugly";
   - the Transactions screen was "cramped". A Monarch screenshot was given as the reference.

   It also reported that "after several syncs new transactions aren't showing up".
5. Mid-way:
   - the password field was still not recognised by Brave on iOS or by Bitwarden;
   - the Settings section picker "looks bad, make it a selectable set of tabs which you can
     scroll horizontally";
   - the review swipe "feel[s] insubstantial";
   - the category pill's "Change" label "looks broken".
6. "anything we can codify in the repo from this session?" This led to this PR.

## Design review (PR #3 follow-up)

**Measured, not eyeballed.** Contrast was computed for each common pairing.

- Three failed 4.5:1 in light mode. In each, text in a role's colour sat on a 20 % tint of the
  same colour:
  - selected chips: 3.96–4.12:1;
  - "needs review" and "not reported since" badges: 3.80:1;
  - error pills: about 4.4:1.
- Fix, in the design system: `-ink` tokens (teal-800, amber-800, red-800 in light mode; the
  role colour itself in dark mode).
- design-lint rule 10 flags the old pairing.
- DESIGN §2.1, §2.3 and §8 were updated.

Quick wins:

- Page titles went up one step, to `text-xl`.
- Phone chip rows and tab strips were changed, then changed again in step 4 below.
- The emoji input in Settings → Categories no longer stretched across the row.
- An uncategorized transaction says so on desktop.
- The all-caps eyebrow in Admin was removed.

**Account rows:** the mark stands for the institution, an avatar for the owner (👥 for Shared,
initials otherwise), and the row carries one line of text.

## The phone UI (PR #4)

`e2e/mobile.spec.ts` came first. It walks every route at 360 and 390 px and fails on anything
past the edge or anything scrolling sideways.

- The baseline: Reports was 776 px wide on a 360 px screen, and the Settings tabs ran off-screen.
- The owner's Transactions overflow never reproduced on demo data. The likely cause is iOS's
  intrinsic width for date inputs, now fixed in `index.css`.

What changed:

- **`SheetSelect`:** a pill that opens a bottom sheet, used for the Reports range and granularity
  on phones. Native selects draw the app's chevron.
- **Settings tabs:** tried as a picker first. On the owner's word they became a single
  swipeable row of tabs, the one strip allowed to scroll sideways (`data-scroll-x-ok`).
- **Transactions on phones** (modelled on the reference):
  - search and a filter button at the top;
  - day headers carrying the day's net;
  - 64 px rows with the category emoji, then "Category · Account";
  - needs-review as a dot;
  - income signed and green.
- **Bottom bar:** 64 px, with the active icon in a pill.
- **Charts:** a tap shows the tooltip, and `touch-action: pan-y` lets a vertical drag scroll the
  page.
- **Review swipe.** Monarch's pages describe the gestures but not the look, and their help
  centre blocks automated fetching. So the brief was "make it feel physical":
  - a bigger card that lifts under the finger;
  - a colour wash and a badge that grow with the drag;
  - a haptic tick at the point of no return;
  - flicks count;
  - an ease-out throw.

## The stalled bank

Diagnosed through the agent API, never through the database:

- Six syncs since Sept 23, 03:04 UTC were all `ok`, reached 13 accounts, and wrote nothing.
- `bytes_fetched` was ~18 932 each time, near-identical.
- The bridge returned no `errlist` messages.
- Pending rows from Sept 20–22 never settled.

So the bridge was serving a stale cache, and the sync was faithful to it. That became ADR-0050:
connections report `last_new_data_at` and `quiet_syncs`, and the app warns on Accounts and in
Admin. The recipe is in `docs/runbooks/debugging-a-live-instance.md`.

## Password managers

The live instance already served `name`/`autocomplete` markup, so that was not the problem. The
likely cause: the fields were controlled React inputs, and a password manager's scripted fill can
bypass React's change tracking. The form now reads its fields from the page on submit, and the
ids are plain `username`/`password`. Not verified on iOS from here. Plain-HTTP origins remain a
known limit of iOS AutoFill.

## A mistake worth recording

After PR #3 merged, the deploy went to the local `metalmark-nw` stack, on the assumption that it
was the instance behind the agent token. It was not: the live instance runs on another machine.
The mismatch was caught by comparing the household id and schema version reported by the agent
API. Lesson: identify the target by what it reports about itself, not by its port. After that
the owner deployed the live instance themselves, and asked that it not be watched until they say
so.

## Codified in this PR

- `AGENTS.md` at the root, in `frontend/` and in `backend/`, each imported by a one-line
  `CLAUDE.md`.
- `docs/runbooks/debugging-a-live-instance.md`.
- `scripts/dev-stack.sh`: a throwaway demo stack, built with host networking because
  `compose build` fails DNS on some hosts.
- ADR-0050, and ADR-0048/0049/0050 marked Accepted.
