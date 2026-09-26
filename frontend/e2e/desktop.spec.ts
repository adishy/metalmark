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
    // The allocation moved to Insights → Allocations (ADR-0054); what shares
    // this grid now is the pointer to it and the holdings list.
    name: "investments: the Insights pointer beside holdings",
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

    // #29. The pane sits at the top of its column, so a row picked far down the
    // ledger opened a form that had already scrolled off: measured at 1280x800
    // with the page at the bottom, the pane's box sat at y = -1668 and the only
    // way to reach it was to scroll back up. Nothing in this file held that —
    // the geometry test above clicks the *first* row, the one case that always
    // worked.
    test("a row deep in the ledger opens its editor in view (#29)", async ({ page }) => {
      await login(page);
      await settle(page, "/transactions");

      // The claim needs a ledger taller than the viewport; on a short one the
      // broken layout and the fixed one look the same.
      const viewport = await page.evaluate(() => window.innerHeight);
      const docHeight = await page.evaluate(() => document.documentElement.scrollHeight);
      expect(docHeight, "the seeded ledger must be taller than the viewport").toBeGreaterThan(
        viewport + 200,
      );

      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const scrolled = await page.evaluate(() => window.scrollY);
      expect(scrolled, "the scroll to the bottom must have moved the page").toBeGreaterThan(0);

      const last = page.locator('[data-testid^="txn-row-"]').last();
      await last.click();
      await expect(page.getByTestId("txn-detail")).toBeVisible();

      const pane = await page.evaluate(() => {
        const header = document.querySelector("header")!.getBoundingClientRect();
        const column = document.querySelector("[data-testid=txn-detail-column]") as HTMLElement;
        const box = column.getBoundingClientRect();
        const cs = getComputedStyle(column);
        return {
          headerBottom: header.bottom,
          top: box.top,
          bottom: box.bottom,
          viewport: window.innerHeight,
          scrollY: window.scrollY,
          position: cs.position,
          stickyTop: parseFloat(cs.top),
          overflowY: cs.overflowY,
        };
      });

      // Sticky, and pinned clear of the bar rather than under it. The offset is
      // read from the computed style rather than assumed: a pane that sticks to
      // `top-0` measures a top of 0 and hides its first 61 px — the header's
      // height — behind the header.
      expect(pane.position).toBe("sticky");
      expect(pane.stickyTop).toBeGreaterThanOrEqual(pane.headerBottom - 1);

      // On screen, and wholly so. `overflow-y-auto` is what makes a form taller
      // than the viewport reachable: its bottom (Save, Delete) sits inside a box
      // that fits, instead of staying pinned below the fold.
      expect(pane.top, "the pane opens above the fold").toBeGreaterThanOrEqual(0);
      expect(pane.top, "the pane opens below the header").toBeLessThan(pane.viewport - 44);
      expect(pane.overflowY).toBe("auto");
      expect(pane.bottom).toBeLessThanOrEqual(pane.viewport + 1);

      // And it got there without moving the page: no forced scroll to the top.
      // (Playwright scrolls an element into view before clicking it, so a pixel
      // either way is not the point; 900 rows of travel is.)
      expect(Math.abs(pane.scrollY - scrolled), "the click must not move the page").toBeLessThan(2);
    });

    // #28. The row is one target (§4.6) and now carries a second control. The
    // failure this test exists for is not "the category did not change" — it is
    // "clicking the category opened the detail pane", which is exactly what a
    // *nested* control does: the browser hands a click inside a button to the
    // button, and no amount of styling changes that. Nothing else can see it.
    // The unit tests cannot (jsdom computes no `lg:` layout, so the cell is
    // reachable at every width there), and no screenshot can (both trees look
    // identical). It is a claim about the DOM tree and about which control a
    // click belongs to, so it is asserted here, in a browser, with a real API.
    test("a category is changed from its row without opening the editor (#28)", async ({ page }) => {
      await login(page);
      await settle(page, "/transactions");

      const row = page.locator('[data-testid^="txn-row-"]').nth(2);
      const cell = row.locator("xpath=..").locator('[data-testid^="txn-category-"]');
      await expect(cell, "a seeded row must offer the inline category control").toHaveCount(1);

      // Siblings, never nested: the row's button must not contain the control.
      const tree = await page.evaluate(() => {
        const rowButton = document.querySelectorAll('[data-testid^="txn-row-"]')[2];
        const parent = rowButton.parentElement!;
        const control = parent.querySelector('[data-testid^="txn-category-"]')!;
        return {
          nested: rowButton.contains(control),
          children: [...parent.children].map((c) => c.tagName),
        };
      });
      expect(tree.nested, "the control is inside the row's button (§4.6)").toBe(false);
      expect(tree.children, "the row button and its second control are siblings").toEqual([
        "BUTTON",
        "DIV",
      ]);

      const height = () =>
        row.locator("xpath=..").evaluate((el) => Math.round(el.getBoundingClientRect().height));
      const before = await height();
      const label = (await cell.getAttribute("aria-label")) ?? "";
      expect(label, "the control must say what it changes").toContain("Change category, currently");

      // Open it. §9.4's row gains columns, not height — and the pane stays shut.
      await cell.click();
      const options = page.locator('[role="listbox"] [role="option"]');
      await expect(options.first()).toBeVisible();
      await expect(page.getByTestId("txn-detail")).toHaveCount(0);

      const texts = (await options.allInnerTexts()).map((t) => t.trim());
      // Skip index 0: that row is the picker's own "no category" entry
      // (`categoryOptions[0]` in the page), and picking it is a *clearing* —
      // `category_id: null` on the wire — which is not the change this test is
      // about. The label check below only skips it when the row being edited has
      // no category already, and which row of the ledger sits at index 2 depends
      // on what the rest of the suite has added, so without the index guard this
      // passes on an uncategorised row and fails on a categorised one.
      const pick = texts.findIndex((t, i) => i > 0 && t && !label.endsWith(t));
      expect(
        pick,
        "the picker must offer a category the row does not already say",
      ).toBeGreaterThan(0);

      // A real pick, over the wire: the row repaints from the server's answer
      // rather than from the click, so the request is the evidence.
      const patched = page.waitForResponse(
        (r) => r.request().method() === "PATCH" && r.url().includes("/transactions/"),
      );
      await options.nth(pick).click();
      const response = await patched;
      expect(response.status()).toBe(200);
      const body = JSON.parse(response.request().postData() ?? "{}");
      expect(body.category_id, "the pick travels as a category change").toEqual(expect.any(String));

      await expect(cell).toHaveAttribute("aria-label", `Change category, currently ${texts[pick]}`);
      expect(await height(), "the row must not grow to fit its control").toBe(before);
      await expect(page.getByTestId("txn-detail"), "picking must not open the pane").toHaveCount(0);

      // ...and back, so the next run of this test starts where this one did.
      const original = label.slice("Change category, currently ".length);
      await cell.click();
      await expect(options.first()).toBeVisible();
      const back = (await options.allInnerTexts()).map((t) => t.trim()).indexOf(original);
      expect(back, "the row's own category must be offered again").toBeGreaterThanOrEqual(0);
      await options.nth(back).click();
      await expect(cell).toHaveAttribute("aria-label", label);
      expect(await height()).toBe(before);

      // And the row is still the target it was: away from the cell, it opens the
      // editor it always did.
      await row.locator("p").first().click();
      await expect(page.getByTestId("txn-detail")).toBeVisible();
    });

    // #26. Each filter is now one pill that opens a panel, and §9.6 is the
    // reason a pill is the right shape for it: a Dialog is a bottom sheet under
    // 640 px and a centred modal above it, and at `lg:` it is "never a bottom
    // sheet". That is a geometric claim about one component — the sheet and the
    // modal are the same element with the same classes, differing only by the
    // overlay's `items-end sm:items-center` — so it cannot be read off the
    // markup, and a screenshot cannot fail it. It is measured here.
    test("a filter panel is a centred dialog at lg:, and its picks outlive it (#26)", async ({
      page,
    }) => {
      await login(page);
      await settle(page, "/transactions");

      const pill = page.getByTestId("filter-categories");
      await expect(pill).toHaveAttribute("aria-expanded", "false");
      await pill.click();
      await expect(pill).toHaveAttribute("aria-expanded", "true");
      const panel = page.getByTestId("filter-categories-sheet");
      await expect(panel).toBeVisible();

      // A sheet is full-bleed and hangs off the bottom edge; a centred modal
      // floats with equal space above and below. Both are `w-full max-w-lg`.
      const vp = page.viewportSize()!;
      const box = (await panel.boundingBox())!;
      expect(box.width, "a bottom sheet would span the viewport").toBeLessThan(vp.width * 0.6);
      expect(
        Math.abs(box.y - (vp.height - box.y - box.height)),
        `a bottom sheet would sit at the bottom edge (y=${Math.round(box.y)})`,
      ).toBeLessThan(8);

      // §4.14's control, not a listbox: more than one answer is true at once,
      // and `role="option"` + `aria-selected` cannot say that (§4.5). The
      // checkboxes are the selection itself, not a proxy for it.
      const options = panel.locator('input[type="checkbox"]');
      await expect(options.nth(1), "the seeded household must have categories").toBeVisible();
      await expect(panel.locator('[role="option"]')).toHaveCount(0);

      // Two picks, two values on the wire. The API takes `category_id` as a
      // list, so a filter that quietly collapsed to one value would still look
      // right on screen — the request is what says it did not.
      await options.nth(0).click();
      const both = page.waitForResponse((r) => {
        const url = new URL(r.url());
        return url.pathname.endsWith("/transactions") && url.searchParams.getAll("category_id").length === 2;
      });
      await options.nth(1).click();
      expect((await both).status()).toBe(200);

      // Still open, and already applied: a multi-select that shut on the first
      // tick would be a single-select with extra steps.
      await expect(panel).toBeVisible();
      await expect(options.nth(0)).toBeChecked();
      await expect(options.nth(1)).toBeChecked();

      // The model, stated in the UI: picks are applied as they are made, so the
      // pill has the answer before the panel closes — closing it cannot lose a
      // selection, which is the failure "live" filters are prone to.
      await page.getByTestId("filter-categories-done").click();
      await expect(panel).toHaveCount(0);
      await expect(pill).toContainText("2 selected");
      await expect(pill).toHaveAttribute("aria-expanded", "false");
      // The values are named in the panel, which reopens holding them.
      await pill.click();
      await expect(options.nth(0)).toBeChecked();
      await expect(options.nth(1)).toBeChecked();
      // Clear is offered only while the filter is doing something, and it is
      // the panel's own Clear: the pill's value, not the whole bar's.
      await page.getByTestId("filter-categories-clear").click();
      await expect(pill).toContainText("All");
      await page.getByTestId("filter-categories-done").click();
      await expect(page.getByTestId("filter-clear"), "an unused Clear is clutter").toHaveCount(0);
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
