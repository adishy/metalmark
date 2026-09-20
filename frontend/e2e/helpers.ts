import { expect, type Page } from "@playwright/test";

export const OWNER_EMAIL = process.env.E2E_EMAIL ?? "owner@example.com";
export const OWNER_PASSWORD = process.env.E2E_PASSWORD ?? "devpassword123";

/** Parse a formatted-money string (e.g. "-$1,234.50") into a number. */
export function parseMoney(text: string): number {
  const neg = /-/.test(text);
  const digits = text.replace(/[^0-9.]/g, "");
  const value = Number(digits || "0");
  return neg ? -value : value;
}

/** Log in and land on the authenticated shell (Accounts). */
export async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await expect(page.getByTestId("login-form")).toBeVisible();
  await page.getByTestId("email").fill(OWNER_EMAIL);
  await page.getByTestId("password").fill(OWNER_PASSWORD);
  await page.getByTestId("login-submit").click();
  // The app shell nav only exists once authenticated. There are two of them —
  // a desktop tab bar above 640px and a bottom tab bar below — and both are
  // always in the DOM, each hidden by CSS at the other width. So assert on
  // whichever this viewport actually shows: `nav-accounts` is `hidden sm:flex`
  // and can never be visible at phone width. The `:visible` filter narrows the
  // pair to exactly one, which is also what keeps strict mode happy.
  await expect(
    page.locator('[data-testid="nav-accounts"]:visible, [data-testid="tab-accounts"]:visible'),
  ).toBeVisible();
}

/** Read the big net-worth figure from the Accounts header as a number. */
export async function readNetWorth(page: Page): Promise<number> {
  const value = page.getByTestId("net-worth").locator("p").nth(1);
  await expect(value).not.toHaveText("—");
  return parseMoney((await value.textContent()) ?? "0");
}

/** Create an owner row (Settings -> Owners); returns the name used. */
export async function addOwner(page: Page, name: string): Promise<void> {
  await page.getByTestId("nav-settings").click();
  await page.getByTestId("settings-tab-owners").click();
  await expect(page.getByTestId("settings-panel-owners")).toBeVisible();
  await page.getByTestId("owner-name").fill(name);
  await page.getByTestId("owner-save").click();
  // The row renders the name in an always-editable inline-rename ``<input>``, so the
  // name is a *value*, not text: ``toContainText`` reads only text nodes and can never
  // match it, however long it waits. The field is found by its accessible name (which
  // carries the owner's name) and asserted with ``toHaveValue``, which is what reads a
  // value. Note ``getByDisplayValue`` is a *page* method — ``Locator`` does not have it.
  await expect(page.getByLabel(`Name of ${name}`)).toHaveValue(name);
}

/** Create a manual account; returns the unique name used. */
export async function addAccount(
  page: Page,
  opts: { name: string; balance: string; currency?: string; type?: string; owner?: string },
): Promise<void> {
  await page.getByTestId("add-account").click();
  const form = page.getByTestId("add-account-form");
  await expect(form).toBeVisible();
  await form.getByTestId("account-name").fill(opts.name);
  await form.getByTestId("account-type").selectOption(opts.type ?? "depository");
  await form.getByTestId("account-currency").fill(opts.currency ?? "USD");
  await form.getByTestId("account-balance").fill(opts.balance);
  // Left alone, the picker is unset and the account lands on Shared.
  if (opts.owner) {
    await form.getByTestId("account-owner").selectOption({ label: opts.owner });
  }
  await form.getByTestId("account-save").click();
  await expect(form).toBeHidden();
}

/** Create a manual transaction. Leave `category` undefined for needs_review. */
export async function addTransaction(
  page: Page,
  opts: { accountName: string; amount: string; merchant: string; category?: string },
): Promise<void> {
  await page.getByTestId("add-transaction").click();
  const form = page.getByTestId("add-txn-form");
  await expect(form).toBeVisible();
  await form.getByTestId("txn-account").selectOption({ label: opts.accountName });
  await form.getByTestId("txn-merchant").fill(opts.merchant);
  await form.getByTestId("txn-amount").fill(opts.amount);
  if (opts.category) {
    await form.getByTestId("txn-category").selectOption({ label: opts.category });
  }
  await form.getByTestId("txn-save").click();
  await expect(form).toBeHidden();
}

/** Open the detail sheet for the transaction whose row shows `merchant`. */
export async function openTxn(page: Page, merchant: string): Promise<void> {
  await page.getByTestId("txn-list").getByText(merchant).first().click();
  await expect(page.getByTestId("txn-detail")).toBeVisible();
}

/** Advance the deck (approving) until `merchant` is the card on top.
 *
 * The review queue is shared and ordered by date, so any backlog — from an
 * earlier run, another spec, or a previous session — can sit ahead of the card a
 * test just created. Draining it is what a user would do; returning early keeps
 * the spec about its own card. */
export async function reviewTarget(page: Page, merchant: string, maxApprovals = 40): Promise<void> {
  for (let i = 0; i <= maxApprovals; i++) {
    if (await page.getByTestId("review-empty").isVisible().catch(() => false)) {
      throw new Error(`review queue emptied before "${merchant}" was reached`);
    }
    const card = page.getByTestId("swipe-card");
    const text = (await card.textContent().catch(() => null)) ?? "";
    if (text.includes(merchant)) return;
    await page.getByTestId("review-approve").click();
    // The deck advances optimistically; wait for the card to actually swap.
    await expect(card).not.toHaveText(text, { timeout: 5_000 });
  }
  throw new Error(`"${merchant}" never reached the top of the deck`);
}

/** Number of items in the review queue ("N to review" / "All done"). */
export async function readRemaining(page: Page): Promise<number> {
  const text = (await page.getByTestId("review-remaining").textContent()) ?? "";
  const m = text.match(/\d+/);
  return m ? Number(m[0]) : 0;
}
