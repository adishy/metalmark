import { test, expect, type Page } from "@playwright/test";
import { login } from "./helpers";

// The command palette (issue #35), end to end: open it, type, press Enter, and
// be on the page.
//
// The component tests in `src/components/CommandPalette.test.tsx` and
// `AppShell.test.tsx` own the parts a browser cannot reach — the combobox
// attributes, the live region's wording, the admin filter, focus returning to
// the control the shortcut interrupted. What only a browser can settle is here,
// and it is not a formality in either direction:
//
//   * the palette is a `lazy()` chunk behind a `Suspense` boundary, so "does it
//     actually open" is a question about a real dynamic import;
//   * the desktop gate is half CSS (`hidden … lg:inline-flex`) and half
//     `matchMedia`, and jsdom applies no CSS at all — so "a phone has no way to
//     open it" is a claim only this file can make.

/** The palette is opened with the shortcut — the entry point a keyboard user
 *  has, and the one the trigger's `kbd` advertises.
 *
 *  `Control+k`, not `Meta+k`: the shortcut handler accepts either, and this
 *  suite runs Chromium on Linux, where `MOD` in the shell prints "Ctrl".
 *
 *  Retried, because the listener is added in an effect *after* the shell's first
 *  paint: login resolves on the nav being visible, and a press inside those few
 *  milliseconds lands before anything is listening (measured — it is the flake
 *  this file had). The retry is guarded, so it cannot toggle a palette that did
 *  open, and it is bounded, so a shortcut that stopped working still fails. */
async function openWithShortcut(page: Page): Promise<void> {
  const input = page.getByTestId("palette-input");
  await expect(async () => {
    if (!(await input.isVisible())) await page.keyboard.press("Control+k");
    await expect(input).toBeVisible({ timeout: 4000 });
  }).toPass({ timeout: 20_000 });
}

test("Cmd/Ctrl+K opens it, typing filters it, Enter lands on the page", async ({ page }) => {
  await login(page);
  await openWithShortcut(page);

  // The list is a combobox and it is the field that has focus, so the letters go
  // into the query rather than anywhere else — which is the whole gesture.
  await expect(page.getByTestId("palette-input")).toBeFocused();
  const input = page.getByTestId("palette-input");
  await input.fill("review");
  const row = page.getByTestId("palette-option-nav-review");
  await expect(row).toBeVisible();
  await expect(page.getByTestId("palette-option-nav-accounts")).toBeHidden();

  // The rows are targets (§7's 44 px floor). `a11y.spec.ts` sweeps every
  // control on every page, and the palette is not on the page until it is
  // opened, so this is the only place that floor is measured on a row.
  const box = await row.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/review$/);
  await expect(page.getByTestId("nav-review")).toHaveAttribute("aria-current", "page");
  // Closed on the way through, not left over the page it navigated to.
  await expect(page.getByTestId("command-palette")).toBeHidden();
});

test("the arrow keys choose which match Enter takes", async ({ page }) => {
  // "insights" answers with three destinations — the section and its two tabs —
  // and the difference between them is one arrow key. Without this the first
  // match is the only one Enter could ever reach, which is the same as having no
  // list at all.
  await login(page);
  await openWithShortcut(page);
  await page.getByTestId("palette-input").fill("insights");
  await expect(page.getByTestId("palette-option-nav-insights")).toBeVisible();

  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/insights\/overview$/);
  await expect(page.getByTestId("insights-panel-overview")).toBeVisible();
});

test("a Settings section is a destination, and arrives on that section", async ({ page }) => {
  // The eleven sections are not routes; the palette reaches one by way of the
  // URL state Settings now holds its tab in. This is the end of that path.
  await login(page);
  await openWithShortcut(page);
  await page.getByTestId("palette-input").fill("owners");
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/settings\?tab=owners$/);
  await expect(page.getByTestId("settings-panel-owners")).toBeVisible();
});

test("Escape closes it and gives the keyboard back where it was", async ({ page }) => {
  // Opened from the trigger rather than the shortcut, because the requirement is
  // about *where focus was*: it goes back to the control the user was on, or the
  // palette is a trap whose door only opens inward.
  await login(page);
  const trigger = page.getByTestId("palette-open");
  await trigger.click();
  await expect(page.getByTestId("palette-input")).toBeFocused();

  await page.keyboard.press("Escape");

  await expect(page.getByTestId("command-palette")).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("the “g then x” chords reach a section without the palette", async ({ page }) => {
  // Optional in the brief, and the reason it is worth a browser: the chord is a
  // pair of *plain* keypresses, so the only question that matters is whether
  // anything between the two keys eats one. Two chords, so a table that fires
  // its first entry only is caught.
  await login(page);
  await expect(page.getByTestId("nav-accounts")).toHaveAttribute("aria-current", "page");

  await page.keyboard.press("g");
  await page.keyboard.press("t");
  await expect(page).toHaveURL(/\/transactions$/);

  await page.keyboard.press("g");
  await page.keyboard.press("r");
  await expect(page).toHaveURL(/\/review$/);
});

test.describe("phone", () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true });

  test("has no way to open the palette, and nothing to scroll sideways", async ({ page }) => {
    await login(page);

    // Not "no room for it": `display: none` takes the trigger out of the tab
    // order and the accessibility tree, so there is no control to reach — which
    // is the honest version of desktop-only, and the one jsdom cannot check.
    await expect(page.getByTestId("palette-open")).toBeHidden();

    // And the shortcut does nothing, because the handler is never registered at
    // this width: no bottom-sheet version of this exists (§9.6).
    await page.keyboard.press("Control+k");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);

    // Nothing was added to the page that could push it past the edge.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the phone scrolls sideways").toBeLessThanOrEqual(1);
  });
});
