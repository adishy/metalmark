import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

// Thin ECharts wrapper with a dark theme baseline. Charts are lenses over the
// same filter model; each page passes a fully-formed option.
export default function Chart({
  option,
  height = 280,
  testid,
}: {
  option: EChartsOption;
  height?: number;
  testid?: string;
}) {
  return (
    <div data-testid={testid}>
      <ReactECharts
        option={option}
        style={{ height }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    </div>
  );
}
