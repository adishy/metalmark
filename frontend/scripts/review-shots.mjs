// Every page in both themes at two widths, for a design review. Not part of CI.
import { chromium } from "@playwright/test";
const out = process.env.OUT;
const base = "http://127.0.0.1:5173";
const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const pages = ["accounts", "transactions", "review", "reports", "settings", "admin"];
for (const scheme of ["light", "dark"]) {
  for (const [w, h, tag] of [[390, 844, "phone"], [1280, 900, "desk"]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
    const page = await ctx.newPage();
    await page.goto(`${base}/login`);
    await page.waitForTimeout(400);
    if (tag === "desk") await page.screenshot({ path: `${out}/${scheme}-${tag}-login.png` });
    await page.getByTestId("email").fill("owner@example.com");
    await page.getByTestId("password").fill("devpassword123");
    await page.getByTestId("login-submit").click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    for (const p of pages) {
      await page.goto(`${base}/${p}`);
      await page.waitForTimeout(1500);
      await page.screenshot({ path: `${out}/${scheme}-${tag}-${p}.png`, fullPage: tag === "desk" });
    }
    await ctx.close();
  }
}
await browser.close();
