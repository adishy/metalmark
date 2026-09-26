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

/**
 * Read a metric once the canvas has stopped changing.
 *
 * A chart is drawn *twice* on mount, and not by anything this app does: `echarts-
 * for-react` paints a temporary instance, and on its `finished` event — the end
 * of the entrance animation, roughly a second in — disposes it and builds the
 * real one, which animates in again from nothing. A fixed pause therefore
 * measures whichever animation happens to be running. Reading the donut 500 ms
 * after it scrolled into view reported 3,448 full-strength pixels, mid-animation,
 * where the settled chart has 23,025 — and the same race would let a `dimmest`
 * read from the legend sweep fall under its bound while proving nothing. Every
 * assertion here is about a state, "at rest" or "spotlit", so a reading has to
 * come from one: two consecutive equal counts 100 ms apart.
 */
async function settled(
  page: import("@playwright/test").Page,
  testid: string,
  metric: Metric,
  { tries = 30, gap = 100 } = {},
): Promise<number> {
  let last = await metric(page, testid);
  let stable = 0;
  for (let i = 0; i < tries; i++) {
    await page.waitForTimeout(gap);
    const value = await metric(page, testid);
    if (value === last) {
      if (++stable === 2) return value;
    } else {
      stable = 0;
      last = value;
    }
  }
  return last;
}

/** Move the pointer away and read the resting value. */
async function restValue(page: import("@playwright/test").Page, testid: string, metric: Metric) {
  await page.mouse.move(4, 4);
  return settled(page, testid, metric);
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
  const hovered = await settled(page, testid, metric);

  return { rest, hovered };
}

const centre = (b: Box) => [b.x + b.width / 2, b.y + b.height / 2] as [number, number];

/**
 * Where the brush band starts, as an x on the page.
 *
 * The band is drawn across the top of the canvas and begins where the plot does,
 * so the leftmost column of ink in the band's own rows *is* its edge, and the
 * handle is centred on it. Read from the canvas rather than assumed: the gutter
 * is a reserved width that follows the value labels, and a test that hardcoded
 * it would point at empty space the next time a label got wider — which is a
 * test that passes by dragging nothing.
 *
 * The band's height is `BRUSH_H` in `lib/netWorthChart.ts` (20 css px at the top
 * of the canvas); the row range is scaled to the canvas's backing store.
 */
async function bandLeft(
  page: import("@playwright/test").Page,
  testid: string,
): Promise<number> {
  const el = page.getByTestId(testid);
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  if (!box) throw new Error(`${testid} has no bounding box`);

  const found = await el.locator("canvas").first().evaluate((c: HTMLCanvasElement) => {
    const ctx = c.getContext("2d");
    if (!ctx) return null;
    const rows = Math.max(1, Math.round((20 / c.clientHeight) * c.height));
    const { data } = ctx.getImageData(0, 0, c.width, rows);
    for (let x = 0; x < c.width; x++) {
      for (let y = 0; y < rows; y++) {
        if (data[(y * c.width + x) * 4 + 3] > 8) return { x, width: c.width };
      }
    }
    return null;
  });
  if (!found) throw new Error(`${testid} drew no brush band`);
  return box.x + (found.x / found.width) * box.width;
}

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
  await page.goto("/insights/overview");
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
  // the strongest response is what makes that robust: a fixed fraction was tried
  // first and quietly pointed at empty space, which meant the test passed by
  // hovering nothing. Each step reads a *settled* count, so the sweep cannot
  // pick up a mid-animation frame and report a dim that never happened.
  let dimmest = rest;
  for (let f = 0.3; f <= 0.8; f += 0.02) {
    await page.mouse.move(box.x + box.width * f, box.y + 10);
    dimmest = Math.min(dimmest, await settled(page, "cash-flow-chart", strong, { tries: 12 }));
  }

  // The legend must genuinely dim the other series. Measured at full strength,
  // because dimming leaves the pixels in place — see `pixelsAbove`.
  expect(dimmest, `legend hover dimmed nothing: stayed at ${rest}`).toBeLessThan(rest * 0.8);
});

/**
 * The range brush has to re-scope the whole card, not just the picture.
 *
 * The window lives inside the ECharts instance (a drag is the library's state,
 * not React's), so the headline above the chart is what makes it real: it has to
 * name the same days the plot is showing. A brush that moved the plot while the
 * headline went on stating "over 3 months" is the failure this exists for — and
 * the range button is how a reader gives the window back, which only works if a
 * click on it also clears the window (the chart is re-mounted for it; see the
 * `brushReset` epoch in `Accounts.tsx`).
 */
test("net worth: the range brush re-scopes the headline, and the range gives it back", async ({
  page,
}) => {
  await page.goto("/accounts");
  const chart = page.getByTestId("accounts-net-worth-chart");
  await chart.scrollIntoViewIfNeeded();
  await expect(chart).toBeVisible();
  await page.waitForTimeout(1600); // the entry animation, then quiet
  const box = await chart.boundingBox();
  if (!box) throw new Error("accounts-net-worth-chart has no bounding box");

  const headline = page.getByTestId("net-worth-change");
  const whole = await headline.textContent();
  // The card opens on the fortnight the issue asks for, and says so.
  expect(whole, "the card should start on its default window").toContain("over the past 2 weeks");

  // The brush is the band across the top of the canvas. The window opens on the
  // whole range, where a *translation* has nowhere to go — so the gesture that
  // narrows it is a pull on the left handle, which sits on the band's edge.
  const band = box.y + 10;
  const from = await bandLeft(page, "accounts-net-worth-chart");
  await page.mouse.move(from + 3, band);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, band, { steps: 12 });
  await page.mouse.up();

  const brushed = await headline.textContent();
  expect(brushed, "the brush did not narrow the window").not.toBe(whole);
  // The window's own words: two named days, in the same sentence the range uses.
  expect(brushed).toMatch(/ from [A-Z][a-z]{2} \d{2} to [A-Z][a-z]{2} \d{2}$/);

  // Choosing the range it is already on is how a reader takes the window back,
  // and it has to restore the headline the card opened with.
  await page.getByTestId("net-worth-range-3m").click();
  await expect(headline).toContainText("over 3 months");

  // A drag across the plot is *not* a brush: that is where the pointer reads a
  // point, which is the gesture the tooltip and tap-to-tooltip are built on.
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5, { steps: 12 });
  await page.mouse.up();
  await expect(headline).toContainText("over 3 months");
});

/**
 * The window the hero opens on, in the reader's hands.
 *
 * Two things the card promises. A shorter window is a shorter *request*, not a
 * shorter label over the same series — the headline states the period it computed
 * over, and the series has to start where that period does. And the choice is the
 * reader's on their next visit: "networth chart at top should have more varied
 * filters … but save / remember if the user selects something else".
 *
 * The window's *start* is read off the chart rather than assumed: hovering the
 * left end of the plot gives back the first point's own day, in the app's own
 * words, which is the one number that tells a fortnight from a week.
 */
test("net worth: the window is what the reader chose, and it is remembered", async ({ page }) => {
  // The app prints a day as `<Mon> <DD>` (`formatDay`, medium), built from its own
  // month names — so this can be computed rather than pattern-matched.
  const MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const said = (back: number) => {
    const d = new Date();
    d.setDate(d.getDate() - back);
    return `${MONTHS[d.getMonth()]} ${String(d.getDate()).padStart(2, "0")}`;
  };

  const chart = page.getByTestId("accounts-net-worth-chart");
  const headline = page.getByTestId("net-worth-change");
  const tooltip = chart.locator(".mm-chart-tooltip");

  /** The day of the first point the plot is drawing, via the hover readout. */
  const firstDay = async (): Promise<string> => {
    await chart.scrollIntoViewIfNeeded();
    const box = await chart.boundingBox();
    if (!box) throw new Error("accounts-net-worth-chart has no bounding box");
    // The left end of the plot is the first point; the pointer is moved off and
    // back so a window change re-opens the tooltip rather than leaving the old
    // one standing.
    await page.mouse.move(box.x + box.width * 0.9, box.y + box.height * 0.5);
    await page.mouse.move(box.x + 72, box.y + box.height * 0.5);
    await expect.poll(
      async () => (await tooltip.innerText().catch(() => "")).split("\n")[0]?.trim() ?? "",
      { timeout: 15_000, message: "the net-worth tooltip never opened" },
    ).not.toBe("");
    return (await tooltip.innerText()).split("\n")[0]!.trim();
  };

  await page.goto("/accounts");
  await expect(chart).toBeVisible();
  await page.waitForTimeout(1600);

  // The default is the fortnight the reader asked to open on.
  await expect(page.getByTestId("net-worth-range-2w")).toHaveAttribute("aria-pressed", "true");
  await expect(headline).toContainText("over the past 2 weeks");
  await expect.poll(firstDay, { timeout: 15_000 }).toBe(said(14));

  await page.getByTestId("net-worth-range-1w").click();
  await expect(headline).toContainText("over the past week");
  await expect(page.getByTestId("net-worth-range-1w")).toHaveAttribute("aria-pressed", "true");
  // The line re-scoped, not just its label: the leftmost point is a week back.
  await expect.poll(firstDay, { timeout: 15_000 }).toBe(said(7));

  // A new visit, in the same browser: the window is read back, not re-defaulted.
  await page.reload();
  await expect(page.getByTestId("net-worth-range-1w")).toHaveAttribute("aria-pressed", "true");
  await expect(headline).toContainText("over the past week");
});

/**
 * A chart's entry animation plays **once**.
 *
 * The complaint this pins: "charts blink twice when it loads instead of loading
 * a smooth animation". It was real, and `Chart.tsx` carries the trace — the box
 * is measured one render after the chart mounts, the option is a function of that
 * measurement, and the library's `echarts.init` is asynchronous, so the second
 * option landed on the throwaway instance `echarts-for-react` builds and discards.
 * That instance's animation *finished* was the signal to dispose it, so the real
 * instance then animated the same data in from nothing: two complete entry
 * animations, and a canvas wiped clean between them.
 *
 * Counting animations is not something the option can be asked about, so this
 * measures the symptom directly. `pixelsAbove` already reads "how much chart is on
 * screen" for the hover tests; sampled frame by frame through the entrance, the
 * defective version is unmistakable — ink climbs to the full 35,785 px, falls to
 * 1,788 (5% of the peak) and climbs again. A single animation is monotonic until
 * it settles.
 *
 * The recorder is installed with `addInitScript`, so it is running before the app
 * renders and catches the very first frame the canvas exists. Reading `max` from
 * the same samples is what makes the bound honest: the peak is this run's own peak,
 * not a constant that a different viewport or dataset would invalidate.
 */
test("net worth: the entry animation plays once, with no blink", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __inkSamples: number[] };
    w.__inkSamples = [];
    const tick = () => {
      const canvas = document.querySelector<HTMLCanvasElement>(
        '[data-testid="accounts-net-worth-chart"] canvas',
      );
      if (canvas && canvas.width > 0) {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
          let drawn = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i] > 8) drawn++;
          w.__inkSamples.push(drawn);
        }
      }
      if (w.__inkSamples.length < 400) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  await page.goto("/accounts");
  await expect(page.getByTestId("accounts-net-worth-chart")).toBeVisible();
  await page.waitForTimeout(2500);

  const samples = await page.evaluate(
    () => (window as unknown as { __inkSamples: number[] }).__inkSamples,
  );
  expect(samples.length, "no frame of the chart was sampled").toBeGreaterThan(10);

  const peak = Math.max(...samples);
  expect(peak, "the chart never drew anything").toBeGreaterThan(1000);
  const peakAt = samples.indexOf(peak);
  expect(peakAt, "the chart was already at rest when sampling began").toBeGreaterThan(0);

  // Everything from the peak on. An entry animation ramps up and stays there; a
  // *second* animation ramps up, is wiped, and ramps up again — so the samples
  // after the first peak are where the two are told apart. Bounded below the peak
  // rather than above zero, because the ramp's own early frames are legitimately
  // near empty and a floor of "> 0" would pass on a chart that never drew at all.
  const floor = Math.min(...samples.slice(peakAt));
  expect(
    floor,
    `the chart was wiped and redrawn after it had finished loading — the peak was ` +
      `${peak} px and the canvas later fell to ${floor} (the entry animation ran twice)`,
  ).toBeGreaterThan(peak * 0.9);
});
