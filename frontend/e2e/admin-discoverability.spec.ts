import { test, expect } from "@playwright/test";
import { login } from "./helpers";

// The sync control panel shipped complete — queue, runs, per-run logs, pause,
// cancel, retune, every control the request asked for, all green in CI — and the
// person who asked for it could not find it. The only link to it in the whole
// app was a muted aside in the fifth of eight Settings tabs, and the word
// "Admin" appeared nowhere in the interface.
//
// So this file is not testing a feature. It is testing that the feature has a
// door, from each of the three directions someone might arrive from. A panel
// nothing points at is not shipped, however green its tests are.

test.describe("the admin panel can be found", () => {
  test("a nav item says Admin and opens the panel", async ({ page }) => {
    await login(page);

    const adminNav = page.getByTestId("nav-admin");
    await expect(adminNav).toBeVisible();
    await expect(adminNav).toHaveText("Admin");

    await adminNav.click();
    await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();
    // The string a person scans for, in the tab title as well as the nav.
    await expect(page).toHaveTitle(/Admin/);
  });

  test("Settings carries an Admin tab, first in the list", async ({ page }) => {
    await login(page);
    await page.getByTestId("nav-settings").click();

    const tab = page.getByTestId("settings-tab-admin");
    await expect(tab).toBeVisible();
    // First, not last: the position is part of the fix. Settings opens on
    // Categories, so a tab at the end of a wrapping row is the same problem
    // one click further in.
    const labels = await page.getByRole("tab").allTextContents();
    expect(labels[0]).toBe("Admin");

    await tab.click();
    await expect(page.getByTestId("settings-panel-admin")).toBeVisible();

    await page.getByTestId("admin-open-panel").click();
    await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();
  });

  test("the Connections tab still links to the panel", async ({ page }) => {
    // The original door. Kept because it is the one someone standing in the
    // credential screen looks for, and because it is the one that existed
    // before this fix — a regression here would be a silent step backwards.
    await login(page);
    await page.goto("/settings");
    await page.getByTestId("settings-tab-connections").click();
    await page.getByTestId("open-admin").click();
    await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();
  });
});
