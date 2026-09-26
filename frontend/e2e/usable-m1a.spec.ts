import { test, expect } from "@playwright/test";
import { addAccount, addOwner, addTransaction, login, openTxn, pickOption } from "./helpers";

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

  // Accounts are owned by default, and the row names the owner they got — as an
  // avatar whose accessible name is the owner, not as a line of text.
  await expect(
    page
      .getByTestId("account-list")
      .locator("li", { hasText: accountName })
      .locator('[data-testid^="account-owner-"]'),
  ).toHaveAccessibleName("Owner: Shared");

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
  await pickOption(page, "detail-owner", ownerName);
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
  // The picker shows the label of what is stored, not the id behind it, so the
  // owner's name in the control *is* the proof that the server sent it back.
  await expect(page.getByTestId("detail-owner")).toHaveValue(ownerName);
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

test("add, rename and delete a category in settings", async ({ page }) => {
  const run = Date.now();
  await login(page);
  await page.getByTestId("nav-settings").click();
  await expect(page.getByTestId("settings-panel-categories")).toBeVisible();

  const name = `Bikes ${run}`;
  // A second category to move things *to*. Both are created through the same
  // form, so both land in the same group — and a replacement has to share the
  // group's type, because a category's type is its group's and the reports read
  // it. Anything else would be a list of one.
  const spare = `Pumps ${run}`;
  // The colour is picked from the palette rather than typed into a raw colour
  // input: ten named swatches, each one a token read at runtime. Pink, so the
  // assertion below is about a colour that *was* picked — the default is the
  // first swatch, and a test that never moved it would pass on a picker that
  // ignored the click.
  await page.getByTestId("category-name").fill(name);
  await page.getByRole("radio", { name: "Pink" }).check();
  await expect(page.getByTestId("category-color-current")).toHaveText(/^Selected: Pink · /);
  const picked = await page
    .getByRole("radio", { name: "Pink" })
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  await page.getByTestId("category-save").click();
  await expect(page.getByTestId("category-list")).toContainText(name);
  // What was picked is what was stored: the dot beside the new category's name
  // is the colour of the swatch that was clicked, read back from the server
  // rather than from the form's own state. Compared against the swatch's own
  // computed style, so a palette change moves both and only a broken round trip
  // fails.
  // `.last()` because the rows are nested inside the group's own <li>, so the
  // group matches `hasText` too — the same scoping the rename below uses.
  await expect(
    page
      .locator('[data-testid="category-list"] li', { hasText: name })
      .last()
      .locator('[data-testid^="category-dot-"]'),
  ).toHaveCSS("background-color", picked);
  await page.getByTestId("category-name").fill(spare);
  await page.getByTestId("category-save").click();
  await expect(page.getByTestId("category-list")).toContainText(spare);

  // Rename it from its own row: Edit opens a dialog over the list, so the tab
  // you were on is still the tab you are on — no full-page context switch.
  const row = page.locator('[data-testid="category-list"] li', { hasText: name }).last();
  await row.getByRole("button", { name: "Edit" }).click();
  await page.getByRole("dialog").getByTestId(/^category-edit-name-/).fill(`${name} renamed`);
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("category-list")).toContainText(`${name} renamed`);

  // File something under it, so the delete has a count to state and something
  // to move. Reassigned to another category rather than left uncategorized:
  // an uncategorized transaction lands in the needs_review queue, and this spec
  // must not leave a backlog behind for review.spec.ts.
  const accountName = `Bikes Acct ${run}`;
  const merchant = `Bike pump ${run}`;
  await page.getByTestId("nav-accounts").click();
  await addAccount(page, { name: accountName, balance: "300", currency: "USD" });
  await page.getByTestId("nav-transactions").click();
  await addTransaction(page, {
    accountName,
    amount: "-24.00",
    merchant,
    category: `${name} renamed`,
  });

  // Delete it from the same dialog the rename happened in: the row carries one
  // action (§4.6), and the destructive one lives behind its own step (§4.12).
  await page.getByTestId("nav-settings").click();
  const renamed = page
    .locator('[data-testid="category-list"] li', { hasText: `${name} renamed` })
    .last();
  await renamed.getByRole("button", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  const dialogTestId = (await dialog.getAttribute("data-testid")) ?? "";
  const catId = dialogTestId.replace("edit-category-dialog-", "");
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();

  // How much is at stake, said before anything is destroyed — and the focus is
  // on the safe option, never on the red one.
  await expect(dialog.getByTestId(`category-usage-${catId}`)).toHaveText(
    "1 transaction is filed under it.",
  );
  await expect(dialog.getByTestId(`category-keep-${catId}`)).toBeFocused();

  // Default target: the sentinel. "Uncategorized" is the absence of a category
  // rather than a row, so the option is the empty value and reads as its own
  // thing — a household category really called "Uncategorized" is a different
  // option with a different value.
  await expect(dialog.getByTestId(`category-target-${catId}`)).toHaveValue(
    "No category (Uncategorized)",
  );

  // Move it to a real category instead, and the confirmation says which.
  await pickOption(dialog, `category-target-${catId}`, new RegExp(`${spare}$`));
  await dialog.getByTestId(`category-delete-confirm-btn-${catId}`).click();

  await expect(page.getByTestId("category-list")).not.toContainText(name);
  // The row is gone, so this line is what says where its entries went.
  const result = page.getByTestId("category-delete-result");
  await expect(result).toContainText(`Deleted “${name} renamed”`);
  await expect(result).toContainText("moved 1 transaction to");
  await expect(result).toContainText(spare);

  // No orphan: the transaction is still there and now reads the new category.
  await page.getByTestId("nav-transactions").click();
  await expect(page.getByTestId("txn-list").locator("li", { hasText: merchant })).toContainText(
    spare,
  );
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
