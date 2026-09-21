import { test, expect, type Page } from "@playwright/test";
import { addAccount, addTransaction, login, readRemaining, reviewTarget } from "./helpers";

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
  await reviewTarget(page, merchant);
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

test("review: the card sits on a stack (§4.17)", async ({ page }) => {
  const merchant = `Deck Stack ${Date.now()}`;
  await queueUncategorized(page, merchant);

  // Measured, not counted. "There are two more divs" is satisfied by two divs
  // sitting exactly under the card, which is what the page had before this was
  // built — the claim is that their *edges* show, and that is a geometry test.
  const stack = await page.getByTestId("review-deck").evaluate((deck) => {
    const live = deck.querySelector("[data-testid=swipe-card]")!.getBoundingClientRect();
    return {
      live: Math.round(live.width),
      behind: Array.from(deck.querySelectorAll(":scope > [aria-hidden=true]")).map((el) => {
        const b = el.getBoundingClientRect();
        // Both positive going *in* and *down* from the live card: an inset card
        // is narrower on both sides, so its left edge is to the right of the
        // live card's, and it ends below it.
        return {
          inset: Math.round(b.left - live.left),
          below: Math.round(b.bottom - live.bottom),
          width: Math.round(b.width),
        };
      }),
    };
  });

  expect(stack.behind.length, "a deck is the live card plus the cards behind it").toBe(2);
  // Sorted rather than indexed: which of the two decorative divs comes first in
  // the DOM is not the contract, the ladder is.
  const [near, far] = stack.behind.slice().sort((a, b) => a.inset - b.inset);
  expect(near.inset, "the near card is not inset under the live one").toBeGreaterThan(0);
  expect(far.inset, "the two cards behind line up as one").toBeGreaterThan(near.inset);
  expect(near.below, "the near card's bottom edge is hidden").toBeGreaterThan(0);
  expect(far.below, "the two cards behind end at the same place").toBeGreaterThan(near.below);
  // A card that collapsed to a sliver would pass everything above — the offsets
  // are the whole of what is visible of it, so they are all there is to check.
  for (const card of [near, far]) {
    expect(card.width, "a card behind is too narrow to read as a card").toBeGreaterThan(
      0.8 * stack.live,
    );
  }
});

test("review: a decided card is thrown, not cut (§4.17)", async ({ page }) => {
  const merchant = `Deck Throw ${Date.now()}`;
  await queueUncategorized(page, merchant);

  // Sampled every frame from inside the page. "The card moves" cannot be read
  // off the DOM after the fact — the card is gone by then — and timing the
  // advance would be asserting that CI is fast, which is not the claim.
  await page.evaluate(() => {
    const w = window as unknown as { __x: number[] };
    w.__x = [];
    const tick = () => {
      const el = document.querySelector("[data-testid=swipe-card]");
      if (el) {
        const m = getComputedStyle(el).transform;
        if (m.startsWith("matrix")) w.__x.push(Math.abs(parseFloat(m.slice(7).split(",")[4])));
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  await page.getByTestId("review-approve").click();
  await cardCleared(page, merchant);

  const xs = await page.evaluate(() => (window as unknown as { __x: number[] }).__x);
  // The threshold is a fifth of the way out: far enough that a card merely
  // nudged by a drag would not reach it, close enough that a starved frame
  // clock still sees it. The last assertion is the other half — that it was on
  // its way *out*, rather than wobbling around the middle.
  expect(Math.max(...xs, 0), "the card never left the centre").toBeGreaterThan(84);
});

test.describe("reduced motion (§2.8)", () => {
  test.use({ reducedMotion: "reduce" });

  test("the deck advances without throwing the card", async ({ page }) => {
    const merchant = `Reduced ${Date.now()}`;
    await queueUncategorized(page, merchant);

    await page.evaluate(() => {
      const w = window as unknown as { __moved: string[] };
      w.__moved = [];
      const tick = () => {
        const el = document.querySelector("[data-testid=swipe-card]");
        if (el) {
          const m = getComputedStyle(el).transform;
          if (m !== "none" && m !== "matrix(1, 0, 0, 1, 0, 0)") w.__moved.push(m);
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    await page.getByTestId("review-approve").click();
    // Still has to advance — reduced motion removes the throw, not the decision.
    await cardCleared(page, merchant);

    expect(await page.evaluate(() => (window as unknown as { __moved: string[] }).__moved)).toEqual(
      [],
    );
  });
});
