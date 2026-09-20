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
  // The app shell nav only exists once authenticated.
  await expect(page.getByTestId("nav-accounts")).toBeVisible();
}

/** Read the big net-worth figure from the Accounts header as a number. */
export async function readNetWorth(page: Page): Promise<number> {
  const value = page.getByTestId("net-worth").locator("p").nth(1);
  await expect(value).not.toHaveText("—");
  return parseMoney((await value.textContent()) ?? "0");
}

/** Create a manual account; returns the unique name used. */
export async function addAccount(
  page: Page,
  opts: { name: string; balance: string; currency?: string; type?: string },
): Promise<void> {
  await page.getByTestId("add-account").click();
  const form = page.getByTestId("add-account-form");
  await expect(form).toBeVisible();
  await form.getByTestId("account-name").fill(opts.name);
  await form.getByTestId("account-type").selectOption(opts.type ?? "depository");
  await form.getByTestId("account-currency").fill(opts.currency ?? "USD");
  await form.getByTestId("account-balance").fill(opts.balance);
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

/** Number of items in the review queue ("N to review" / "All done"). */
export async function readRemaining(page: Page): Promise<number> {
  const text = (await page.getByTestId("review-remaining").textContent()) ?? "";
  const m = text.match(/\d+/);
  return m ? Number(m[0]) : 0;
}
