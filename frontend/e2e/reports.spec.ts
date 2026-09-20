import { test, expect } from "@playwright/test";
import { addAccount, addOwner, login, parseMoney } from "./helpers";

// Reports owns four ECharts surfaces — the net-worth series, the income-vs-expense
// trend, the cash-flow Sankey and the spending donut — and has no component test, so
// this spec is the only place that proves they mount at all (vitest renders none of
// them; jsdom has no canvas) and that the owner filter genuinely re-scopes them.
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

  // Unfiltered: all four surfaces draw, and nothing claims an attribution — with no
  // filter there is no scoping to describe.
  await expect(page.getByTestId("net-worth-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("cash-flow-sankey").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("spending-donut").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toBeHidden();
  await expect(page.getByTestId("cash-flow-attribution")).toBeHidden();
  await expect(page.getByTestId("sankey-attribution")).toBeHidden();
  await expect(page.getByTestId("spending-attribution")).toBeHidden();

  // The graph's text equivalent, which is the part a canvas cannot carry (§2.9):
  // both sides listed with a total each, and both totals the same figure — the
  // property the picture is drawn on, asserted where a reader can see it.
  const inTotal = page.getByTestId("report-sankey").locator("p").filter({ hasText: /^In — / });
  const outTotal = page.getByTestId("report-sankey").locator("p").filter({ hasText: /^Out — / });
  await expect(inTotal).toBeVisible();
  await expect(outTotal).toBeVisible();
  expect(parseMoney((await inTotal.textContent()) ?? "")).toBe(
    parseMoney((await outTotal.textContent()) ?? ""),
  );

  // Filtered to one owner: still draws, and now says which scoping it used. Net
  // worth is account-scoped while spending and cash flow are row-scoped, so leaving
  // that unsaid would be the UI implying the two views add up. They do not.
  await page.getByTestId("owner-filter").getByRole("button", { name: ownerName, exact: true }).click();
  await expect(page.getByTestId("net-worth-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toBeVisible();
  await expect(page.getByTestId("net-worth-attribution")).toContainText("account");
  await expect(page.getByTestId("cash-flow-attribution")).toBeVisible();
  await expect(page.getByTestId("cash-flow-attribution")).toContainText("row");
  await expect(page.getByTestId("sankey-attribution")).toBeVisible();
  await expect(page.getByTestId("sankey-attribution")).toContainText("row");
  await expect(page.getByTestId("spending-attribution")).toBeVisible();
  await expect(page.getByTestId("spending-attribution")).toContainText("row");

  // Back to the whole household: the caveats go away with the filter.
  await page.getByTestId("owner-filter-all").click();
  await expect(page.getByTestId("net-worth-attribution")).toBeHidden();
  await expect(page.getByTestId("cash-flow-attribution")).toBeHidden();
  await expect(page.getByTestId("sankey-attribution")).toBeHidden();
  await expect(page.getByTestId("spending-attribution")).toBeHidden();
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("cash-flow-sankey").locator("canvas")).toBeVisible();
});

// The window is the page's one piece of linkable state, and this is the only
// place that proves it: the component test runs against a `MemoryRouter` with no
// server, so nothing there can show that a window the reader set is still the
// window they get back a day later.
test("the report window is the reader's, and survives a reload", async ({ page }) => {
  await login(page);
  await page.getByTestId("nav-reports").click();

  // The default is the current year. The control names the period ("This year"),
  // and this line prints the days it resolved to, because a stored link read next
  // month has to say which days it means. The `<time datetime>` is asserted and
  // not the text: the text is locale-formatted, the attribute is not.
  const windowLine = page.getByTestId("report-window");
  const year = new Date().getFullYear();
  await expect(windowLine.locator("time").first()).toHaveAttribute("datetime", `${year}-01-01`);
  await expect(windowLine.locator("time").last()).toHaveAttribute("datetime", `${year}-12-31`);

  // A custom window goes in the URL, not into a `useState`.
  await page.getByTestId("range-custom").click();
  await page.getByTestId("range-start").fill("2020-03-01");
  await page.getByTestId("range-end").fill("2020-03-31");
  await expect(page).toHaveURL(/range=custom/);
  await expect(windowLine.locator("time").first()).toHaveAttribute("datetime", "2020-03-01");
  await expect(windowLine.locator("time").last()).toHaveAttribute("datetime", "2020-03-31");
  // A window with nothing in it still draws a chart — empty axes, not a
  // missing card — which is what makes the reload below a real assertion.
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();

  // Reload: it all comes back, because all of it was in the address bar.
  await page.reload();
  await expect(page.getByTestId("range-custom")).toHaveAttribute("aria-pressed", "true");
  await expect(windowLine.locator("time").first()).toHaveAttribute("datetime", "2020-03-01");
  await expect(page.getByTestId("cash-flow-chart").locator("canvas")).toBeVisible();

  // An inverted window is refused in the control and draws nothing underneath it.
  // Not an empty state either: that would read as a household with no money,
  // which is a wrong answer to a question the reader never managed to ask.
  await page.getByTestId("range-start").fill("2020-06-01");
  await expect(page.getByTestId("range-invalid")).toBeVisible();
  await expect(page.getByTestId("report-net-worth")).toContainText("ends before it starts");
  await expect(page.getByTestId("net-worth-chart")).toHaveCount(0);
  await expect(page.getByTestId("cash-flow-chart")).toHaveCount(0);
  await expect(page.getByTestId("cash-flow-sankey")).toHaveCount(0);
  await expect(page.getByTestId("spending-donut")).toHaveCount(0);

  // One click back to the default, and the charts return. The default preset is
  // spelled by the absence of a param, so the URL is bare again.
  await page.getByTestId("range-this-year").click();
  await expect(page).not.toHaveURL(/range=/);
  await expect(page.getByTestId("net-worth-chart").locator("canvas")).toBeVisible();
});
