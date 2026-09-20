import { test, expect } from "@playwright/test";
import { addAccount, addOwner, login } from "./helpers";

// Reports owns three ECharts surfaces — the net-worth series, the income-vs-expense
// trend and the spending donut — and has no component test, so this spec is the
// only place that proves they mount at all (vitest renders none of them; jsdom has
// no canvas) and that the owner filter genuinely re-scopes them.
//
// The trend chart in particular is what `/reports/cash-flow` draws: one stacked bar
// per month with a net line over it. Asserting the canvas exists is the honest
// limit of what a headless browser can check cheaply — the numbers behind it are
// covered by the backend's report tests.

test("reports render their charts and re-scope to one owner", async ({ page }) => {
  const run = Date.now();
  await login(page);

  // This owner needs an account of its own: with no filter every chart would render
  // from the whole household's data, and filtering to an owner who owns nothing
  // would prove only that an empty chart renders.
  const ownerName = `Report Owner ${run}`;
  await addOwner(page, ownerName);
  await page.getByTestId("nav-accounts").click();
  await addAccount(page, {
    name: `Report Acct ${run}`,
    balance: "250",
    currency: "USD",
    owner: ownerName,
  });

  await page.getByTestId("nav-reports").click();
  await expect(page.getByTestId("report-net-worth")).toBeVisible();

  // Unfiltered: all three surfaces draw, and nothing claims an attribution.
  await expect(page.getByTestId("net-worth-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("spending-donut").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toBeHidden();

  // Filtered to one owner: still draws, and now says which scoping it used. Net
  // worth is account-scoped while spending is row-scoped, so leaving that unsaid
  // would be the UI implying the two views add up. They do not.
  await page.getByTestId("owner-filter").getByRole("button", { name: ownerName, exact: true }).click();
  await expect(page.getByTestId("net-worth-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toContainText("account");

  // Back to the whole household: the caveat goes away with the filter.
  await page.getByTestId("owner-filter-all").click();
  await expect(page.getByTestId("net-worth-attribution")).toBeHidden();
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();
});
