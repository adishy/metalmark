import { defineConfig, devices } from "@playwright/test";

// E2E runs against the ALREADY-RUNNING compose stack (do NOT start a webServer
// here). From the host that is http://localhost:5173; inside a container on the
// compose network, override with E2E_BASE_URL=http://web:5173.
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  // Data-mutating tests share one household with no per-test reset, so run them
  // serially with a single worker for deterministic net-worth / queue math.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["html", { open: "never" }], ["list"]],
  timeout: 30_000,
  expect: {
    timeout: 10_000,
    // Baselines are generated in the pinned Playwright container, so rendering
    // is deterministic run-to-run; this only absorbs a few AA/subpixel pixels.
    // Keep it small — a 1% ratio previously waved through a visible text change.
    toHaveScreenshot: { maxDiffPixels: 100 },
  },
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 800 },
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
