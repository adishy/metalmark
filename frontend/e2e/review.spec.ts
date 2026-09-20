import { test, expect, type Page } from "@playwright/test";
import { addAccount, addTransaction, login, readRemaining } from "./helpers";

// Poll until the given merchant is no longer the visible review card (either the
// deck advanced to a different card or the queue emptied).
async function cardCleared(page: Page, merchant: string): Promise<void> {
  await expect
    .poll(
      async () => {
        if (await page.getByTestId("review-empty").isVisible().catch(() => false)) return true;
        const card = await page
          .getByTestId("swipe-card")
          .textContent()
          .catch(() => null);
        return card != null && !card.includes(merchant);
      },
      { timeout: 10_000 },
    )
    .toBe(true);
}

// Seed an uncategorized transaction so it lands in the needs_review queue, then
// return the review page with that card on top.
async function queueUncategorized(page: Page, merchant: string): Promise<void> {
  const run = Date.now();
  const accountName = `Review Acct ${run}`;
  await login(page);
  await addAccount(page, { name: accountName, balance: "100", currency: "USD" });
  await page.getByTestId("nav-transactions").click();
  await addTransaction(page, { accountName, amount: "-7.77", merchant }); // no category
  await page.getByTestId("nav-review").click();
  await expect(page.getByTestId("review-deck")).toBeVisible();
  await expect(page.getByTestId("swipe-card")).toContainText(merchant);
}

test("review: approving a card removes it from the queue", async ({ page }) => {
  const merchant = `Needs Review ${Date.now()}`;
  await queueUncategorized(page, merchant);

  const before = await readRemaining(page);
  expect(before).toBeGreaterThan(0);

  await page.getByTestId("review-approve").click();
  await cardCleared(page, merchant);
});

test("review: keyboard ArrowRight swipes the top card", async ({ page }) => {
  const merchant = `Keyboard Swipe ${Date.now()}`;
  await queueUncategorized(page, merchant);

  // Focus the document, then use the desktop keyboard review path.
  await page.getByTestId("review-deck").click();
  await page.keyboard.press("ArrowRight");
  await cardCleared(page, merchant);
});
