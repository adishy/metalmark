// Hovering a chart must not erase it.
//
// This exists because it did. Hovering the net-worth line made its area fill
// disappear while the stroke and the dots stayed, so the chart came apart under
// the pointer. The cause was the colour format in `chartTokens.ts` — zrender
// cannot parse CSS Color 4's `rgb(71 85 105)`, so the emphasis colour ECharts
// derives by lifting it came out `undefined` and zrender declined to paint the
// fill at all. See the header of `theme/chartInteraction.ts` for the full trace.
//
// Nothing in the suite could see it: `reports.spec.ts` asserts the chart's canvas
// is visible, and a canvas that has just been emptied is still visible.
//
// So this measures the ink. The canvas is transparent where nothing is drawn
// (ECharts is given no `backgroundColor`, so the card shows through), which
// makes "pixels with alpha" a direct count of how much chart is on screen. A
// hover that erases a series drops that count hard; the assertion is that it
// does not.
//
// Note the tooltip is a DOM element, not canvas — so it cannot inflate the
// hovered count and mask a series that vanished underneath it.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

/**
 * Count pixels with alpha above `min` on a chart's canvas.
 *
 * Two thresholds, because erasure and dimming are different events and one
 * number cannot see both:
 *
 *   - `ink` (low) — anything drawn at all. This is what catches a series that
 *     disappeared, which is the defect this file exists for.
 *   - `strong` (high) — anything drawn at *full* strength. A spotlighted chart
 *     keeps its pixels: it only makes them fainter, so a count at the low
 *     threshold barely moves (a dimmed slice sits near alpha 76). Counting
 *     intensity is the only way to see that the spotlight happened.
 */
async function pixelsAbove(
  page: import("@playwright/test").Page,
  testid: string,
  min: number,
): Promise<number> {
  return page
    .getByTestId(testid)
    .locator("canvas")
    .first()
    .evaluate((c: HTMLCanvasElement, floor: number) => {
      const ctx = c.getContext("2d");
      if (!ctx) return -1;
      const { data } = ctx.getImageData(0, 0, c.width, c.height);
      let drawn = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i] > floor) drawn++;
      return drawn;
    }, min);
}

/** Anything drawn. Falls when a series is erased. */
const ink = (page: import("@playwright/test").Page, testid: string) => pixelsAbove(page, testid, 8);

/** Drawn at full strength. Falls when a series is dimmed. */
const strong = (page: import("@playwright/test").Page, testid: string) => pixelsAbove(page, testid, 150);

type Box = { x: number; y: number; width: number; height: number };

type Metric = (page: import("@playwright/test").Page, testid: string) => Promise<number>;

/** Move the pointer away and read the resting value. */
async function restValue(page: import("@playwright/test").Page, testid: string, metric: Metric) {
  await page.mouse.move(4, 4);
  await page.waitForTimeout(500); // let the entrance animation settle
  return metric(page, testid);
}

/**
 * Point somewhere on a chart and report the ink before and after.
 *
 * The `boundingBox()` is read *after* `scrollIntoViewIfNeeded()` and not before.
 * Reading it first was a real bug in the diagnostic this replaces: the box was
 * captured while the chart was still below the fold, so the pointer was moved to
 * coordinates that no longer held the element, and two charts reported
 * byte-identical "no change" results that looked like a finding.
 */
async function hoverAt(
  page: import("@playwright/test").Page,
  testid: string,
  aim: (b: Box) => [number, number],
  metric: Metric = ink,
) {
  const el = page.getByTestId(testid);
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  if (!box) throw new Error(`${testid} has no bounding box`);

  const rest = await restValue(page, testid, metric);
  const [x, y] = aim(box);
  await page.mouse.move(x, y);
  await page.waitForTimeout(500);
  const hovered = await metric(page, testid);

  return { rest, hovered };
}

const centre = (b: Box) => [b.x + b.width / 2, b.y + b.height / 2] as [number, number];

/**
 * A point on the donut's ring, not in its hole.
 *
 * The ring runs from 45% to 70% of the smaller dimension, centred at (50%, 45%);
 * aiming at the middle of the box hits the *hole*, which hovers nothing — and a
 * test that hovers nothing passes while proving nothing.
 */
const ring = (b: Box) => {
  const r = Math.min(b.width, b.height) / 2;
  return [b.x + b.width * 0.5, b.y + b.height * 0.45 - (0.45 * r + 0.7 * r) / 2] as [number, number];
};

test.beforeEach(async ({ page }) => {
  await login(page);
  await page.goto("/reports");
  await expect(page.getByTestId("report-net-worth")).toBeVisible();
  await page.waitForTimeout(800);
});

// Hovering the plot keeps every series drawn on the two axis charts, so their
// ink barely moves. The donut spotlights one slice, so its ink legitimately
// falls — its bound is what catches erasure, not a dim.
for (const [testid, name, aim, floor] of [
  ["net-worth-chart", "net worth", centre, 0.8],
  ["cash-flow-chart", "cash flow", centre, 0.8],
  ["spending-donut", "spending donut", ring, 0.25],
] as const) {
  test(`${name}: hovering does not erase the series`, async ({ page }) => {
    const { rest, hovered } = await hoverAt(page, testid, aim);
    expect(rest, "chart should draw something at rest").toBeGreaterThan(1000);
    expect(hovered, `${testid} lost ink on hover: ${rest} → ${hovered}`).toBeGreaterThan(
      rest * floor,
    );
  });
}

test("spending donut: hovering a slice spotlights it", async ({ page }) => {
  // Measured in full-strength pixels, not drawn ones: dimmed slices are still
  // drawn, so the spotlight is invisible to a pixel *count*. Without this the
  // suite would pass even if dimming silently stopped working and every slice
  // stayed at full strength — the one failure mode a count cannot see.
  const { rest, hovered } = await hoverAt(page, "spending-donut", ring, strong);
  expect(rest, "donut should draw solid slices at rest").toBeGreaterThan(1000);
  expect(hovered, `donut did not dim on hover: ${rest} → ${hovered}`).toBeLessThan(rest * 0.85);
});

test("cash flow: the legend spotlights a series without erasing the rest", async ({ page }) => {
  const el = page.getByTestId("cash-flow-chart");
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  if (!box) throw new Error("cash-flow-chart has no bounding box");
  const rest = await restValue(page, "cash-flow-chart", strong);

  // The legend is drawn on the canvas, so its entries have no DOM to target and
  // their x positions depend on the label widths. Sweeping the band and taking
  // the strongest response is what makes this robust to that: a fixed fraction
  // was tried first and quietly pointed at empty space, which meant the test
  // passed by hovering nothing.
  let dimmest = rest;
  for (let f = 0.3; f <= 0.8; f += 0.02) {
    await page.mouse.move(box.x + box.width * f, box.y + 10);
    await page.waitForTimeout(300);
    dimmest = Math.min(dimmest, await strong(page, "cash-flow-chart"));
  }

  // The legend must genuinely dim the other series. Measured at full strength,
  // because dimming leaves the pixels in place — see `pixelsAbove`.
  expect(dimmest, `legend hover dimmed nothing: stayed at ${rest}`).toBeLessThan(rest * 0.8);
});
