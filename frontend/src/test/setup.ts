// Vitest setup: register jest-dom matchers and auto-clean the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
// `?raw` keeps this reading the real stylesheet rather than a copy of the
// palette. Importing the file normally would not help: Vite stubs CSS out of the
// test bundle, so the custom properties would never reach the document.
import css from "../index.css?raw";

/*
 * jsdom does not resolve CSS custom properties from stylesheets, and `token()`
 * in theme/chartTokens.ts throws on a missing variable — deliberately, so a typo
 * cannot silently paint the previous theme's palette in production (§2.9). Left
 * alone, that strictness would instead surface as every chart test failing for a
 * reason unrelated to the test, so the tokens are seeded here from index.css.
 *
 * Only the `:root` (light) values are applied. jsdom has no cascade to switch on
 * a class, so dark-mode charts are covered by the Playwright visual specs, which
 * run a real browser.
 */
function blockFor(source: string, selector: string): string {
  const at = source.indexOf(selector);
  if (at === -1) return "";
  const open = source.indexOf("{", at);
  const close = source.indexOf("}", open);
  return open === -1 || close === -1 ? "" : source.slice(open + 1, close);
}

for (const [, name, value] of blockFor(css, ":root").matchAll(/--([\w-]+):\s*([^;]+);/g)) {
  document.documentElement.style.setProperty(`--${name}`, value.trim());
}

afterEach(() => {
  cleanup();
});
