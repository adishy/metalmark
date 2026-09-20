import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// DESIGN.md §8 calls a target-size test "cheap and worth having": assert every
// interactive control clears 44x44 (WCAG 2.5.8 / §5). It is cheap, but only if
// the exceptions are handled deliberately — otherwise it fires on correct code
// and gets switched off, which is the failure §8 rule 7 describes.
//
// The exceptions, and why each is correct rather than a waiver:
//   - Not rendered (0x0, aria-hidden, display:none) is not a target at all.
//   - An inline link inside a sentence is exempt under 2.5.8's "inline"
//     exception: its size is set by the text around it, and padding it would
//     break the paragraph.
//   - A checkbox's box is 20px by design; the *label row* is the target
//     (§4.14). So radio/checkbox inputs are measured via their label.
//
// Coverage is the part that is easy to get wrong and hard to notice. A sweep
// that visits five routes measures five screens: /settings renders only its
// default tab, and a sheet or dialog is in the DOM only once opened. So the
// sweep walks the Settings tabs and opens the overlays rather than assuming a
// route reaches them. A view that is never measured passes silently, which
// looks identical to a view that is clean.

const MIN = 44;

/** Routes that render their whole content on load. */
const ROUTES = ["/accounts", "/transactions", "/review", "/reports"] as const;

/** Only one Settings tab is mounted at a time; the default is "categories". */
const SETTINGS_TABS = [
  "categories",
  "tags",
  "currencies",
  "household",
  "owners",
  "rules",
  "profile",
] as const;

/** Query-driven panels paint after the tab switches, so the first measurement
 *  would otherwise catch a loading state and report fewer controls than exist. */
const SETTLE_MS = 300;

async function undersized(page: import("@playwright/test").Page) {
  return page.evaluate((min) => {
    const describe = (el: Element) =>
      `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}` +
      `[${(el.getAttribute("data-testid") ?? el.textContent ?? "").trim().slice(0, 24)}]`;

    const targets = [...document.querySelectorAll<HTMLElement>("button, a, input, select")];
    const bad: string[] = [];

    for (const el of targets) {
      if (el.closest('[aria-hidden="true"]')) continue;

      const box = el.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) continue; // not rendered
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;

      // Inline link in a sentence — 2.5.8's inline exception.
      if (el.tagName === "A" && style.display === "inline") continue;

      // A checkbox/radio delegates its target to the enclosing label.
      if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
        const label = el.closest("label");
        if (label) {
          const lr = label.getBoundingClientRect();
          if (lr.width >= min && lr.height >= min) continue;
          bad.push(`${describe(el)} (label ${Math.round(lr.width)}x${Math.round(lr.height)})`);
          continue;
        }
      }

      if (box.width < min || box.height < min) {
        bad.push(`${describe(el)} ${Math.round(box.width)}x${Math.round(box.height)}`);
      }
    }
    return bad;
  }, MIN);
}

test.describe("target size (SC 2.5.8)", () => {
  test(`every control on every page is at least ${MIN}x${MIN}`, async ({ page }) => {
    await login(page);
    const failures: string[] = [];

    // Label every finding with the view it came from — a bare list of controls
    // across seven screens is not actionable.
    const measure = async (view: string) => {
      await page.waitForTimeout(SETTLE_MS);
      failures.push(...(await undersized(page)).map((b) => `${view}  ${b}`));
    };

    // /review is included, but the seeded ledger has nothing in needs_review,
    // so what gets measured here is the empty state — the swipe deck's two
    // buttons are only reached by review.spec.ts, which seeds its own queue.
    // Acceptable because both carry an explicit `min-h-11`; it would not be if
    // they were styled any other way.
    for (const path of ROUTES) {
      await page.goto(path);
      await expect(page.locator("main")).toBeVisible();
      await measure(path);
    }

    await page.goto("/settings");
    await expect(page.locator("main")).toBeVisible();
    for (const tab of SETTINGS_TABS) {
      await page.getByTestId(`settings-tab-${tab}`).click();
      await expect(page.getByTestId(`settings-panel-${tab}`)).toBeVisible();
      await measure(`/settings#${tab}`);
    }

    // Overlays are in the DOM only while open, so the sweep has to open them.
    // Navigating away is the reset: it is independent of how each overlay
    // chooses to close, so a change to Escape handling cannot silently strand
    // the sweep on an open sheet.
    await page.goto("/transactions");
    await expect(page.getByTestId("txn-list")).toBeVisible();
    await page.waitForTimeout(SETTLE_MS);
    const firstRow = page.locator('[data-testid^="txn-row-"]').first();
    // Not a skip if this is empty. The sheet is the biggest surface in the app
    // (73 controls), and a guard that quietly steps over it is how it went
    // unmeasured the first time this spec was written.
    await expect(firstRow, "seeded ledger must have a row to open").toHaveCount(1);
    await firstRow.click();
    await expect(page.getByTestId("txn-delete")).toBeVisible();
    await measure("/transactions (detail sheet)");

    await page.goto("/transactions");
    await page.getByTestId("import-csv").click();
    await expect(page.getByTestId("import-cancel")).toBeVisible();
    await measure("/transactions (import dialog)");

    expect(failures, `undersized targets:\n${failures.join("\n")}`).toEqual([]);
  });
});
