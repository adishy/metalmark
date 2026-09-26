import { useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useReducedMotion } from "framer-motion";
import { useTheme } from "@/theme/theme";
import { chartMotion, type ChartBox } from "@/theme/chartInteraction";

// Thin ECharts wrapper. Charts are lenses over the same filter model; each page
// passes a fully-formed option built from `useChartTokens()` and the interaction
// helpers in `theme/chartInteraction.ts`.
//
// Three things this wrapper is responsible for, because a per-page chart would
// forget all three:
//
//   - `label` is required. Canvas is invisible to assistive technology, so the
//     wrapper carries `role="img"` and an accessible name. It must state the
//     *finding* ("Net worth rose to $12,340 in March"), not the chart type — a
//     screen reader user cannot see the shape being described. A chart is never
//     the only way to read a value (§2.9), so this complements a text rendering
//     rather than replacing one.
//   - `key={resolved}`. ECharts reads its colours once, at mount, into canvas
//     pixels — flipping the theme's CSS variables changes nothing it can see.
//     Re-mounting is what makes a theme switch repaint the chart.
//   - Motion. Merged here rather than in each option so no page has to remember
//     it, and so `prefers-reduced-motion` is honoured in one place. ECharts
//     animates in JS onto a canvas, which the CSS rule in index.css cannot
//     reach, so it has to be switched off explicitly (§2.8). A page that sets
//     its own `animation` still wins — this only supplies the default.
//
// `option` may be a function of the chart's own box. A chart has no viewport —
// only the width of the card it was put in, which is 310 px on a phone and 1104 px
// on a wide page for the *same* chart — so an option that has to place things in px
// takes the measurement from here, where the box is known, rather than guessing it
// from the window. Measured in a layout effect, before the first paint: a chart laid
// out for a width it does not have would draw, then jump to the one it does.
export default function Chart({
  option,
  label,
  height = 280,
  testid,
}: {
  option: EChartsOption | ((box: ChartBox) => EChartsOption);
  /** The finding, in a sentence. Read aloud in place of the canvas. */
  label: string;
  height?: number;
  testid?: string;
}) {
  const { resolved } = useTheme();
  const reduced = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(Math.round(el.getBoundingClientRect().width));
    measure();
    // A card changes width without the window doing anything — the two-up grid at
    // `lg:` re-pairs, a sidebar collapses — so the box is watched, not sampled once.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const sized = useMemo(() => ({ width, height }), [width, height]);
  const resolvedOption = useMemo(
    () => (typeof option === "function" ? option(sized) : option),
    [option, sized],
  );
  const merged = useMemo(
    () => ({ ...chartMotion(!!reduced), ...resolvedOption }),
    [reduced, resolvedOption],
  );

  return (
    // `touch-action: pan-y`: a finger dragging up and down over a chart scrolls
    // the page, as it does everywhere else; only a tap (or a sideways drag along
    // the axis) belongs to the chart. Without it a chart the width of the phone
    // was a dead zone the page could not be scrolled from.
    <div ref={box} role="img" aria-label={label} data-testid={testid} style={{ touchAction: "pan-y" }}>
      <ReactECharts
        key={resolved}
        option={merged}
        style={{ height, touchAction: "pan-y" }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    </div>
  );
}
