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
//
// Data is the other half of that, and it bites the same way. A control that
// renders *per row of data* cannot be measured on a database where that data
// does not exist — the sweep sees the empty state, reports zero failures, and
// the layout is only ever exercised by a human who happens to have rows. The
// detail sheet's tag chips went out at 26px tall for exactly this reason: the
// demo ledger had no tags, so those buttons never appeared to be measured.
// This is an argument for the demo seed being *broad* (a little of everything
// the UI can render), not just for it being present.

const MIN = 44;

/** Routes that render their whole content on load. `/admin` is in here rather
 *  than reached through the settings walk because it is a route of its own —
 *  and a route nobody measures is a route nobody has measured. */
const ROUTES = [
  "/accounts",
  "/transactions",
  "/review",
  "/insights/overview",
  "/insights/allocations",
  "/admin",
] as const;

/** Only one Settings tab is mounted at a time; the default is "categories". */
const SETTINGS_TABS = [
  "categories",
  "tags",
  "currencies",
  "household",
  "data",
  "connections",
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

    // /accounts carries two views and mounts one, so the route sweep above only
    // ever measures the balances one — the same gap the Settings walk below
    // exists to close. The investments view brings controls the balances view
    // does not have at all: the group-by tab strip and the view switcher itself.
    // The seeded ledger has no investment accounts, so what is measured here is
    // the empty states plus those controls; a holding row's own controls would
    // need the demo seed to be broader (see the note on data above).
    await page.goto("/accounts");
    await expect(page.getByTestId("accounts-view-balances")).toBeVisible();
    await page.getByTestId("accounts-view-investments").click();
    await expect(page.getByTestId("investments-view")).toBeVisible();
    await measure("/accounts#investments");

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

    // The Insights window is the same gap one level down. The route sweep above
    // measures the range and granularity chips, but the custom range's two date
    // inputs are in the DOM only once Custom is picked — and date inputs are
    // exactly where a native control's own height can win over the padding that
    // was supposed to set it.
    await page.goto("/insights/overview");
    await page.getByTestId("range-custom").click();
    await expect(page.getByTestId("range-start")).toBeVisible();
    await measure("/insights/overview#custom-window");

    expect(failures, `undersized targets:\n${failures.join("\n")}`).toEqual([]);
  });
});
