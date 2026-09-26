import { expect, type Locator, type Page } from "@playwright/test";

export const OWNER_EMAIL = process.env.E2E_EMAIL ?? "owner@example.com";
export const OWNER_PASSWORD = process.env.E2E_PASSWORD ?? "devpassword123";

/**
 * Parse a formatted-money string (e.g. "−$1,234.50") into a number.
 *
 * The minus is U+2212, not the hyphen-minus (§6.2), so `/[-−]/` — matching only
 * the ASCII hyphen silently reads every negative as positive, which turns a
 * wrong net worth into a passing test.
 */
export function parseMoney(text: string): number {
  const neg = /[-−]/.test(text);
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

/**
 * Choose an option from a picker, by the text it reads as.
 *
 * The record pickers (`Combobox`) are not `<select>`s, so `selectOption` does
 * not apply to them — the list exists only while the control is open and its
 * rows are elements to click. A `Combobox` is also a combobox above `sm` and the
 * platform `<select>` below it, which is the control a phone should get (§4.4),
 * and specs run at both widths, so this asks the DOM which of the two it got
 * rather than assuming. Either way the option is chosen the same way: by what it
 * says.
 */
export async function pickOption(
  scope: Page | Locator,
  testid: string,
  name: string | RegExp,
): Promise<void> {
  const control = scope.getByTestId(testid);
  if ((await control.evaluate((el) => el.tagName)) === "SELECT") {
    const value = await control
      .locator("option")
      .filter({ hasText: name })
      .first()
      .getAttribute("value");
    await control.selectOption(value ?? "");
    return;
  }
  await control.click();
  // Scoped to the control's own listbox, found through the `aria-controls` the
  // combobox points at: a native `<option>` is an element with role `option` too
  // (the add-transaction form carries both kinds of control), so an unscoped
  // `getByRole("option")` can match an option that is not on screen at all. The
  // id is looked up from the page because `scope` is a form as often as it is a
  // page, and only a `Locator` knows its own page.
  const listId = await control.getAttribute("aria-controls");
  if (!listId) throw new Error(`${testid} did not open a listbox on click`);
  const onPage = typeof (scope as Locator).page === "function" ? (scope as Locator).page() : (scope as Page);
  await onPage.locator(`[id="${listId}"]`).getByRole("option", { name }).first().click();
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
    await pickOption(form, "account-owner", opts.owner);
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
  await pickOption(form, "txn-account", opts.accountName);
  await form.getByTestId("txn-merchant").fill(opts.merchant);
  await form.getByTestId("txn-amount").fill(opts.amount);
  if (opts.category) {
    // Options read "🛒 Groceries": match the name at the end, not the whole label.
    const escaped = opts.category.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    await pickOption(form, "txn-category", new RegExp(`(^|\\s)${escaped}$`));
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
