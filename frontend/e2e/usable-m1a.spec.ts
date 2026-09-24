import { test, expect } from "@playwright/test";
import { addAccount, addOwner, addTransaction, login, openTxn } from "./helpers";

// Covers the "usable M1a" flows layered on top of the thin ledger: editing a
// transaction (incl. setting an owner), splitting it, managing categories in
// Settings, and adding an FX rate. Data is uniquely named per run so the suite
// is repeatable against a long-lived compose DB.

test("edit a transaction and set an owner", async ({ page }) => {
  const run = Date.now();
  const accountName = `Edit Acct ${run}`;
  const ownerName = `Owner ${run}`;
  await login(page);
  await addOwner(page, ownerName);
  await page.getByTestId("nav-accounts").click();
  await addAccount(page, { name: accountName, balance: "500", currency: "USD" });

  // Accounts are owned by default, and the row names the owner they got.
  await expect(
    page.getByTestId("account-list").locator("li", { hasText: accountName }),
  ).toContainText(/Shared/);

  await page.getByTestId("nav-transactions").click();
  const merchant = `Coffee ${run}`;
  // Categorised on purpose: an uncategorised txn lands in the needs_review
  // queue, and this suite must not leave a backlog behind for review.spec.ts.
  await addTransaction(page, { accountName, amount: "-9.99", merchant, category: "Restaurants" });
  await expect(page.getByTestId("txn-list")).toContainText(merchant);

  // Scoped to this run's row: the ledger is shared, so a bare "first owner
  // badge" could belong to a transaction an earlier run left behind.
  const rowBadge = page
    .getByTestId("txn-list")
    .locator("li", { hasText: merchant })
    .locator('[data-testid^="txn-owner-"]');

  // Nothing was set on this transaction, so it shows the owner it inherits,
  // marked as inherited.
  await expect(rowBadge).toHaveAttribute("title", /Inherited from/);

  await openTxn(page, merchant);
  const edited = `${merchant} EDITED`;
  await page.getByTestId("detail-merchant").fill(edited);
  await page.getByTestId("detail-owner").selectOption({ label: ownerName });
  await page.getByTestId("txn-detail-save").click();
  await expect(page.getByTestId("txn-detail")).toBeHidden();

  // The row reflects the new merchant and the owner now set on the transaction
  // itself rather than inherited.
  await expect(page.getByTestId("txn-list")).toContainText(edited);
  await expect(rowBadge).toContainText(ownerName);
  await expect(rowBadge).toHaveAttribute("title", "Owner set on this transaction");

  // Owner persisted on the server: reopening shows it selected, and the ledger
  // filter for that owner still finds the transaction.
  await openTxn(page, edited);
  await expect(page.getByTestId("detail-owner")).not.toHaveValue("");
  await expect(page.getByTestId("detail-owner").locator("option:checked")).toHaveText(ownerName);
  await page.getByTestId("txn-detail-close").click();

  await page.getByTestId("owner-filter").getByRole("button", { name: ownerName, exact: true }).click();
  await expect(page.getByTestId("txn-list")).toContainText(edited);
  await page.getByTestId("owner-filter-all").click();
  await expect(page.getByTestId("owner-filter-all")).toHaveAttribute("aria-pressed", "true");
});

test("split a transaction by amount", async ({ page }) => {
  const run = Date.now();
  const accountName = `Split Acct ${run}`;
  await login(page);
  await addAccount(page, { name: accountName, balance: "1000", currency: "USD" });

  await page.getByTestId("nav-transactions").click();
  const merchant = `Groceries Split ${run}`;
  await addTransaction(page, { accountName, amount: "-120.00", merchant, category: "Groceries" });

  await openTxn(page, merchant);
  await page.getByTestId("split-open").click();
  await page.getByTestId("split-amount-0").fill("-40.00");
  await page.getByTestId("split-amount-1").fill("-80.00");
  // Sum (-120) equals the txn amount, so save becomes enabled.
  await expect(page.getByTestId("split-save")).toBeEnabled();
  await page.getByTestId("split-save").click();

  // After saving, the parent is flagged as a split and an un-split action exists.
  await expect(page.getByTestId("split-unsplit")).toBeVisible();
  await page.getByTestId("txn-detail-close").click();
  await expect(page.getByTestId("txn-list").getByText(merchant).first()).toBeVisible();
  await expect(page.getByTestId("txn-list")).toContainText("split");
});

test("add and delete a category in settings", async ({ page }) => {
  const run = Date.now();
  await login(page);
  await page.getByTestId("nav-settings").click();
  await expect(page.getByTestId("settings-panel-categories")).toBeVisible();

  const name = `Bikes ${run}`;
  await page.getByTestId("category-name").fill(name);
  await page.getByTestId("category-save").click();
  await expect(page.getByTestId("category-list")).toContainText(name);

  // Delete the freshly-added category via its row's Delete button.
  const row = page.locator('[data-testid="category-list"] li', { hasText: name }).last();
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByTestId("category-list")).not.toContainText(name);
});

test("add an FX rate in settings", async ({ page }) => {
  await login(page);
  await page.getByTestId("nav-settings").click();
  await page.getByTestId("settings-tab-currencies").click();
  await expect(page.getByTestId("settings-panel-currencies")).toBeVisible();

  await page.getByTestId("fx-base").fill("USD");
  await page.getByTestId("fx-quote").fill("EUR");
  await page.getByTestId("fx-rate").fill("0.9250");
  await page.getByTestId("fx-save").click();

  await expect(page.getByTestId("fx-list")).toContainText("EUR");
});
