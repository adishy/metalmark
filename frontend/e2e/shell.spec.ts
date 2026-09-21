import { test, expect } from "@playwright/test";
import { login } from "./helpers";

// AppShell-level behaviour: the things that belong to the frame every page
// renders inside rather than to any one page. One so far — where the page is
// scrolled to when you arrive at it.

// Transactions to Reports, and the pages are the point: the assertion is that
// the *destination* starts at the top, so a destination that cannot hold a
// scroll offset would pass whatever the code did. Review is exactly that page at
// 1280x720 — 720 px tall in a 720 px window, `max scroll 0` — so navigating to
// it measures nothing, which is what the first version of this test did. Both
// of these are 2553 and 2306 px at 1280x720 and both scroll at every width.
const FROM = "transactions";
const TO = "reports";

/** Scroll the current page to the bottom and return where that landed. */
async function scrollToBottom(page: import("@playwright/test").Page): Promise<number> {
  const y = await page.evaluate(() => {
    window.scrollTo(0, document.body.scrollHeight);
    return Math.round(window.scrollY);
  });
  // A page that did not scroll would make the assertions below pass for free.
  expect(y, "the page did not scroll, so the test proves nothing").toBeGreaterThan(100);
  return y;
}

test("a route change starts at the top of the new page", async ({ page }) => {
  await login(page);
  await page.getByTestId(`nav-${FROM}`).click();
  // The list, before scrolling. These pages arrive empty and fill in when their
  // queries resolve, and a `scrollTo` issued on the click lands on a page with
  // nothing to scroll yet — the measurement then reads 0 and the test fails
  // claiming the page is short when it is 2553 px tall.
  await expect(page.getByTestId("txn-list")).toBeVisible();
  await scrollToBottom(page);

  await page.getByTestId(`nav-${TO}`).click();
  await expect(page.getByTestId(`nav-${TO}`)).toHaveAttribute("aria-current", "page");

  // Before this, the answer here was 1586 — the offset the ledger was left at,
  // clamped to what Reports can hold. The header is `sticky`, so navigation
  // stays put at any offset and nothing looked wrong until you read the page
  // and found the top of it missing.
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test("Back restores the page you came from instead of topping it", async ({ page }) => {
  await login(page);
  await page.getByTestId(`nav-${FROM}`).click();
  await expect(page.getByTestId("txn-list")).toBeVisible();
  const bottom = await scrollToBottom(page);

  await page.getByTestId(`nav-${TO}`).click();
  await expect(page.getByTestId(`nav-${TO}`)).toHaveAttribute("aria-current", "page");

  await page.goBack();
  await expect(page.getByTestId("txn-list")).toBeVisible();

  // What Back is riding on is the browser's own per-entry scroll restoration,
  // not the `navigationType` exemption in the shell — that check is insurance
  // for engines this suite does not run, and this test passes with or without
  // it (checked both ways). What is pinned here is the *outcome*: Back returns
  // you down the page, and a change that made arrival-at-the-top unconditional
  // — a data router, a `scrollRestoration = "manual"` — would fail it.
  //
  // Not `toBe(bottom)`: the browser restores the offset before the ledger's list
  // has re-rendered, so it lands at whatever the page can hold at that instant
  // and stays there — 1503 of 1833, measured, not a flake.
  const restored = await page.evaluate(() => window.scrollY);
  expect(restored, "Back returned to the top instead of where we were").toBeGreaterThan(
    bottom / 2,
  );
});
