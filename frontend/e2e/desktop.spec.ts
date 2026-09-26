import { expect, test, type Page } from "@playwright/test";
import { addAccount, addTransaction, login, readRemaining } from "./helpers";

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
    // Three *columns*, each a stack of cards — issue #33. The assertion below
    // is the one that would notice a column count changing or the cells going
    // unequal; "no cell is padded to the height of its neighbour" is what the
    // dedicated trend/card tests in this file measure.
    name: "admin: three questions, three columns",
    route: "/admin",
    root: "[data-testid=admin-columns]",
    columns: 3,
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

    // Replaces the shape SHAPES used to carry for this page ("net worth paired
    // with income vs expense"). Issue #33 retired that pair — see DESIGN §9.3 —
    // so what is worth asserting is the opposite claim: each card claims the full
    // row, which is what stops a grid row from padding the shorter card's column
    // with blank space.
    test("the insights cards each take the full row, none is paired (#33)", async ({ page }) => {
      await login(page);
      await settle(page, "/insights/overview");

      const geo = await page.evaluate(() => {
        const root = document.querySelector("[data-testid=insights-overview-page]") as HTMLElement;
        const box = root.getBoundingClientRect();
        const cards = [...root.querySelectorAll("section[data-testid]")].map((el) => {
          const r = el.getBoundingClientRect();
          return {
            t: (el as HTMLElement).dataset.testid!,
            width: r.width,
            top: r.top,
            bottom: r.bottom,
          };
        });
        return { display: getComputedStyle(root).display, width: box.width, cards };
      });

      expect(geo.display, "the page's placement grid is gone").toBe("grid");
      // A selector that stopped matching would make every claim below vacuous.
      expect(geo.cards.length, "every report card must be found").toBeGreaterThanOrEqual(3);

      for (const card of geo.cards) {
        // One column is half the row minus the gap; two columns is the row. The
        // 2 px is float noise, not tolerance: the difference being caught is
        // ~550 px.
        expect(Math.abs(card.width - geo.width), `${card.t} is not full width`).toBeLessThan(2);
      }

      const stacked = [...geo.cards].sort((a, b) => a.top - b.top);
      for (let i = 1; i < stacked.length; i += 1) {
        expect(
          stacked[i].top,
          `${stacked[i].t} shares a row with ${stacked[i - 1].t}`,
        ).toBeGreaterThanOrEqual(stacked[i - 1].bottom - 1);
      }
    });

    test("the investments pointer is a row above the holdings, not a column beside them (#33)", async ({
      page,
    }) => {
      await login(page);
      await page.goto("/accounts");
      await page.getByTestId("accounts-view-investments").click();
      await expect(page.getByTestId("investments-view")).toBeVisible();
      await expect(page.getByTestId("portfolio")).toBeVisible();

      const geo = await page.evaluate(() => {
        const view = document.querySelector("[data-testid=investments-view]") as HTMLElement;
        const link = document.querySelector("[data-testid=allocation-moved-link]") as HTMLElement;
        const holdings = document.querySelector("[data-testid=portfolio]") as HTMLElement;
        const v = view.getBoundingClientRect();
        const l = link.getBoundingClientRect();
        const h = holdings.getBoundingClientRect();
        return {
          viewWidth: v.width,
          linkWidth: l.width,
          linkBottom: l.bottom,
          linkHeight: l.height,
          holdingsWidth: h.width,
          holdingsTop: h.top,
        };
      });

      // Both take the full width: the pointer used to be a 44 px card in a grid
      // cell whose row was as tall as the holdings list, with a blank column
      // under it.
      expect(geo.linkWidth).toBeGreaterThan(geo.viewWidth - 2);
      expect(geo.holdingsWidth).toBeGreaterThan(geo.viewWidth - 2);
      // And the pointer comes *before* the holdings, not beside them.
      expect(geo.linkBottom).toBeLessThanOrEqual(geo.holdingsTop);
      // §9.4: 44 px is the floor at `lg:` too, and the pointer is the one target
      // on this view.
      expect(geo.linkHeight).toBeGreaterThanOrEqual(44);
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

// Issue #36: the run history grew the page as syncs accumulated.
//
// The seeded household has *no* sync history — the demo seed creates none, and
// the sync spec's own runs are made against a connection this fixture does not
// have — so the one thing this test cannot get from the data is a long list. It
// is supplied instead, and the assertion is on what the app does with it: the
// server's own page (50 runs, the endpoint's default and `RUN_LIMIT` in
// `Admin.tsx`) is exactly the shape the issue is about, and fifty syncs is a
// fixture nobody would run a layout spec against.

/** A run as `GET /connections/runs` returns it: every field `RunRow` reads. */
function mockRun(i: number) {
  return {
    id: `mock-run-${i}`,
    connection_id: "mock-conn",
    connection_label: "Everyday",
    trigger: "manual",
    status: "ok",
    started_at: new Date(Date.UTC(2026, 8, 20, 10, i)).toISOString(),
    finished_at: new Date(Date.UTC(2026, 8, 20, 10, i) + 4_000).toISOString(),
    duration_ms: 4_000,
    http_ms: 900,
    http_status: 200,
    bytes_fetched: 1_024,
    accounts_seen: 2,
    accounts_created: 0,
    accounts_remapped: 0,
    txns_rekeyed: 0,
    txns_inserted: 3,
    txns_updated: 1,
    txns_reconciled: 0,
    pendings_expired: 0,
    transfers_matched: 0,
    rules_applied: 0,
    error: null,
  };
}

test.describe("the run history is a fixed region, not a page that grows (#36)", () => {
  test("a longer history takes the same room, and stays scrollable by keyboard", async ({ page }) => {
    // Three full loads of the page, each with its own connect/checks/tokens
    // requests, plus a keyboard interaction — over the 30 s default.
    test.slow();

    await login(page);

    let runs = 2;
    await page.route("**/connections/runs*", (route) =>
      route.fulfill({ json: Array.from({ length: runs }, (_, i) => mockRun(i)) }),
    );

    /** Render `/admin` with `n` runs in the history and measure it.
     *
     * `card` is the runs card itself, and it is measured instead of the page on
     * purpose. Admin is three columns of live data — connections, checks, agent
     * tokens — each landing on its own schedule, and an earlier version of this
     * test compared page heights across two loads and came back 8 px apart when
     * a neighbouring card had not finished: a difference in the *fixture's*
     * timing, not in the list. The card that holds the list is fully determined
     * by the list. */
    const measure = async (n: number) => {
      runs = n;
      await page.goto("/admin");
      await expect(page.getByTestId("run-region")).toBeVisible();
      return page.evaluate(() => {
        const region = document.querySelector("[data-testid=run-region]") as HTMLElement;
        const cs = getComputedStyle(region);
        return {
          role: region.getAttribute("role"),
          tabIndex: region.getAttribute("tabindex"),
          label: region.getAttribute("aria-label"),
          copy: document.querySelector("[data-testid=run-count]")!.textContent ?? "",
          maxHeight: cs.maxHeight,
          overflowY: cs.overflowY,
          height: region.getBoundingClientRect().height,
          clientHeight: region.clientHeight,
          scrollHeight: region.scrollHeight,
          card: Math.round(region.closest("section")!.getBoundingClientRect().height),
          page: document.documentElement.scrollHeight,
        };
      });
    };

    const two = await measure(2);

    // §4.7's scroll region: named, and in the tab order, because an `overflow`
    // box is not focusable on its own and a scroll region a keyboard cannot
    // scroll hides its content from anyone not using a pointer (2.1.1).
    expect(two.role).toBe("region");
    expect(two.tabIndex).toBe("0");
    expect(two.label).toBe("Run history, 2 runs, newest first");
    expect(two.copy).toBe("2 runs, newest first.");
    // A short history is a short card. The cap is a ceiling, not a reservation:
    // reserving 384 px for two rows is the empty gutter this batch is about.
    expect(two.height).toBeLessThan(380);
    expect(two.scrollHeight).toBe(two.clientHeight);

    // The endpoint's own page size: what the panel asks for, and the most it can
    // ever be asked for without changing that.
    const fifty = await measure(50);
    expect(fifty.copy).toBe("The 50 most recent runs. Older runs, if any, are not listed.");
    expect(fifty.label).toBe("Run history, 50 runs, newest first");

    // The cap itself: `max-h-96` is 24rem, and this app's root is 16 px.
    expect(fifty.overflowY).toBe("auto");
    expect(fifty.maxHeight).toBe("384px");
    expect(fifty.height).toBeLessThanOrEqual(385);
    // ...and it is doing work: fifty rows are far taller than the box.
    expect(fifty.scrollHeight).toBeGreaterThan(fifty.clientHeight * 2);

    // The claim the issue makes, in the sharpest form available: the *longest
    // history the endpoint can return* (200 is `MAX_RUN_LIMIT`) takes exactly the
    // same room as fifty runs. Four times the content, the same card, to the
    // pixel — where an unbounded list would be some 17,000 px taller.
    const longest = await measure(200);
    expect(longest.copy).toBe(fifty.copy);
    expect(longest.card).toBe(fifty.card);
    expect(longest.height).toBeLessThanOrEqual(385);
    // ...and it is genuinely four times the content, so the equality above is
    // the cap doing its job rather than two short lists.
    expect(longest.scrollHeight).toBeGreaterThan(fifty.scrollHeight * 3);
    // The list is many times the height of the page it is on, which is what
    // "scrolled inside itself" means. Laid out in the page, this state would put
    // the document at ~23,000 px.
    expect(longest.page).toBeLessThan(longest.scrollHeight / 4);

    // Keyboard: focus the region and page down. This is the whole reason it is
    // focusable — the rows below the fold have to be reachable without a mouse,
    // and a scroll region that cannot be scrolled from the keyboard hides its
    // content from anyone who does not use one (2.1.1).
    await page.getByTestId("run-region").focus();
    await page.keyboard.press("PageDown");
    await expect
      .poll(() => page.getByTestId("run-region").evaluate((el) => el.scrollTop))
      .toBeGreaterThan(0);

    // And a history that fits without scrolling is a short card, not 384 px of
    // reserved space with two rows at the top of it: the cap is a ceiling, not a
    // reservation — the empty gutter this batch exists to remove (#33).
    expect(two.card).toBeLessThan(fifty.card);
  });
});

// Issue #31's other half. The holdings cards on the Investments view were the
// named instance and #33's commit bounded them (one scroll region per account
// card); this is the ranking the same view links to — Insights → Allocations —
// which had no shape of its own at all. The demo household is the wrong fixture
// for measuring it in either direction: it holds one cash row, so the page has
// neither a long list nor a cap to prove, which is why the endpoint is mocked at
// both sizes here.

/** An `Allocation` as `GET /investments/allocation` returns it, at `n` rows. */
function mockAllocation(n: number) {
  return {
    as_of: "2026-09-20",
    base_currency: "USD",
    total_base: String(n * 100),
    rows: Array.from({ length: n }, (_, i) => ({
      key: `sec-${i}`,
      label: `SEC${i}`,
      value_base: "100.00",
      percent: "1.0000",
      holdings: 1,
      sources: [],
    })),
    unpriced_positions: 0,
    no_rate_positions: 0,
    max_stale_days: 2,
  };
}

test.describe("a long allocation scrolls inside its card, not down the page (#31)", () => {
  test("forty groups take one box, and the total stays on screen above them", async ({ page }) => {
    // Two full loads plus a keyboard interaction — over the 30 s default on a
    // loaded host.
    test.slow();

    await login(page);

    let groups = 3;
    await page.route("**/investments/allocation*", (route) =>
      route.fulfill({ json: mockAllocation(groups) }),
    );

    const measure = async (n: number) => {
      groups = n;
      await page.goto("/insights/allocations");
      await expect(page.getByTestId("allocation-rows-region")).toBeVisible();
      return page.evaluate(() => {
        const box = document.querySelector("[data-testid=allocation-rows-region]") as HTMLElement;
        const cs = getComputedStyle(box);
        const total = document.querySelector("[data-testid=allocation-total]") as HTMLElement;
        return {
          role: box.getAttribute("role"),
          tabIndex: box.getAttribute("tabindex"),
          label: box.getAttribute("aria-label"),
          maxHeight: cs.maxHeight,
          overflowY: cs.overflowY,
          height: box.getBoundingClientRect().height,
          clientHeight: box.clientHeight,
          scrollHeight: box.scrollHeight,
          rows: document.querySelectorAll("[data-testid^=allocation-row-]").length,
          totalBottom: total.getBoundingClientRect().bottom,
          page: document.documentElement.scrollHeight,
          viewport: window.innerHeight,
        };
      });
    };

    const three = await measure(3);

    // A ranking that fits is left alone: no region, no tab stop, no cap taking
    // effect. Seven rows is where the box starts to scroll, and three is not it.
    expect(three.rows).toBe(3);
    expect(three.role).toBeNull();
    expect(three.tabIndex).toBeNull();
    expect(three.overflowY).toBe("visible");
    expect(three.height).toBeLessThan(384);

    const forty = await measure(40);

    // §4.7's region: named with the grouping and the row count, and in the tab
    // order, because an `overflow` box is not focusable on its own and a scroll
    // region a keyboard cannot scroll hides its content from anyone not using a
    // pointer (2.1.1).
    expect(forty.role).toBe("region");
    expect(forty.tabIndex).toBe("0");
    expect(forty.label).toBe("Allocation by security, 40 rows");
    // The cap itself: `max-h-96` is 24rem, and this app's root is 16 px.
    expect(forty.overflowY).toBe("auto");
    expect(forty.maxHeight).toBe("384px");
    expect(forty.height).toBeLessThanOrEqual(385);
    // ...and it is doing work: forty rows are far taller than the box.
    expect(forty.scrollHeight).toBeGreaterThan(forty.clientHeight * 2);
    // Nothing was dropped by the cap: every row the server sent is in the page.
    expect(forty.rows).toBe(40);
    // The total and the caveats below it are *outside* the box, so they are on
    // screen — without scrolling the page at all — while the ranking moves under
    // them. Laid out in the page instead, forty rows measured 2,212 px of card in
    // a 2,515 px document, with the total at the bottom of it.
    expect(forty.totalBottom).toBeLessThanOrEqual(forty.viewport);
    // The claim the issue makes, in the sharpest form available: 37 more groups
    // cost at most one box of page, where unbounded they cost ~1,900 px. The
    // 400 px is the box's own growth (156 px of three rows to 384) plus slack for
    // the row that follows the header; the difference being caught is the whole
    // list.
    expect(forty.page - three.page).toBeLessThan(400);
    expect(forty.page).toBeLessThan(900);

    // Keyboard: focus the box and page down. This is the whole reason it is
    // focusable — the rows below the fold have to be reachable without a mouse.
    await page.getByTestId("allocation-rows-region").focus();
    await page.keyboard.press("PageDown");
    await expect
      .poll(() => page.getByTestId("allocation-rows-region").evaluate((el) => el.scrollTop))
      .toBeGreaterThan(0);
  });
});

// ADR-0057. `/review` is §9's one recorded exception to "a page must not have two
// JSX trees branching on viewport", and the price of an exception is the half of
// the page nobody measures: `review.spec.ts` is pinned below `lg:` and covers the
// deck, and this is the table. Both trees, both specs — the exception is only
// affordable because of it.
//
// The queue is seeded here rather than assumed: the demo household has nothing
// pending. It goes in through the ledger — one account, one uncategorized
// transaction, the same route `review.spec.ts` seeds by — because the review
// queue is not a fixture that can be posted directly.
//
// Every test below files the rows it made, through the page, so a run leaves the
// queue as it found it: this spec runs before `review.spec.ts`, whose
// `reviewTarget` drains a backlog to reach its own card, and an accumulating
// queue would be this file's fault.

/** Seed `merchants` as uncategorized transactions and land on `/review`. */
async function seedReviewQueue(page: Page, merchants: string[]): Promise<void> {
  const run = Date.now();
  const accountName = `Table Acct ${run}`;
  await login(page);
  await addAccount(page, { name: accountName, balance: "100", currency: "USD" });
  await page.getByTestId("nav-transactions").click();
  for (const merchant of merchants) {
    await addTransaction(page, { accountName, amount: "-7.77", merchant });
  }
  await page.getByTestId("nav-review").click();
  await expect(page.getByTestId("review-rows")).toBeVisible();
}

/** The row showing `merchant`. Found by text, never by position: the queue is
 *  shared and ordered, so a row another spec left pending can sit ahead of it. */
function reviewRow(page: Page, merchant: string) {
  return page
    .getByTestId("review-rows")
    .locator("li[data-row]")
    .filter({ hasText: merchant })
    .first();
}

/** Every row's id, in the order they are shown. */
async function rowIds(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll("[data-testid=review-rows] li[data-row]")].map(
      (r) => (r as HTMLElement).dataset.row ?? "",
    ),
  );
}

test.describe("the review queue at lg: is a triage table (ADR-0057)", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("a row is §9.4's row, under a real header, and the deck is not on the page", async ({
    page,
  }) => {
    test.slow();

    const merchant = `Table Row ${Date.now()}`;
    await seedReviewQueue(page, [merchant]);
    const row = reviewRow(page, merchant);
    await expect(row).toBeVisible();

    const geo = await page.evaluate(() => {
      const rows = document.querySelector("[data-testid=review-rows]") as HTMLElement;
      const r = rows.querySelector("li[data-row]") as HTMLElement;
      const header = rows.firstElementChild as HTMLElement;
      // The row's four value cells live in a `display: contents` wrapper, so the
      // DOM's child count is smaller than the grid's item count. This reads the
      // grid's items: a child that is `contents` contributes its own children.
      const cells = (el: HTMLElement) =>
        [...el.children].flatMap((c) =>
          getComputedStyle(c).display === "contents" ? [...c.children] : [c],
        ) as HTMLElement[];
      const cell = (sel: string) => r.querySelector(sel) as HTMLElement;
      const controls = [
        '[data-testid^="review-open-"]',
        '[data-testid^="review-category-"]',
        '[data-testid^="review-ignore-"]',
        '[data-testid^="review-approve-"]',
      ].map((sel) => {
        const box = cell(sel).getBoundingClientRect();
        return { w: Math.round(box.width), h: Math.round(box.height) };
      });
      const money = cells(r).map((c) => getComputedStyle(c).textAlign);
      const decide = getComputedStyle(r.lastElementChild as HTMLElement).justifyContent;
      return {
        deck: document.querySelectorAll("[data-testid=review-deck]").length,
        cards: document.querySelectorAll("[data-testid=swipe-card]").length,
        headerHidden: header.getAttribute("aria-hidden"),
        headerText: header.textContent ?? "",
        headerLefts: cells(header).map((c) => Math.round(c.getBoundingClientRect().left)),
        rowLefts: cells(r).map((c) => Math.round(c.getBoundingClientRect().left)),
        rowTemplate: getComputedStyle(r).gridTemplateColumns,
        display: getComputedStyle(r).display,
        minHeight: getComputedStyle(r).minHeight,
        height: Math.round(r.getBoundingClientRect().height),
        tabStops: rows.querySelectorAll('[tabindex="0"]').length,
        controls,
        money,
        decide,
        overflow:
          document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });

    // One instrument at a time: at 1280 the deck is not mounted, so its window
    // keydown is not either — it stands down structurally rather than by a guard.
    expect(geo.deck, "the deck is still rendered at lg:").toBe(0);
    expect(geo.cards, "a swipe card is still rendered at lg:").toBe(0);

    // §9.4: "A real header row appears above the list, naming the columns." The
    // sighted reader gets the names; `aria-hidden`, because a screen reader would
    // otherwise hear them before every row's own values.
    for (const name of ["Merchant", "Date", "Category", "Owner", "Amount"]) {
      expect(geo.headerText, `the header must name ${name}`).toContain(name);
    }
    expect(geo.headerHidden).toBe("true");
    // The header is the table's columns, so it is the same grid as a row *and its
    // labels sit over the columns they name* — which is what makes it a header
    // rather than a caption above a list of its own. Measured as column positions
    // rather than as a class: `lg:grid-cols-*` that never compiled (§8 rule 5's
    // failure mode) leaves the DOM exactly as written.
    expect(geo.rowTemplate.split(" ").length, "the row is not seven columns").toBe(7);
    expect(geo.rowLefts, "the header's labels do not line up with the row's cells").toEqual(
      geo.headerLefts,
    );
    expect(geo.display).toBe("grid");

    // §9.4's row height, and its floor: `min-h-12` is the desktop figure and 44
    // is still the floor. The row measures 56 px because the four controls in it
    // are each 44 — which is asserted here rather than left to `a11y.spec.ts`,
    // whose sweep only sees rows when another spec has left some pending.
    expect(geo.minHeight).toBe("48px");
    expect(geo.height).toBeGreaterThanOrEqual(48);
    for (const [i, control] of geo.controls.entries()) {
      expect(control.h, `row control ${i} is under 44 px tall`).toBeGreaterThanOrEqual(44);
      expect(control.w, `row control ${i} is under 44 px wide`).toBeGreaterThanOrEqual(44);
    }

    // One tab stop for the queue, not four per row (§4.14's roving model): a
    // fifty-row queue is otherwise two hundred stops between the page and its
    // end, which is not a keyboard route.
    expect(geo.tabStops, "the queue must have exactly one tab stop").toBe(1);

    // §6.1: a column of amounts lines up. The amount is the sixth column — the
    // last of the value cells — and it is right-aligned, as is the column of
    // actions beside it, the latter by `justify-content` because a flex row does
    // not answer to `text-align`.
    expect(geo.money[5], "the amount column is not right-aligned").toBe("right");
    expect(geo.decide, "the actions column is not right-aligned").toBe("flex-end");

    expect(geo.overflow, "the table pushes the page sideways").toBeLessThanOrEqual(0);

    // Filing by mouse, which is also how this test gives the queue back: the row
    // leaves, and the rows that were behind it are still there.
    await reviewRow(page, merchant).getByTestId(/^review-approve-/).click();
    await expect(reviewRow(page, merchant), "an approved row must leave").toHaveCount(0);
    await expect(page.locator("[data-testid=review-rows] li[data-row]").first()).toBeVisible();
  });

  test("the keyboard works the queue, and says what it did", async ({ page }) => {
    test.slow();

    const first = `Table Keys A ${Date.now()}`;
    const second = `Table Keys B ${Date.now()}`;
    await seedReviewQueue(page, [first, second]);

    const rows = page.locator("[data-testid=review-rows] li[data-row]");
    await expect(rows.first()).toBeVisible();

    // The queue's own rows, in order — the assertions below are about the
    // relation between a row and its neighbour, and never about which merchant
    // happens to be where.
    const ids = await rowIds(page);
    expect(ids.length, "the queue must hold the rows this test seeded").toBeGreaterThanOrEqual(2);

    // Before anything is focused *in the list*: the deck's keys are `window`-level
    // (`Review.tsx`), the table's are this list's own handler (§9.3, ADR-0057).
    // Focus is still on the nav link the seed clicked — nowhere near a row — so
    // ← / → / `e` must do nothing at all. The failure this rules out is a deck
    // left mounted under the table, deciding cards behind it: the whole reason
    // this page is two trees instead of one tree with `lg:hidden`.
    const beforeStrayKeys = await readRemaining(page);
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("e");
    await expect(page.getByTestId("txn-detail"), "`e` belongs to a row, not to the page").toHaveCount(0);
    expect(await rowIds(page), "→ must not file a row it is not standing on").toEqual(ids);
    expect(await readRemaining(page)).toBe(beforeStrayKeys);

    // Stand on the second row, so ↑ and ↓ both have somewhere to go.
    const standOn = ids[1];
    const openOf = (id: string) => page.locator(`[data-testid="review-open-${id}"]`);
    await openOf(standOn).focus();

    // One tab stop, and it is the row the keyboard is on.
    await expect(page.locator('[data-testid=review-rows] [tabindex="0"]')).toHaveCount(1);
    await expect(openOf(standOn)).toHaveAttribute("tabindex", "0");

    // ↓ to whatever comes next — or nowhere, when there is no next row: a queue
    // has a bottom, and wrapping to the top is how someone files a row they never
    // saw.
    const below = ids[2];
    await page.keyboard.press("ArrowDown");
    await expect(openOf(below ?? standOn)).toBeFocused();

    // ↑ back, then ↑ again to the first row, and once more with nowhere to go.
    await page.keyboard.press("ArrowUp");
    await expect(openOf(standOn)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(openOf(ids[0])).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(openOf(ids[0])).toBeFocused();

    // Now a decision, on a row this test knows by name. Where focus lands is
    // worked out from the order as it was *before*: the row that takes the
    // vacated place is the one after it, or the new last row when it was last.
    const idOfSecond = (await reviewRow(page, second).getAttribute("data-row")) ?? "";
    const expected = ids[ids.indexOf(idOfSecond) + 1] ?? ids[ids.indexOf(idOfSecond) - 1];
    const before = await readRemaining(page);
    await reviewRow(page, second).locator('[data-testid^="review-open-"]').focus();
    await page.keyboard.press("ArrowRight");

    await expect(reviewRow(page, second), "→ must file the row").toHaveCount(0);
    // Three things say what happened, because there is no throw to carry it: the
    // count, focus, and a line naming the row.
    expect(await readRemaining(page)).toBe(before - 1);
    if (expected) await expect(openOf(expected)).toBeFocused();
    await expect(page.getByTestId("review-last")).toHaveText(`${second} reviewed.`);

    // And ← ignores the row it is on. This one is seeded for the purpose and is
    // the last row left behind.
    await reviewRow(page, first).locator('[data-testid^="review-open-"]').focus();
    await page.keyboard.press("ArrowLeft");
    await expect(reviewRow(page, first), "← must file the row").toHaveCount(0);
    await expect(page.getByTestId("review-last")).toHaveText(`${first} ignored.`);
  });

  test("a category is assigned from the row, and the row keeps its place", async ({ page }) => {
    test.slow();

    const merchant = `Table Pick ${Date.now()}`;
    await seedReviewQueue(page, [merchant]);
    const before = await readRemaining(page);

    const row = reviewRow(page, merchant);
    await row.locator('[data-testid^="review-open-"]').focus();
    await page.keyboard.press("c");

    const picker = page.getByTestId("review-category-picker");
    await expect(picker).toBeVisible();
    await picker.getByTestId("review-category-picker-search").fill("Groceries");
    await picker.getByRole("button", { name: /Groceries/ }).first().click();

    // Filing a category is not deciding the transaction (§4.17, and the same on
    // both instruments): the row is still here, still asking, and the queue is no
    // shorter. It now answers the question the picker was opened to change.
    await expect(row.locator('[data-testid^="review-category-"]')).toContainText("Groceries");
    expect(await readRemaining(page)).toBe(before);

    // The detail opens over the rows as a dialog, never beside them as a pane:
    // §9.3's pane is the Transactions shape, and there is no second column here.
    await row.locator('[data-testid^="review-open-"]').click();
    const detail = page.getByTestId("txn-detail");
    await expect(detail).toBeVisible();
    await expect(detail).toHaveAttribute("role", "dialog");
    await page.getByTestId("txn-detail-close").click();
    await expect(detail).toHaveCount(0);
    await expect(row, "opening a row must not decide it").toBeVisible();

    // Give the queue back, through the page, as the other tests do.
    await reviewRow(page, merchant).getByTestId(/^review-approve-/).click();
    await expect(reviewRow(page, merchant)).toHaveCount(0);
  });
});

