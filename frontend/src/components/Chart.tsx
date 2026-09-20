import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useTheme } from "@/theme/theme";

// Thin ECharts wrapper. Charts are lenses over the same filter model; each page
// passes a fully-formed option built from `useChartTokens()`.
//
// Two things this wrapper is responsible for, because a per-page chart would
// forget both:
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

  return (
    <div role="img" aria-label={label} data-testid={testid}>
      <ReactECharts
        key={resolved}
        option={option}
        style={{ height }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    </div>
  );
}
