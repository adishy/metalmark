// Vitest setup: register jest-dom matchers and auto-clean the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
// `?raw` reads the real stylesheet, so this cannot drift from the palette.
//
// This import silently yields `""` unless `test.css` is enabled in vite.config.ts:
// vitest stubs CSS out by default, `?raw` included, so the import *succeeds* with
// no content and the only symptom is `token()` throwing "Unknown design token"
// from an unrelated test. See the note on `css: true` there.
import css from "../index.css?raw";

/**
 * The body of a top-level rule, e.g. everything inside `:root { … }`.
 *
 * Anchored to the start of a line rather than a bare `indexOf`: index.css's
 * header comment mentions `:root` in prose, so a plain search matches inside the
 * comment first and only lands on the real block because the next `{` happens to
 * be its opening brace. One brace in that prose and this would silently read
 * nothing — the same silent-empty failure the `css: true` note above describes.
 */
function blockFor(source: string, selector: string): string {
  const rule = new RegExp(`^${selector}\\s*\\{`, "m").exec(source);
  if (!rule) return "";
  const open = rule.index + rule[0].length - 1;
  const close = source.indexOf("}", open);
  return close === -1 ? "" : source.slice(open + 1, close);
}

/*
 * Seed the palette into the document.
 *
 * jsdom resolves custom properties that were set on the element, but it has no
 * cascade to read them from a stylesheet — and `token()` in theme/chartTokens.ts
 * throws on a missing variable, deliberately, so a typo cannot silently paint the
 * previous theme's palette in production (§2.9). Without this, that strictness
 * surfaces instead as every chart test failing for a reason unrelated to the test.
 *
 * Only the `:root` (light) values are applied. jsdom cannot switch on a class, so
 * dark-mode charts are covered by the Playwright specs, which run a real browser.
 */
for (const [, name, value] of blockFor(css, ":root").matchAll(/--([\w-]+):\s*([^;]+);/g)) {
  document.documentElement.style.setProperty(`--${name}`, value.trim());
}

afterEach(() => {
  cleanup();
});
