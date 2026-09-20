import { test, expect } from "@playwright/test";
import { login } from "./helpers";

// Visual regression is intentionally limited to deterministic, data-free views.
// The login page has no dynamic content, so its snapshot is stable across runs.
// Baselines are committed as *-linux.png, generated in the Playwright container
// (see e2e/README.md) so they match the CI runner's OS/arch.
//
// BOTH themes are snapshotted. Light mode was the default for most of this
// project's life while every screen was dark, so a light-only regression — the
// palette is the newer of the two — had nothing to catch it.
const THEME_KEY = "metalmark-theme";

test.describe("visual regression", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`login page (${theme})`, async ({ page }) => {
      // addInitScript runs before any page script, which is what the inline
      // blocking script in index.html needs to avoid a flash of the wrong
      // theme. Setting it after `goto` would snapshot the flash.
      await page.addInitScript(
        ([key, value]) => localStorage.setItem(key, value),
        [THEME_KEY, theme] as const,
      );
      await page.goto("/login");
      await expect(page.getByTestId("login-form")).toBeVisible();
      // Let the auth /me probe settle so we never snapshot the loading state.
      await expect(page.getByTestId("loading")).toHaveCount(0);
      await expect(page).toHaveScreenshot(`login-${theme}.png`, { animations: "disabled" });
    });
  }
});

// The theme is applied by two independent pieces of code — the inline script in
// index.html before first paint, and src/theme/theme.ts once React is running.
// They read the same storage key and must agree, including on the case where
// nothing is stored at all.
test.describe("theme resolution", () => {
  const CASES = [
    { name: "stored light wins over a dark OS", stored: "light", scheme: "dark", expect: "light" },
    { name: "stored dark wins over a light OS", stored: "dark", scheme: "light", expect: "dark" },
    { name: "system follows a dark OS", stored: "system", scheme: "dark", expect: "dark" },
    { name: "system follows a light OS", stored: "system", scheme: "light", expect: "light" },
    { name: "no stored choice defers to the OS", stored: null, scheme: "dark", expect: "dark" },
  ] as const;

  for (const c of CASES) {
    test(c.name, async ({ page }) => {
      await page.emulateMedia({ colorScheme: c.scheme });
      await page.addInitScript(
        ([key, value]) => {
          // "system" is stored as the *absence* of a key, and so is "unset".
          if (value === null || value === "system") localStorage.removeItem(key);
          else localStorage.setItem(key, value);
        },
        [THEME_KEY, c.stored] as const,
      );
      await page.goto("/login");
      await expect(page.getByTestId("login-form")).toBeVisible();

      await expect
        .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
        .toBe(c.expect === "dark");

      // The meta theme-color drives mobile browser chrome; it must track the
      // resolved theme, not the choice.
      const meta = await page.getAttribute('meta[name="theme-color"]', "content");
      expect(meta).toBe(c.expect === "dark" ? "#020617" : "#f8fafc");
    });
  }

  test("the toggle flips the theme live, with no reload", async ({ page }) => {
    // The toggle lives in the app shell's header, so this one needs a session.
    await login(page);
    const isDark = () =>
      page.evaluate(() => document.documentElement.classList.contains("dark"));
    const before = await isDark();

    // Count navigations so the assertion cannot be satisfied by a reload —
    // "repaints without a reload" is the property under test, and an earlier
    // version of this test reloaded and then asserted anyway.
    let navigations = 0;
    page.on("framenavigated", (f) => {
      if (f === page.mainFrame()) navigations++;
    });

    await page.getByTestId("theme-button").click();

    await expect.poll(isDark).toBe(!before);
    expect(navigations, "theme flip must not navigate").toBe(0);
    // The stored choice has to survive, or the next load flashes the old theme.
    const stored = await page.evaluate((key) => localStorage.getItem(key), THEME_KEY);
    expect(stored).toBe(!before ? "dark" : "light");
  });
});
