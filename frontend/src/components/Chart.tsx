import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useReducedMotion } from "framer-motion";
import { useTheme } from "@/theme/theme";
import { chartMotion } from "@/theme/chartInteraction";

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
export default function Chart({
  option,
  label,
  height = 280,
  testid,
}: {
  option: EChartsOption;
  /** The finding, in a sentence. Read aloud in place of the canvas. */
  label: string;
  height?: number;
  testid?: string;
}) {
  const { resolved } = useTheme();
  const reduced = useReducedMotion();
  const merged = useMemo(
    () => ({ ...chartMotion(!!reduced), ...option }),
    [reduced, option],
  );

  return (
    // `touch-action: pan-y`: a finger dragging up and down over a chart scrolls
    // the page, as it does everywhere else; only a tap (or a sideways drag along
    // the axis) belongs to the chart. Without it a chart the width of the phone
    // was a dead zone the page could not be scrolled from.
    <div role="img" aria-label={label} data-testid={testid} style={{ touchAction: "pan-y" }}>
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
