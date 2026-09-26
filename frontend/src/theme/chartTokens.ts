// Chart colours.
//
// ECharts takes concrete colour strings, not classes, so it is the one consumer
// that cannot use the Tailwind token utilities. Rather than restate the palette
// here, this reads the same CSS custom properties those tokens are built from —
// so index.css stays the single source of truth and a theme flip needs no second
// list kept in step. (Overview.tsx (formerly Reports.tsx) previously hardcoded `#475569`, `#1e293b`,
// `#94a3b8` and `#0f172a`, which is why its charts ignored the theme entirely.)
import { useMemo } from "react";
import { useTheme } from "@/theme/theme";

/**
 * Resolve a `--token` to a real colour. The variables are stored as "R G B"
 * triplets so Tailwind's opacity modifiers work; ECharts has no such concept,
 * hence `alpha` here.
 *
 * **The output must be comma-separated**, and that is not a style preference.
 * zrender only parses its own colour syntax: `color.parse` strips spaces and
 * splits on commas, so `"rgb(71 85 105)"` — the CSS Color 4 form, and the
 * obvious thing to emit from an "R G B" triplet — parses to `undefined`, as does
 * anything built on it such as `lift()`:
 *
 *     parse("rgb(71 85 105)")   -> undefined
 *     parse("rgb(71,85,105)")   -> [71, 85, 105, 1]
 *
 * Painting still *looks* fine, because zrender hands the string to the canvas
 * and the browser parses it. The failure is silent and delayed: anything that
 * needs the numeric components gets nothing. ECharts lifts colours when it
 * builds a default emphasis state, so an emphasis that leaves a fill or stroke
 * unspecified gets `undefined` written into it, and zrender then declines to
 * paint that element at all — which is how the net-worth line's fill came to
 * disappear on hover while its stroke stayed.
 */
export function token(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${name}`)
    .trim();
  // This check is the whole point of the function. `getPropertyValue` returns ""
  // for a name that does not exist, and an empty string interpolates into an
  // invalid colour that ECharts silently ignores — so a typo paints the previous
  // theme's palette with no error anywhere. Throwing turns a silent wrong colour
  // into a stack trace.
  if (!raw) throw new Error(`Unknown design token: --${name}`);
  const [r, g, b] = raw.split(/\s+/).map(Number);
  return alpha === 1 ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Every colour a chart option may use. Series colours are looked up in order, so
 * `slice(0, n)` is the palette for an n-category chart.
 *
 * The ten series slots are tokens, not a hardcoded table: index.css defines them
 * per theme (`--chart-1` … `--chart-10`), which is what lets the light-mode
 * palette be the 600 weights that clear 3:1 on white while dark uses the 400s.
 */
export const chartTokens = () => ({
  /** Axis line and ticks. A control boundary, so it clears 3:1. */
  axis: token("border-strong"),
  /** Grid lines. Decorative, so a lighter value than the axis. */
  split: token("border"),
  /** Axis and legend text. */
  label: token("fg-muted"),
  /** Card and tooltip background — charts sit on raised surfaces. */
  surface: token("surface-raised"),
  border: token("border"),
  /** Tooltip body text. */
  fg: token("fg"),
  accent: token("accent"),
  positive: token("positive"),
  negative: token("negative"),
  warning: token("warning"),
  series: Array.from({ length: 10 }, (_, i) => token(`chart-${i + 1}`)),
});

export type ChartTokens = ReturnType<typeof chartTokens>;

/**
 * Read the tokens for the current theme.
 *
 * Keyed on `resolved`, not `choice`: `apply()` in theme.ts flips the class on
 * <html> *before* it notifies React, so by the time this runs the computed
 * values are already the new theme's. Reading them any earlier returns the
 * previous palette, which is the subtle failure this memo exists to avoid.
 */
export function useChartTokens(): ChartTokens {
  const { resolved } = useTheme();
  return useMemo(() => chartTokens(), [resolved]);
}
