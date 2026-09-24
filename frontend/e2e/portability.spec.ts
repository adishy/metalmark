import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";
import { addAccount, addTransaction, login } from "./helpers";

// The round trip, end to end through the browser: download the household's
// export, hand the same file straight back to the importer, and expect it to
// recognize everything it just wrote.
//
// That second step is the whole point, and it is a *no-op* — which is what makes
// it safe to run against the shared demo database in CI. Anything it creates on
// a second import is a duplicate the duplicate-detection promised not to make,
// so the assertion is not "it worked" but "it created nothing". It is also the
// restore path a person is most likely to take: export before a change, and put
// it back if the change was wrong.

test("export and re-import the household: nothing is duplicated", async ({ page }) => {
  await login(page);
  await page.getByTestId("nav-settings").click();
  await page.getByTestId("settings-tab-data").click();
  await expect(page.getByTestId("settings-panel-data")).toBeVisible();

  // The export route is a GET with a Content-Disposition; Playwright reports it
  // as a download because the app hands the response to the browser as a blob
  // rather than rendering it into the tab.
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("export-download").click(),
  ]);
  // The server names the file, dated — a bare default name would mean the header
  // was lost somewhere between FastAPI and here.
  expect(download.suggestedFilename()).toMatch(/^metalmark-export-\d{4}-\d{2}-\d{2}\.json$/);
  const path = await download.path();
  expect(path, "Playwright must hand back a path to the saved file").toBeTruthy();

  await page.getByTestId("import-document-file").setInputFiles(path!);
  await page.getByTestId("import-document-submit").click();

  // Everything in the document is already in this household, so the import says
  // so. A created count here is a duplicate; a warning here is a reference the
  // reader could not resolve in the household it came from.
  await expect(page.getByTestId("import-result")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("import-nothing-new")).toBeVisible();
  await expect(page.getByTestId("import-warnings")).toHaveCount(0);
  // And it did more than look at it: the matched list is the proof that rows
  // were recognised rather than skipped wholesale.
  await expect(page.getByTestId("import-matched")).toContainText("transactions:");
});

test("an account exports a CSV the importer can read back", async ({ page }) => {
  // Its own account with its own row, rather than whichever account the demo
  // ledger lists first: this test is about the *file*, and an account that
  // happens to be empty would make it pass or fail on the seed's shape instead.
  const run = Date.now();
  const accountName = `CSV Acct ${run}`;
  const merchant = `CSV Merchant ${run}`;
  await login(page);
  await addAccount(page, { name: accountName, balance: "500" });
  await page.getByTestId("nav-transactions").click();
  // Categorised on purpose, so this leaves nothing in the review queue behind
  // it — review.spec.ts drains that queue and would find this row first.
  await addTransaction(page, { accountName, amount: "-12.34", merchant, category: "Restaurants" });

  // The per-account export lives on the account itself, which is where someone
  // asks for it — not in a list of everybody's settings.
  await page.getByTestId("nav-accounts").click();
  await page.getByTestId("account-list").locator("li", { hasText: accountName })
    .locator('[data-testid^="account-edit-"]').click();

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("account-export-csv").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^metalmark-transactions-\d{4}-\d{2}-\d{2}\.csv$/);

  const path = await download.path();
  expect(path).toBeTruthy();
  // Read as text with the BOM stripped: the file carries one so a spreadsheet
  // opens it as UTF-8 rather than as the local codepage, and the first header
  // would otherwise arrive with an invisible character glued to it.
  const csv = readFileSync(path!, "utf8").replace(/^﻿/, "");
  const [header, ...rows] = csv.trim().split(/\r?\n/);
  // The six columns the CSV importer maps without a human: this file is meant to
  // be read back in, not merely opened.
  expect(header).toBe("date,amount,description,category,owner,notes");
  expect(rows).toHaveLength(1);
  expect(rows[0]).toContain(merchant);
  // Plain signed decimal, and the category by name — the two things the importer
  // resolves rather than reads.
  expect(rows[0]).toContain("-12.34");
  expect(rows[0]).toContain("Restaurants");
});
