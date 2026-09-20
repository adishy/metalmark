// How a chart responds to being pointed at.
//
// This exists because each chart was assembling its own option object inline,
// and none of them defined any interaction state at all. That left ECharts to
// apply its default emphasis, and the net-worth line fell apart under the
// pointer: the fill vanished while the stroke stayed, so the chart read as
// "blinking away" rather than highlighting.
//
// The underlying cause turned out to be in `chartTokens.ts`, not here: `token()`
// emitted the CSS Color 4 form `rgb(71 85 105)`, which zrender's parser cannot
// read. Painting hid it (zrender hands the string to the canvas and the browser
// parses it), but ECharts *lifts* a colour when it builds a default emphasis
// state, and a lift over an unparseable colour returns `undefined` — which
// ECharts writes into the style, after which zrender declines to paint the
// element. That is why the fill went and the stroke stayed, and why
// `emphasis: {disabled: true}` "fixed" it by removing hover feedback entirely.
//
// So the colours below are stated explicitly even though the token format is now
// fixed. That is deliberate: relying on the auto-lift means a data refresh while
// an element is hovered captures the *lifted* colour as the new resting colour,
// and the hue walks brighter on every interaction. Explicit costs a line and
// cannot drift.
//
// Colours come from `chartTokens()`; nothing here names a colour of its own.
// See DESIGN.md §2.9 (chart colour) and §2.10 (chart interaction).
import type { EChartsOption } from "echarts";
import type { ChartTokens } from "@/theme/chartTokens";

/**
 * How far a non-hovered series fades when another is spotlighted.
 *
 * Deliberately not ECharts' default, which is low enough that a dimmed series
 * reads as *absent* — the same complaint as the vanishing line, arrived at from
 * the other direction. Dimming has to stay legible as "this is still here, it is
 * just not the one you are asking about".
 */
export const DIM = 0.3;

/** The area fill's resting density, shared so emphasis cannot drift from it. */
export const AREA_OPACITY = 0.15;

/**
 * A fill is already faint at rest, so dimming it by the same ratio as a stroke
 * sends it to invisible — which is the failure this module exists to prevent.
 * Floors the dimmed fill at something that still reads as present.
 */
const DIMMED_FILL = Math.max(AREA_OPACITY * DIM, 0.06);

/**
 * The only motion a chart is allowed, using the same vocabulary as the rest of
 * the app (§2.8): 200ms to arrive, 120ms to change state, easing out.
 */
export const CHART_MOTION = { enter: 200, state: 120, easing: "cubicOut" } as const;

/**
 * Motion defaults, merged into every option by `Chart.tsx`.
 *
 * `stateAnimation` is the one that is easy to miss: the emphasis/blur fade is
 * driven by it and *not* by `animationDurationUpdate`, and its global default is
 * 300ms — over the 120ms §2.8 allows for a state change. Without setting it
 * explicitly the hover fade quietly runs long.
 *
 * `reduced` comes from `useReducedMotion()`, which is `boolean | null`. The
 * `prefers-reduced-motion` block in index.css shortens CSS animations and
 * transitions, and ECharts does neither — it animates in JavaScript onto a
 * canvas, which no CSS rule can reach. That is why index.css:129 says JS-driven
 * animation has to be told explicitly; Dialog.tsx and Review.tsx do it for
 * framer-motion, and this is the third and last source of animation in the app.
 * `animation: false` covers the state transition too: ECharts gates the state
 * fade on the same flag, so one switch turns all of it off.
 */
export function chartMotion(reduced: boolean | null): EChartsOption {
  if (reduced) return { animation: false, stateAnimation: { duration: 0 } };
  return {
    animation: true,
    animationDuration: CHART_MOTION.enter,
    animationDurationUpdate: CHART_MOTION.state,
    animationEasing: CHART_MOTION.easing,
    animationEasingUpdate: CHART_MOTION.easing,
    stateAnimation: { duration: CHART_MOTION.state, easing: CHART_MOTION.easing },
  };
}

/**
 * The tooltip, and the pointer that anchors it.
 *
 * `confine` keeps it inside the canvas: without it a tooltip on a phone-width
 * chart overhangs the card it belongs to.
 */
export function chartTooltip(
  t: ChartTokens,
  { trigger = "axis", formatter }: { trigger?: "axis" | "item"; formatter?: string } = {},
): NonNullable<EChartsOption["tooltip"]> {
  return {
    trigger,
    confine: true,
    backgroundColor: t.surface,
    borderColor: t.border,
    textStyle: { color: t.fg },
    ...(formatter ? { formatter } : {}),
    // An item-triggered tooltip has no axis to point at.
    ...(trigger === "axis"
      ? {
          axisPointer: {
            type: "line" as const,
            lineStyle: { color: t.axis, width: 1, type: "dashed" as const },
            // The chip that rides the pointer, showing the value under it.
            //
            // `show: true` is required, and not obvious: ECharts only defaults
            // this to true for a `cross` pointer, so on a `line` pointer the chip
            // is off and every key below is dead styling. Without it the dashed
            // line has no label and you have to read the axis to know where it is.
            label: {
              show: true,
              formatter: "{value}",
              backgroundColor: t.surface,
              borderColor: t.border,
              borderWidth: 1,
              // The default is `'auto'` — a fixed slate with white text, which
              // is below the §7 contrast floor in light mode.
              color: t.fg,
            },
          },
        }
      : {}),
  };
}

/** Axis styling. `grid` adds the horizontal rules, so it is for the value axis. */
export function chartAxis(t: ChartTokens, { grid = false } = {}) {
  return {
    axisLine: { lineStyle: { color: t.axis } },
    axisLabel: { color: t.label },
    ...(grid ? { splitLine: { lineStyle: { color: t.split } } } : {}),
  };
}

/**
 * The legend.
 *
 * `inactiveColor` applies to a toggled-off entry's label *and* its icon, so it
 * is a text colour as much as a swatch — which rules out the obvious choice of
 * the dimmest grey in the palette (`border-strong` is ~4.3:1 on white, under the
 * §7 floor). Using the muted text token instead keeps the label readable and
 * still signals "off", because the icon drops from its series colour to that
 * grey. The signal is carried by the swatch, the legibility by the text.
 */
export function chartLegend(t: ChartTokens, extra: Record<string, unknown> = {}) {
  return {
    textStyle: { color: t.label },
    inactiveColor: t.label,
    ...extra,
  };
}

function areaStyleFor(c: string, opacity: number) {
  return { color: c, opacity };
}

/**
 * The resting area fill for a line series. Use this rather than writing the
 * `areaStyle` inline, so the resting and emphasised fills are the same colour by
 * construction instead of by two people remembering.
 */
export function chartArea(t: ChartTokens, color?: string) {
  return areaStyleFor(color ?? t.accent, AREA_OPACITY);
}

/**
 * Spotlight the hovered line, dim the rest.
 *
 * `scale: 1` is deliberate. ECharts grows a hovered symbol by a size-dependent
 * ratio when `emphasis.scale` is unset — at the default `symbolSize: 4` that is
 * a 50% pop — so the same chart animates differently at different sizes. Pinning
 * it keeps hover about the spotlight rather than the symbol.
 */
export function emphasisLine(t: ChartTokens, { area = false, color }: { area?: boolean; color?: string } = {}) {
  const c = color ?? t.accent;
  return {
    focus: "series" as const,
    scale: 1,
    blur: {
      lineStyle: { opacity: DIM },
      itemStyle: { opacity: DIM },
      ...(area ? { areaStyle: areaStyleFor(c, DIMMED_FILL) } : {}),
    },
    lineStyle: { color: c, width: 2.5 },
    itemStyle: { color: c, borderColor: t.surface, borderWidth: 2 },
    ...(area ? { areaStyle: areaStyleFor(c, AREA_OPACITY) } : {}),
  };
}

/**
 * Spotlight the hovered bar, dim the rest.
 *
 * `color` is required. It defaults to the accent only in the type signature's
 * absence — a caller who forgets it gets teal bars on every stack, which looks
 * deliberate and is wrong. Making it an argument means the compiler asks.
 */
export function emphasisBar(t: ChartTokens, color: string) {
  return {
    focus: "series" as const,
    blur: { itemStyle: { opacity: DIM } },
    itemStyle: { color, borderColor: t.surface, borderWidth: 1 },
  };
}

/**
 * Spotlight the hovered slice, dim the rest.
 *
 * The label dims with its slice: a slice at 30% under a full-strength caption
 * reads as a labelling bug rather than a highlight. The leader line has to be
 * dimmed alongside the label, because it is styled from its own state model — a
 * dimmed caption on a full-strength leader reads as a rendering fault.
 */
export function emphasisPie(t: ChartTokens) {
  return {
    // `self`, not `series`: a donut is ONE series whose slices are data items,
    // so `series` has nothing to blur and hovering dims nothing at all. `self`
    // blurs the item's siblings in the same series, which is the slice
    // spotlighting the other charts get from `series`.
    focus: "self" as const,
    blur: {
      itemStyle: { opacity: DIM },
      label: { opacity: DIM },
      labelLine: { lineStyle: { opacity: DIM } },
    },
    itemStyle: { borderColor: t.surface, borderWidth: 2 },
    scale: true,
    scaleSize: 4,
  };
}
