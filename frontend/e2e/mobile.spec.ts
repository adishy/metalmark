import { test, expect, type Page } from "@playwright/test";
import { login } from "./helpers";

// A phone never scrolls sideways — not the page, and not a strip inside it.
// Found in review (session 06): chip rows and tab strips that scrolled
// horizontally, and a transactions screen wider than the phone. Both are
// failures of the same rule, so one test holds every route to it at the two
// widths DESIGN.md §5 names.
// ROUTES is every page a phone can reach, plus the redirects it can arrive
// through. `/insights` itself redirects to Overview, and `/reports` is kept
// deliberately as the legacy-redirect case: Insights' old address, which a
// bookmark still hits, and whose redirect has no more licence to spill
// sideways than a real page does.
//
// The Insights tabs are the union of the branches that carry them:
// `/insights/allocations` renders the placeholder on `feat/recurring` and the
// real tab on `feat/insights-allocations` (stacked on it) — so whichever of
// the two lands last leaves this list holding the full coverage rather than
// losing a tab to the merge.
const ROUTES = [
  "/accounts",
  "/transactions",
  "/review",
  "/insights",
  "/insights/overview",
  "/insights/allocations",
  "/insights/recurring",
  "/reports",
  "/settings",
  "/admin",
];
const WIDTHS = [360, 390];

async function sidewaysOffenders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const vw = document.documentElement.clientWidth;
    // The page's own width is never exempt — a sanctioned region still may not
    // push the document wide, and this is the check that catches it when one
    // does (a positioned descendant escaping the region, as the jobs card once
    // did before it was given `relative`).
    if (document.documentElement.scrollWidth > vw + 1) {
      out.push(`page scrollWidth ${document.documentElement.scrollWidth} > ${vw}`);
    }
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;
      // A chart's canvas and an SVG draw inside their own box; what matters is
      // the box, which the element checks below cover.
      if (el.closest("canvas, svg")) continue;
      // The one strip allowed to swipe (DESIGN §5), and whatever it holds.
      if (el.closest("[data-scroll-x-ok]")) continue;
      // DESIGN §5's other exception: a data table whose columns are the point,
      // kept in a labelled scroll region (§4.7). It covers the table, what the
      // table holds, and the region that scrolls it — the table is the whole of
      // the exemption, so a labelled region with no table in it is still an
      // offender. (The `closest` below matches the region itself too.)
      const region = el.closest('[role="region"][aria-label]');
      if (region && region.querySelector("table")) continue;
      const scrolls = /(auto|scroll)/.test(style.overflowX);
      if (scrolls && el.scrollWidth > el.clientWidth + 1) {
        out.push(`scrolls sideways: ${el.tagName.toLowerCase()}${el.dataset.testid ? `[${el.dataset.testid}]` : ""} ${el.scrollWidth}>${el.clientWidth}`);
      }
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > vw + 1 || r.left < -1) && !el.closest("[data-offscreen-ok]")) {
        // Only report the outermost offender, not every descendant of it.
        const parent = el.parentElement?.getBoundingClientRect();
        if (!parent || parent.right <= vw + 1) {
          out.push(`past the edge: ${el.tagName.toLowerCase()}${el.dataset.testid ? `[${el.dataset.testid}]` : ""} ${Math.round(r.left)}..${Math.round(r.right)}`);
        }
      }
    }
    return out;
  });
}

for (const width of WIDTHS) {
  test.describe(`phone ${width}px`, () => {
    test.use({ viewport: { width, height: 780 }, hasTouch: true, isMobile: true });

    test("no route scrolls or spills sideways", async ({ page }) => {
      await login(page);
      const found: string[] = [];
      for (const route of ROUTES) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(400);
        for (const f of await sidewaysOffenders(page)) found.push(`${route}  ${f}`);
      }
      expect(found).toEqual([]);
    });

    test("every bottom-bar destination is a generous target", async ({ page }) => {
      await login(page);
      const tabs = page.locator('[data-testid^="tab-"]');
      const n = await tabs.count();
      expect(n).toBeGreaterThan(0);
      for (let i = 0; i < n; i++) {
        const box = await tabs.nth(i).boundingBox();
        expect(box!.height).toBeGreaterThanOrEqual(56);
        expect(box!.width).toBeGreaterThanOrEqual(56);
      }
    });
  });
}
