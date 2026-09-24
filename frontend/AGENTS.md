# Frontend rules (React/TS, Tailwind, ECharts)

`docs/DESIGN.md` is the design system and it is checkable: read §1–§2 before touching a
component, §5 before touching layout. What follows is the short list that review keeps
catching.

## Rules

- **Tokens, not palette classes.** Colours come from the roles in `src/index.css` /
  `tailwind.config.js`. Text on a tint (`bg-accent/20`, `bg-warning/20`, `bg-negative/20`)
  uses the role's **`-ink`** token (`text-accent-ink`, …) — the plain role colour fails 4.5:1
  there in light mode (DESIGN §2.1). design-lint rule 10 enforces it.
- **Phone first, and nothing scrolls sideways** (DESIGN §5). Build at 360 px. A row of choices
  that does not fit becomes a `SheetSelect` pill on phones, not a scrolling strip; chips may
  wrap. The one allowed sideways strip is marked `data-scroll-x-ok` (the Settings tabs).
- **Targets ≥ 44×44**, bottom-bar destinations ≥ 56. Grow hit areas with padding.
- **Dropdowns:** `Select` from `components/form` (it draws the app's chevron). Filters and view
  switches on a phone are `SheetSelect`.
- **Categories are written with their emoji** — `categoryLabel()` from
  `components/CategoryPicker`.
- **Money:** amounts are `text-base font-semibold`, never truncated; an expense is not red
  (DESIGN §6). Dates render through `<Day>`/`<Instant>`, never as ISO text.
- **Charts** go through `components/Chart` and the helpers in `theme/chartInteraction.ts`
  (tap-to-tooltip and `touch-action: pan-y` come from there).
- **Forms a password manager fills** read their values from the form on submit, not from
  React state (see `pages/Login.tsx`).
- **Branding:** user-visible copy says "MetalMark Money".

## Checks, in order

```bash
npm run typecheck && npm run lint:design && NODE_OPTIONS=--no-experimental-webstorage npm test
```

(`NODE_OPTIONS` works around Node 25's built-in `localStorage` breaking jsdom tests.)

Then **look at it**. With a demo stack up (`../scripts/dev-stack.sh up`):

```bash
# every page, both themes, 390 and 1280 px, into $OUT
# (CHROME=/path/to/chromium if Playwright's own browser is not installed)
OUT=/tmp/shots node scripts/review-shots.mjs
```

and run the e2e specs that cover the change, in the pinned Playwright image (the version must
match `@playwright/test` in `package.json`, or the visual baselines will not match):

```bash
docker run --rm --network mm-dev_default -e E2E_BASE_URL=http://web:5173 \
  -v "$PWD":/work -w /work mcr.microsoft.com/playwright:v1.63.0-noble \
  sh -c "npx playwright test e2e/mobile.spec.ts e2e/review.spec.ts"
```

`e2e/mobile.spec.ts` fails on anything past the edge at 360/390 px — run it for any layout
change. After an intentional change to the login page, regenerate its baselines with
`--update-snapshots e2e/visual.spec.ts` in the same image and commit the `*-linux.png` files.
`bank sync` specs need the fake provider (CI has it; `verify.sh e2e` sets it up).

README screenshots come from the demo household only (`scripts/readme-screenshots.mjs`) —
never from a real instance.
