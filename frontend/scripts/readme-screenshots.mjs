// README screenshots, from the demo household on a running dev stack (see README "Screenshots").
// Usage (from frontend/): OUT=../docs/screenshots [CHROME=/path/to/chromium] node scripts/readme-screenshots.mjs
import { chromium } from "@playwright/test";
const out = process.env.OUT;
const base = "http://127.0.0.1:5173";
const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});

async function login(page) {
  await page.goto(`${base}/login`);
  await page.getByTestId("email").fill("owner@example.com");
  await page.getByTestId("password").fill("devpassword123");
  await page.getByTestId("login-submit").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

// Desktop.
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
  await login(page);
  await page.goto(`${base}/accounts`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/accounts.png` });
  await page.goto(`${base}/transactions`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/transactions.png` });
  await page.goto(`${base}/reports`);
  await page.waitForTimeout(2500);
  await page.getByTestId("report-cash-flow").screenshot({ path: `${out}/cash-flow.png` });
  await page.goto(`${base}/admin`);
  await page.waitForTimeout(1200);
  await page.getByText("Auto-categorize", { exact: true }).first().locator("xpath=ancestor::section[1]")
    .screenshot({ path: `${out}/auto-categorize.png` });
  await page.close();
}
// Phone.
{
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await login(page);
  await page.goto(`${base}/accounts`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/accounts-phone.png` });
  await page.goto(`${base}/review`);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/review-phone.png` });
  await page.getByTestId("review-category").click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/review-category-phone.png` });
  await page.keyboard.press("Escape");
  await page.goto(`${base}/accounts`);
  await page.waitForTimeout(1000);
  await page.locator('[data-testid^="account-edit-"]').nth(1).click();
  await page.waitForTimeout(800);
  await page.getByTestId("balance-history").scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${out}/balance-history-phone.png` });
  await page.close();
}
await browser.close();
