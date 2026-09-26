import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

// DESIGN.md §9.7's two viewports, and the §9.3 arrangements that exist only at
// `lg:`.
//
// This is the half of the suite that measures *structure* where `a11y.spec.ts`
// measures targets. It exists because nothing else would notice a page's
// `lg:grid-cols-*` going away: the app still works, every control is still 44px,
// and a single column of full-width cards at 1280 is exactly what a phone layout
// looks like after the width is widened. That silent version is the regression
// this file is for — §9.3 calls the Transactions pane "the single biggest win",
// and "biggest win" is not a claim any assertion in the repo held.
//
// §9.7 names 1280x800 and 1440x900. The shell caps at `max-w-7xl` (1280) and
// nothing in the app sizes itself in `vw`, so a 1440 viewport's content box is
// identical to a 1280 one — the second viewport is not a second test, and the
// numbers asserted below are the same at both. It is here because §9.7 asks for
// it, and because "assumed identical" is not the same claim as "measured
// identical".
//
// 1023 is the half that matters most. One pixel below `lg:` every one of these
// has to be a single column again, and a breakpoint that is wrong in *that*
// direction is invisible to any sweep that only ever looks wide.

const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
] as const;

/** One below `lg:`. Not a viewport §9.7 names — it is the boundary §9.3 needs. */
const BELOW_LG = { width: 1023, height: 800 } as const;

interface Shape {
  name: string;
  route: string;
  /** The page's own root, which is the element §9.3 puts the grid on. */
  root: string;
  columns: number;
  /** Widest column ÷ narrowest. `undefined` means "equal within a pixel". */
  ratio?: number;
  prepare?: (page: Page) => Promise<void>;
}

const SHAPES: Shape[] = [
  {
    name: "transactions: the list keeps two thirds",
    route: "/transactions",
    root: "[data-testid=transactions-page]",
    columns: 2,
    ratio: 2,
  },
  {
    name: "insights overview: net worth paired with income vs expense",
    route: "/insights/overview",
    root: "[data-testid=insights-overview-page]",
    columns: 2,
  },
  {
    name: "admin: three questions, three columns",
    route: "/admin",
    root: "[data-testid=admin-page]",
    columns: 3,
  },
  {
    name: "investments: allocation beside holdings",
    route: "/accounts",
    root: "[data-testid=investments-view]",
    columns: 2,
    prepare: async (page) => {
      await page.getByTestId("accounts-view-investments").click();
      await expect(page.getByTestId("investments-view")).toBeVisible();
    },
  },
];

/** Read off the *computed* style rather than the class list. A `lg:grid-cols-*`
 *  that never compiled (§8 rule 5's failure mode) leaves the DOM exactly as
 *  written and the layout untouched, so a spec that greps the class string would
 *  pass on a page that does not grid. */
async function columnsOf(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) return null;
    const cs = getComputedStyle(el);
    return {
      display: cs.display,
      widths:
        cs.gridTemplateColumns === "none"
          ? []
          : cs.gridTemplateColumns.split(" ").map((v) => parseFloat(v)),
    };
  }, selector);
}

/** Laid out with a sideways scrollbar is the failure a width assertion cannot
 *  catch: a grid whose `minmax(0, …)` lost its `0` overflows its own column
 *  without changing the template's numbers. */
async function overflows(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

async function settle(page: Page, route: string, prepare?: (page: Page) => Promise<void>) {
  await page.goto(route);
  await expect(page.locator("main")).toBeVisible();
  if (prepare) await prepare(page);
  await page.waitForTimeout(300);
}

const ROUTES = ["/accounts", "/transactions", "/review", "/insights/overview", "/settings", "/admin"];

for (const vp of VIEWPORTS) {
  test.describe(`desktop ${vp.width}x${vp.height} (§9.7)`, () => {
    test.use({ viewport: vp });

    test("§9.3's arrangements hold", async ({ page }) => {
      await login(page);

      for (const shape of SHAPES) {
        await settle(page, shape.route, shape.prepare);

        const measured = await columnsOf(page, shape.root);
        expect(measured, `${shape.name}: root not found`).not.toBeNull();
        expect(measured!.display, `${shape.name}: not a grid`).toBe("grid");
        expect(measured!.widths.length, `${shape.name}: column count`).toBe(shape.columns);

        const lo = Math.min(...measured!.widths);
        const hi = Math.max(...measured!.widths);
        if (shape.ratio === undefined) {
          expect(hi - lo, `${shape.name}: columns unequal (${measured!.widths})`).toBeLessThan(2);
        } else {
          // `2fr 1fr` through a 24px gap lands on 805.33 and 402.67 — the ratio
          // is exactly 2, so the tolerance is for float noise, not for drift.
          expect(Math.abs(hi / lo - shape.ratio), `${shape.name}: ratio`).toBeLessThan(0.01);
        }

        expect(await overflows(page), `${shape.name}: sideways overflow`).toBeLessThanOrEqual(0);
      }
    });

    test("no route scrolls sideways", async ({ page }) => {
      await login(page);
      for (const route of ROUTES) {
        await settle(page, route);
        expect(await overflows(page), `${route} overflows sideways`).toBeLessThanOrEqual(0);
      }
    });

    test("the settings tabs are a rail, not a strip", async ({ page }) => {
      await login(page);
      await settle(page, "/settings");

      const rail = await page.locator("[role=tablist]").evaluate((el) => {
        const box = el.getBoundingClientRect();
        const panel = document.querySelector("[role=tabpanel]") as HTMLElement;
        const active = el.querySelector("[aria-selected=true]") as HTMLElement;
        return {
          orientation: el.getAttribute("aria-orientation"),
          direction: getComputedStyle(el).flexDirection,
          width: Math.round(box.width),
          right: box.right,
          panelLeft: panel.getBoundingClientRect().left,
          activeWidth: Math.round(active.getBoundingClientRect().width),
        };
      });

      // The attribute is what §9.3's "aria-orientation following the axis" asks
      // for, and it is the one thing on this page no CSS class can carry — which
      // also makes it the one thing that can silently stop matching the rail.
      expect(rail.orientation).toBe("vertical");
      expect(rail.direction).toBe("column");
      expect(rail.width).toBe(224); // `lg:w-56`
      // Beside, not above.
      expect(rail.panelLeft).toBeGreaterThan(rail.right);
      // The active tab fills the rail and carries the AppShell nav's inset fill
      // rather than the strip's underline.
      expect(rail.activeWidth).toBe(224);
    });

    test("the transaction detail is a pane, the phone's is a dialog", async ({ page }) => {
      await login(page);
      await settle(page, "/transactions");

      const row = page.locator('[data-testid^="txn-row-"]').first();
      await expect(row, "seeded ledger must have a row to open").toHaveCount(1);
      await row.click();
      await expect(page.getByTestId("txn-detail")).toBeVisible();

      const detail = await page.evaluate(() => {
        const list = document.querySelector("[data-testid=txn-list]")!.getBoundingClientRect();
        const pane = document.querySelector("[data-testid=txn-detail]") as HTMLElement;
        const box = pane.getBoundingClientRect();
        return {
          role: pane.getAttribute("role"),
          modal: pane.getAttribute("aria-modal"),
          listRight: list.right,
          left: box.left,
        };
      });

      // §9.6: at `lg:` never a sheet over the page. A pane is not a modal — it
      // traps no focus, locks no scroll, and carries no `aria-modal`, and none of
      // that is observable in a screenshot. Nor is "beside" versus "over": both
      // paint a form, and only the geometry and the role say which one a reader
      // got.
      expect(detail.left).toBeGreaterThanOrEqual(detail.listRight);
      expect(detail.role).toBe("region");
      expect(detail.modal).toBeNull();
    });
  });
}

test.describe("below lg (1023)", () => {
  test.use({ viewport: BELOW_LG });

  test("every §9.3 arrangement is a single column again", async ({ page }) => {
    await login(page);

    for (const shape of SHAPES) {
      await settle(page, shape.route, shape.prepare);

      const measured = await columnsOf(page, shape.root);
      expect(measured, `${shape.name}: root not found`).not.toBeNull();
      // `lg:grid` off means the element is not a grid container at all: no
      // widths, and the page is the block layout it is on a phone.
      expect(measured!.display, `${shape.name}: gridded below lg:`).not.toBe("grid");
      expect(measured!.widths, `${shape.name}: columns below lg:`).toEqual([]);
      expect(await overflows(page), `${shape.name}: sideways overflow`).toBeLessThanOrEqual(0);
    }
  });

  test("the settings tabs are a strip, and the transaction detail is a dialog", async ({ page }) => {
    await login(page);
    await settle(page, "/settings");

    const strip = await page.locator("[role=tablist]").evaluate((el) => ({
      orientation: el.getAttribute("aria-orientation"),
      direction: getComputedStyle(el).flexDirection,
    }));
    expect(strip.orientation).toBe("horizontal");
    expect(strip.direction).toBe("row");

    await settle(page, "/transactions");
    const row = page.locator('[data-testid^="txn-row-"]').first();
    await expect(row, "seeded ledger must have a row to open").toHaveCount(1);
    await row.click();
    await expect(page.getByTestId("txn-detail")).toBeVisible();

    const detail = await page.evaluate(() => {
      const list = document.querySelector("[data-testid=txn-list]")!.getBoundingClientRect();
      const pane = document.querySelector("[data-testid=txn-detail]") as HTMLElement;
      const box = pane.getBoundingClientRect();
      return {
        role: pane.getAttribute("role"),
        modal: pane.getAttribute("aria-modal"),
        // The sheet is full-width and comes up from the bottom, so it shares
        // horizontal space with the list instead of sitting beside it. This is
        // the same measurement the `lg:` case makes, read the other way — a
        // pane's left edge starts *past* the list, so it never overlaps.
        overlaps: box.left < list.right && box.right > list.left,
      };
    });
    expect(detail.overlaps).toBe(true);
    expect(detail.role).toBe("dialog");
    expect(detail.modal).toBe("true");
  });
});
