import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { addAccount, login } from "./helpers";

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.getByTestId("edit-account-dialog").evaluate((dialog) => {
    const width = document.documentElement.clientWidth;
    return [...dialog.querySelectorAll<HTMLElement>("*")].filter((element) => {
      const rect = element.getBoundingClientRect();
      const scrolls = /(auto|scroll)/.test(getComputedStyle(element).overflowX);
      return rect.width > 0 && (rect.left < -1 || rect.right > width + 1
        || (scrolls && element.scrollWidth > element.clientWidth + 1));
    }).map((element) => `${element.tagName}: ${element.textContent?.slice(0, 70)}`);
  });
  expect(overflow).toEqual([]);
}

// Each run owns its account, holding and files, and removes all of them in finally.
// The demo's existing balances, files and holdings are never modified.
for (const width of [360, 1280]) {
  test(`account holdings and attachments work at ${width}px`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 900 });
    await login(page);
    const session = await (await page.request.get("/api/auth/me")).json();
    const cleanupHeaders = { "X-CSRF-Token": session.csrf_token };
    const run = `${Date.now()}${width}`;
    const name = `Account management ${run}`;
    let accountId: string | undefined;
    let securityId: string | undefined;
    try {
      const [created] = await Promise.all([
        page.waitForResponse((response) => response.url().endsWith("/api/accounts")
          && response.request().method() === "POST"),
        addAccount(page, { name, balance: "0", type: "investment" }),
      ]);
      accountId = (await created.json()).id;
      await page.getByTestId("account-list").locator("li", { hasText: name })
        .locator('[data-testid^="account-edit-"]').click();
      const dialog = page.getByTestId("edit-account-dialog");
      const holdings = dialog.getByTestId("account-holdings-editor");
      await expect(holdings.getByText("No holdings yet.")).toBeVisible();
      await holdings.getByRole("button", { name: "Add holding", exact: true }).click();
      await holdings.getByRole("textbox", { name: "Name", exact: true }).fill(`Test investment ${run}`);
      await holdings.getByLabel("Symbol", { exact: true }).fill(`E${run}`);
      await holdings.getByLabel("Type", { exact: true }).selectOption("etf");
      await holdings.getByRole("spinbutton", { name: "Quantity", exact: true }).fill("2.5");
      await holdings.getByLabel("Market value · USD", { exact: true }).fill("250");
      await holdings.getByLabel("Total cost basis · USD", { exact: true }).fill("200");
      if (width === 360) await expectNoHorizontalOverflow(page);
      const [security] = await Promise.all([
        page.waitForResponse((response) => response.url().endsWith("/api/investments/securities")
          && response.request().method() === "POST"),
        holdings.getByRole("button", { name: "Save holding", exact: true }).click(),
      ]);
      securityId = (await security.json()).id;
      await expect(holdings.getByRole("button", { name: "Add holding", exact: true })).toBeVisible();
      await expect(holdings).toContainText("2.5 units");
      await expect(holdings).toContainText("$250.00");
      await holdings.getByRole("button", { name: "Edit", exact: true }).click();
      await holdings.getByRole("textbox", { name: "Name", exact: true }).fill(`Corrected investment ${run}`);
      await holdings.getByRole("spinbutton", { name: "Quantity", exact: true }).fill("3");
      await holdings.getByLabel("Market value · USD", { exact: true }).fill("360");
      await holdings.getByRole("button", { name: "Save holding", exact: true }).click();
      await expect(holdings).toContainText(`Corrected investment ${run}`);
      await expect(holdings).toContainText("3 units");
      await expect(holdings).toContainText("$360.00");
      await dialog.screenshot({ path: testInfo.outputPath("account-holdings.png") });

      const documents = dialog.getByRole("region", { name: "Account documents" });
      await expect(documents.getByText("No documents attached.")).toBeVisible();
      const pdfName = `statement-${run}-${"longfilename".repeat(10)}.pdf`;
      const pdfBytes = Buffer.from("%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n");
      await documents.getByLabel("Choose account document").setInputFiles({
        name: pdfName, mimeType: "application/pdf", buffer: pdfBytes,
      });
      const pdf = documents.locator("li", { hasText: pdfName });
      await expect(pdf).toBeVisible();
      const [download] = await Promise.all([
        page.waitForEvent("download"), pdf.getByRole("link", { name: "Download", exact: true }).click(),
      ]);
      expect(download.suggestedFilename()).toBe(pdfName);
      expect(readFileSync((await download.path())!)).toEqual(pdfBytes);
      const view = pdf.getByRole("link", { name: "View PDF", exact: true });
      const preview = await page.request.get((await view.getAttribute("href"))!);
      expect(preview.headers()["content-disposition"]).toMatch(/^inline;/);
      expect(preview.headers()["content-type"]).toBe("application/pdf");
      expect(await preview.body()).toEqual(pdfBytes);

      const officeName = `presentation-${run}.pptx`;
      const officeBytes = Buffer.from("PK\x03\x04user presentation bytes", "binary");
      await documents.getByLabel("Choose account document").setInputFiles({
        name: officeName,
        mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        buffer: officeBytes,
      });
      const office = documents.locator("li", { hasText: officeName });
      await expect(office).toBeVisible();
      await expect(office.getByRole("link", { name: "View PDF" })).toHaveCount(0);
      const [officeDownload] = await Promise.all([
        page.waitForEvent("download"), office.getByRole("link", { name: "Download", exact: true }).click(),
      ]);
      expect(officeDownload.suggestedFilename()).toBe(officeName);
      expect(readFileSync((await officeDownload.path())!)).toEqual(officeBytes);
      if (width === 360) await expectNoHorizontalOverflow(page);
      await dialog.screenshot({ path: testInfo.outputPath("account-documents.png") });
      for (const file of [pdf, office]) {
        await file.getByRole("button", { name: "Delete", exact: true }).click();
        await expect(file.getByText("Delete this document permanently?")).toBeVisible();
        await file.getByRole("button", { name: "Keep file", exact: true }).click();
        await expect(file).toBeVisible();
        await file.getByRole("button", { name: "Delete", exact: true }).click();
        await file.getByRole("button", { name: "Delete file", exact: true }).click();
        await expect(file).toHaveCount(0);
      }
      await expect(documents.getByText("No documents attached.")).toBeVisible();
      await dialog.getByTestId("edit-account-dialog-close").click();
      await page.getByTestId("account-list").locator("li", { hasText: name })
        .locator('[data-testid^="account-edit-"]').click();
      await expect(page.getByTestId("account-holdings-editor")).toContainText(`Corrected investment ${run}`);
      await expect(page.getByTestId("account-holdings-editor")).toContainText("$360.00");
    } finally {
      testInfo.setTimeout(testInfo.timeout + 30_000);
      const headers = cleanupHeaders;
      if (accountId) expect((await page.request.delete(`/api/accounts/${accountId}`, { headers })).ok()).toBeTruthy();
      if (securityId) expect((await page.request.delete(`/api/investments/securities/${securityId}`, { headers })).ok()).toBeTruthy();
    }
  });
}

test("incomplete-data notes are collapsed until requested", async ({ page }) => {
  await login(page);
  const note = "Test coverage note: an account is missing a rate.";
  await page.route("**/api/reports/net-worth?*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: { ...data, warnings: [note] } });
  });
  await page.goto("/insights/overview");
  const notes = page.getByTestId("reconciliation-warnings");
  await expect(notes).toBeVisible();
  await expect(notes).not.toHaveAttribute("open", "");
  await expect(notes.getByText(note, { exact: true })).toBeHidden();
  await notes.locator("summary").click();
  await expect(notes.getByText(note, { exact: true })).toBeVisible();
});

test("header search fits at 1024px", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await login(page);
  const header = page.getByRole("banner");
  const offenders = await header.evaluate((banner) => {
    const width = document.documentElement.clientWidth;
    return [...banner.querySelectorAll<HTMLElement>("*")].filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && (rect.left < -1 || rect.right > width + 1);
    }).map((element) => `${element.tagName}: ${element.textContent?.slice(0, 70)}`);
  });
  expect(offenders).toEqual([]);
  await header.screenshot({ path: testInfo.outputPath("header-1024-light.png") });
});

test("budget exceptions stay collapsed and readable at 360px", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 360, height: 900 });
  await login(page);
  const missingRate = "Some transactions are excluded because an exchange rate is missing.";
  const history = "A newly added account has incomplete history for this month.";
  // Synthetic report only: no budget or seeded account is changed.
  await page.route("**/api/budgets", async (route) => {
    await route.fulfill({ json: {
      base_currency: "USD", period_start: "2026-10-01", period_end: "2026-10-31",
      rows: [{ category_id: "00000000-0000-4000-8000-000000000001", category_name: "Groceries",
        category_icon: "🛒", budget: "250.0000", spent: "100.0000" }],
      total_budget: "250.0000", total_spent: "100.0000", budgeted_spent: "100.0000",
      unbudgeted_spent: "0.0000", attribution: "row", warnings: [missingRate, missingRate, history],
    } });
  });
  await page.goto("/insights/budgets");
  const notes = page.getByTestId("budgets-warning");
  await expect(notes).toBeVisible();
  await expect(notes).not.toHaveAttribute("open", "");
  // The count is of notes, not of distinct sentences: the repeat is shown as ×2.
  await expect(notes.locator("summary")).toContainText("3 notes");
  await expect(notes.getByText(missingRate)).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath("budgets-360-collapsed-light.png"), fullPage: true });
  await notes.locator("summary").click();
  await expect(notes.getByText(missingRate)).toBeVisible();
  await expect(notes.getByText("×2")).toBeVisible();
  await expect(notes.getByText(history, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth
    <= document.documentElement.clientWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("budgets-360-expanded-light.png"), fullPage: true });
});

test("recorded-trade holdings allow metadata edits at 360px", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 360, height: 1100 });
  await login(page);
  const accountId = "00000000-0000-4000-8000-000000000002";
  const securityId = "00000000-0000-4000-8000-000000000003";
  const accountName = "Recorded-trade form preview";
  await page.route("**/api/accounts", async (route) => {
    const response = await route.fetch();
    const accounts = await response.json();
    await route.fulfill({ response, json: [{ ...accounts[0], id: accountId, name: accountName,
      type: "investment", currency: "USD", balance_source: "stated", current_balance: "1000.0000" }] });
  });
  await page.route(`**/api/accounts/${accountId}/balances`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/accounts/${accountId}/documents`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/investments/holdings?account_id=${accountId}`, (route) => route.fulfill({
    json: [{ id: "00000000-0000-4000-8000-000000000004", account_id: accountId,
      security_id: securityId, security: { id: securityId, name: "History-backed investment",
        ticker: "HIST", security_type: "etf", currency: "USD", is_manual: true },
      quantity: "25.00000000", cost_basis: "750.0000", quantity_source: "history",
      basis_source: "history", manual_quantity: "20.00000000", manual_cost_basis: "600.0000",
      as_of: "2026-10-04", source: "manual", is_override: false }],
  }));
  await page.goto("/accounts");
  await page.getByTestId("account-list").locator("li", { hasText: accountName })
    .locator('[data-testid^="account-edit-"]').click();
  const holdings = page.getByTestId("account-holdings-editor");
  await holdings.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(holdings.getByRole("textbox", { name: "Name", exact: true })).toBeEnabled();
  await expect(holdings.getByLabel("Symbol", { exact: true })).toBeEnabled();
  await expect(holdings.getByLabel("Type", { exact: true })).toBeEnabled();
  await expect(holdings.getByLabel("Market value · USD", { exact: true })).toBeEnabled();
  await expect(holdings.getByRole("spinbutton", { name: "Quantity", exact: true })).toBeDisabled();
  await expect(holdings.getByLabel("Total cost basis · USD", { exact: true })).toBeDisabled();
  await expect(holdings.getByText("Quantity and cost basis come from recorded trades. You can edit the name, symbol, type and value here.")).toBeVisible();
  await holdings.locator("form").scrollIntoViewIfNeeded();
  await expectNoHorizontalOverflow(page);
  await page.getByTestId("edit-account-dialog").screenshot({ path: testInfo.outputPath("holdings-history-360-light.png") });
  // Inspect only: the synthetic position is never submitted or written to the database.
  await holdings.getByRole("button", { name: "Cancel", exact: true }).click();
});
