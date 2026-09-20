import { test, expect } from "@playwright/test";

// Visual regression is intentionally limited to deterministic, data-free views.
// The login page has no dynamic content, so its snapshot is stable across runs.
// Baselines are committed as *-linux.png, generated in the Playwright container
// (see e2e/README.md) so they match the CI runner's OS/arch.
test.describe("visual regression", () => {
  test("login page", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("login-form")).toBeVisible();
    // Let the auth /me probe settle so we never snapshot the loading state.
    await expect(page.getByTestId("loading")).toHaveCount(0);
    await expect(page).toHaveScreenshot("login.png", { animations: "disabled" });
  });
});
