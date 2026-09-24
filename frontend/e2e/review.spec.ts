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
//
// `narrow` picks the phone's tab bar over the header nav. Below `sm` the header
// nav is `hidden`, so the `nav-*` links are in the DOM and unclickable — a spec
// that uses them at a phone width times out on "element is not visible" with
// nothing on screen to explain it.
async function queueUncategorized(page: Page, merchant: string, narrow = false): Promise<void> {
  const run = Date.now();
  const accountName = `Review Acct ${run}`;
  const nav = narrow ? "tab" : "nav";
  await login(page);
  await addAccount(page, { name: accountName, balance: "100", currency: "USD" });
  await page.getByTestId(`${nav}-transactions`).click();
  await addTransaction(page, { accountName, amount: "-7.77", merchant }); // no category
  await page.getByTestId(`${nav}-review`).click();
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

test("review: pulling the card up opens it, and does not decide it (§4.17)", async ({ page }) => {
  const merchant = `Deck Lift ${Date.now()}`;
  await queueUncategorized(page, merchant);

  const box = (await page.getByTestId("swipe-card").boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  // `steps`, so the pointer travels rather than jumping: framer-motion only
  // starts a drag after a few pixels of movement, and a single teleporting
  // move is a pointerdown and a pointerup as far as it can tell.
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx, cy - 120, { steps: 12 });
  await page.mouse.up();

  await expect(page.getByTestId("txn-detail")).toBeVisible();
  await page.getByTestId("txn-detail-close").click();

  // Up is "open", not a third answer to the question the card asks: the card is
  // still on top, still undecided, and can be decided normally afterwards.
  await expect(page.getByTestId("swipe-card")).toContainText(merchant);
  await page.getByTestId("review-approve").click();
  await cardCleared(page, merchant);
});

test("review: editing a card from the deck files nothing (§4.17)", async ({ page }) => {
  const merchant = `Deck Edit ${Date.now()}`;
  await queueUncategorized(page, merchant);

  await page.getByTestId("review-edit").click();
  await expect(page.getByTestId("txn-detail")).toBeVisible();

  // Categorize it — the whole reason to open a card from the deck. The label is
  // read off the option rather than hard-coded: the demo ledger's categories are
  // seed data, and a spec that names one is a spec that breaks when the seed
  // changes for reasons that have nothing to do with this page.
  const select = page.getByTestId("detail-category");
  const label = await select.locator("option").nth(1).textContent();
  await select.selectOption({ index: 1 });
  await page.getByTestId("txn-detail-save").click();
  await expect(page.getByTestId("txn-detail")).toHaveCount(0);

  // Saving is not deciding. The card is where it was, it still asks the same
  // question, and it now answers the one the sheet was opened to change.
  const card = page.getByTestId("swipe-card");
  await expect(card).toContainText(merchant);
  await expect(card).toContainText(label!);
});

test("review: the keyboard does not decide the card under the open sheet", async ({ page }) => {
  const merchant = `Deck Modal Keys ${Date.now()}`;
  await queueUncategorized(page, merchant);

  await page.getByTestId("review-edit").click();
  await expect(page.getByTestId("txn-detail")).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await page.getByTestId("txn-detail-close").click();
  await expect(page.getByTestId("txn-detail")).toHaveCount(0);

  // Two assertions, and both are needed. The card surviving says the arrows went
  // to the form rather than to the deck behind it; deciding it now says the deck
  // was only paused for the sheet, not broken by it.
  await expect(page.getByTestId("swipe-card")).toContainText(merchant);
  await page.keyboard.press("ArrowRight");
  await cardCleared(page, merchant);
});

test.describe("the detail opens over the deck (§9.3)", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("at lg: it is a slide-over, because Review has no second column", async ({ page }) => {
    const merchant = `Deck Pane ${Date.now()}`;
    await queueUncategorized(page, merchant);

    await page.getByTestId("review-edit").click();
    const detail = page.getByTestId("txn-detail");
    await expect(detail).toBeVisible();
    // A pane is `role=region` and sits in the page beside its list; the overlay
    // is `role=dialog` and covers it. §9.3 gives Review one centred column, so
    // there is no second column for a pane to be in — and at 1280 the width
    // alone would have chosen the pane.
    await expect(detail).toHaveAttribute("role", "dialog");
  });
});

// `drag` puts `touch-action: none` on the card, so a touch that starts on the
// card can never scroll the page. On a phone that would be a trap — the card is
// the biggest thing on the screen and the reviewer's finger is aimed at it.
//
// It is not a trap, and these are the two numbers that say so. Both are
// measured rather than chosen: the deck is `h-72` and 129 px of shell sits above
// it, which a taller deck, a full-height one, or a fatter header would take
// away. Neither is a claim about the *drag* — that is the gesture test above.
test.describe("the deck leaves the page usable (§4.17)", () => {
  test("at the §8 floor the queue fits the screen", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    const merchant = `Deck Small ${Date.now()}`;
    await queueUncategorized(page, merchant, true);

    // The card *and* the two buttons that file it, above the fold: on the phone
    // the buttons are the primary interaction, and a review screen that has to
    // be scrolled to reach them is one the user is fighting the deck on.
    const bottom = await page.evaluate(() => {
      const rect = (sel: string) => document.querySelector(sel)!.getBoundingClientRect().bottom;
      return {
        card: Math.round(rect("[data-testid=swipe-card]")),
        buttons: Math.round(rect("[data-testid=review-approve]")),
      };
    });
    // 568 tall is the smallest phone §8 rule 9 supports; the deck ends at 417
    // and the buttons at 485, so what this is really pinning is the ~83 px of
    // slack between the buttons and the bottom edge.
    expect(bottom.card, "the card runs off the bottom of the smallest phone").toBeLessThan(568);
    expect(bottom.buttons, "the buttons are below the fold on the smallest phone").toBeLessThan(
      568,
    );
  });

  test("in landscape there is page above the card to start a scroll from", async ({ page }) => {
    await page.setViewportSize({ width: 667, height: 375 });
    const merchant = `Deck Wide ${Date.now()}`;
    await queueUncategorized(page, merchant);

    // From the top, deliberately. A scroll carried over from the previous page
    // would otherwise decide this measurement, and the number under test is
    // where the deck sits on a page that has not been scrolled.
    await page.evaluate(() => window.scrollTo(0, 0));
    const top = await page
      .getByTestId("review-deck")
      .evaluate((el) => Math.round(el.getBoundingClientRect().top));

    // At 375 tall the deck's 288 px plus the buttons do not fit, so the page
    // does scroll and has to be scrollable from somewhere the card is not. 129
    // px is the answer — and 44 is `min-h-11`, the smallest thing worth aiming
    // a fingertip at. A deck that grew to the height of the window would put
    // this at 0 and leave the landscape phone unable to scroll at all.
    expect(top, "the deck covers the top of the page, so a touch cannot scroll it").toBeGreaterThan(
      44,
    );
  });
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

test("review: the category is changed from the deck, and the card keeps its place", async ({ page }) => {
  const merchant = `Pick Category ${Date.now()}`;
  await queueUncategorized(page, merchant);

  await page.getByTestId("review-category").click();
  const picker = page.getByTestId("review-category-picker");
  await expect(picker).toBeVisible();
  await picker.getByTestId("review-category-picker-search").fill("Groceries");
  await picker.getByRole("button", { name: /Groceries/ }).first().click();

  await expect(page.getByTestId("review-category")).toContainText("Groceries");
  // Filing it is not deciding it: the same card is still asking.
  await expect(page.getByTestId("swipe-card")).toContainText(merchant);
});
