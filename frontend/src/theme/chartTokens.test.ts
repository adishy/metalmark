// The colour format `token()` emits is load-bearing, and this is the test that
// says so.
//
// zrender only reads its own colour syntax: it strips spaces and splits on
// commas. So `"rgb(71 85 105)"` — the CSS Color 4 form, and the natural thing to
// build from the "R G B" triplet the variables store — parses to `undefined`.
// Nothing visible goes wrong, because zrender hands the string to the canvas and
// the browser parses that happily. The damage is deferred: ECharts lifts a
// colour when it builds a default emphasis state, a lift over an unparseable
// colour yields `undefined`, and zrender then declines to paint that element at
// all. That is how the net-worth line's fill came to vanish on hover.
//
// So this asserts the format, not just that a colour came back — a string that
// starts with "rgb(" and looks right is exactly the failure mode.
import { describe, expect, it } from "vitest";
import { chartTokens, token } from "./chartTokens";

/** Comma-separated `rgb()`/`rgba()`, which is all zrender's parser accepts. */
const PARSEABLE = /^rgba?\(\d+, ?\d+, ?\d+(, ?[\d.]+)?\)$/;

describe("chart token colours", () => {
  it("emits only comma-separated colours zrender can parse", () => {
    for (const [name, value] of Object.entries(chartTokens())) {
      const colours = Array.isArray(value) ? value : [value];
      for (const colour of colours) {
        expect(colour, `${name} must be a parseable colour`).toMatch(PARSEABLE);
      }
    }
  });

  it("emits parseable colours with alpha too", () => {
    // The alpha path is a separate branch, and `rgb(71 85 105 / 0.15)` — the
    // spaced form with a slash — is unparseable for the same reason.
    expect(token("accent", 0.15)).toMatch(PARSEABLE);
    expect(token("accent", 0.15)).toContain("rgba(");
  });

  it("still fails loudly on a token that does not exist", () => {
    // The strictness that keeps a typo from silently painting the previous
    // theme's palette (§2.9). Asserted here so a future refactor of the format
    // cannot quietly drop it.
    expect(() => token("not-a-token")).toThrow(/Unknown design token/);
  });
});
