import { test, expect } from "@playwright/test";
import { addAccount, addTransaction, login, readNetWorth } from "./helpers";

// One end-to-end pass through the manual ledger. Data is uniquely named per run
// (Date.now suffix) and assertions are relative, so the suite is repeatable
// against a long-lived compose DB with no reset.
test("manual happy path: login -> account -> transaction -> insights", async ({ page }) => {
  const run = Date.now();
  await login(page);

  // --- Accounts: net worth is shown, adding an asset account raises it -------
  await expect(page.getByTestId("nav-accounts")).toBeVisible();
  const before = await readNetWorth(page);

  const accountName = `E2E Checking ${run}`;
  const balance = 4200;
  await addAccount(page, { name: accountName, balance: String(balance), currency: "USD" });

  await expect(page.getByTestId("account-list")).toContainText(accountName);
  // USD asset balance flows 1:1 into base-currency net worth.
  await expect
    .poll(async () => readNetWorth(page), { timeout: 10_000 })
    .toBeCloseTo(before + balance, 1);

  // --- Transactions: add a categorized Groceries expense --------------------
  await page.getByTestId("nav-transactions").click();
  const merchant = `Whole Foods ${run}`;
  await addTransaction(page, {
    accountName,
    amount: "-54.32",
    merchant,
    category: "Groceries",
  });
  await expect(page.getByTestId("txn-list")).toContainText(merchant);

  // --- Insights: net-worth section + spending donut render --------------------
  await page.getByTestId("nav-insights").click();
  await expect(page.getByTestId("report-net-worth")).toBeVisible();
  await expect(page.getByTestId("report-spending")).toBeVisible();
  // We just posted a Groceries expense this year, so the donut has data.
  await expect(page.getByTestId("spending-donut")).toBeVisible();
});
