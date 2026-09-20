# MetalMark frontend — end-to-end & visual tests

Playwright drives a Chromium browser against the **already-running** compose
stack (`docker compose up -d`). The config does **not** start a `webServer`.

- Base URL: `http://localhost:5173` by default; override with `E2E_BASE_URL`
  (e.g. `http://web:5173` when running from another container on the compose
  network `metalmark_default`).
- Login: `owner@example.com` / `devpassword123` (override with `E2E_EMAIL` /
  `E2E_PASSWORD`). Re-seed if the DB was reset — see the repo root instructions.

## What's covered

| Spec | Covers |
|------|--------|
| `happy-path.spec.ts` | login → net worth shown → add account (list + net-worth reflect it) → add categorized Groceries expense (in list) → Reports render (`report-net-worth`, `spending-donut`) |
| `review.spec.ts` | add an **uncategorized** txn → it enters the `needs_review` queue → approve via button, and separately via the **keyboard** (`ArrowRight`) swipe path → card leaves the deck |
| `usable-m1a.spec.ts` | edit a txn's merchant and **owner** and filter the ledger by that owner (incl. the inherited-owner badge) → split a txn by amount → add/delete a category → add an FX rate |
| `reports.spec.ts` | the three ECharts surfaces actually mount (jsdom has no canvas, so vitest cannot cover them) → the **owner filter** re-scopes them → the net-worth **attribution caveat** appears with the filter and goes away without it |
| `visual.spec.ts` | `toHaveScreenshot` of the static **login page** |

Tests are **repeatable without a DB reset**: every run creates uniquely-named
data (suffixed with `Date.now()`) and asserts *relative* changes, so a
long-lived compose database never causes flake. Run serially (`workers: 1`).

## Running locally (macOS/Windows: use the compose network)

`--network host` does not reach the host on Docker Desktop, so drive the stack
over the compose network instead:

```bash
docker run --rm --network metalmark_default \
  -e E2E_BASE_URL=http://web:5173 \
  -v "$PWD/frontend":/work -w /work \
  mcr.microsoft.com/playwright:v1.63.0-noble \
  sh -c "npm ci && npx playwright test"
```

On Linux you can instead use `--network host` and the default localhost base URL.

## Visual baselines (OS/arch-stable)

Baselines are committed as `*-linux.png` and **must** be generated in the
official Playwright container so they match the CI runner (Linux). Regenerate
after an intentional UI change:

```bash
docker run --rm --network metalmark_default \
  -e E2E_BASE_URL=http://web:5173 \
  -v "$PWD/frontend":/work -w /work \
  mcr.microsoft.com/playwright:v1.63.0-noble \
  sh -c "npm ci && npx playwright test --update-snapshots"
```

Then commit the updated `e2e/**/*-snapshots/*-linux.png` files. The image tag
must match the installed `@playwright/test` version (pinned to `1.63.0`).
Keep visual coverage to deterministic/static views only — no flaky snapshots.
