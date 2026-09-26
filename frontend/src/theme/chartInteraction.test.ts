import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import { BAR_RADIUS, barEndRadius, zeroRule } from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#111111", split: "#222222", label: "#333333", surface: "#444444", border: "#555555",
  fg: "#666666", accent: "#777777", positive: "#888888", negative: "#999999", warning: "#aaaaaa",
  series: [],
} as unknown as ChartTokens;

describe("barEndRadius", () => {
  it("rounds the end the value is at, in CSS corner order", () => {
    expect(barEndRadius("top")).toEqual([BAR_RADIUS, BAR_RADIUS, 0, 0]);
    expect(barEndRadius("bottom")).toEqual([0, 0, BAR_RADIUS, BAR_RADIUS]);
  });
});

describe("zeroRule", () => {
  const rule = zeroRule(T);

  it("rules the zero line at the axis colour, and nothing else", () => {
    expect(rule.data).toEqual([{ yAxis: 0 }]);
    expect(rule.lineStyle).toEqual({ color: T.axis, width: 1, type: "dashed" });
    // Not a series to be pointed at: no tooltip, no arrowheads, no label.
    expect(rule.silent).toBe(true);
    expect(rule.symbol).toBe("none");
    expect(rule.label).toEqual({ show: false });
  });
});

// The helpers above say what the option *asks* for; this says what ECharts
// *draws*. Both keys are honoured only if the library reads them — `borderRadius`
// lands on the zrender rect's `r`, and a `markLine` on a bar series is a mark
// line — and a canvas cannot be read back in a test. The SVG renderer runs
// headless and its output is plain nodes, so both can be checked as geometry.
describe("as ECharts draws them", () => {
  const draw = () => {
    const chart = echarts.init(null as never, null, {
      renderer: "svg",
      ssr: true,
      width: 310,
      height: 280,
    });
    chart.setOption({
      animation: false,
      grid: { top: 30, right: 8, bottom: 8, left: 8, containLabel: true },
      xAxis: { type: "category", data: ["Jul", "Aug", "Sep"] },
      yAxis: { type: "value" },
      series: [
        {
          name: "Income",
          type: "bar",
          stack: "cf",
          itemStyle: { color: T.positive, borderRadius: barEndRadius("top") },
          markLine: zeroRule(T),
          data: [5235, 5300, 5100],
        },
        {
          name: "Expense",
          type: "bar",
          stack: "cf",
          itemStyle: { color: T.negative, borderRadius: barEndRadius("bottom") },
          data: [-990, -1010, -950],
        },
      ],
    });
    const svg = chart.renderToSVGString();
    chart.dispose();
    return [...svg.matchAll(/<path\b([^>]*)\/?>(?:<\/path>)?/g)].map((m) => {
      const attrs = m[1];
      const at = (name: string) => new RegExp(`${name}="([^"]*)"`).exec(attrs)?.[1];
      return {
        d: at("d") ?? "",
        stroke: at("stroke") ?? "",
        dashed: /stroke-dasharray/.test(attrs),
        fill: at("fill") ?? "",
      };
    });
  };

  const paths = draw();

  it("draws both stack ends rounded, and leaves the joins square", () => {
    const bars = paths.filter((p) => p.fill === T.positive || p.fill === T.negative);
    expect(bars.length).toBeGreaterThan(1);
    // An arc means a rounded corner was actually built into the bar's path.
    expect(bars.filter((p) => /[Aa]/.test(p.d))).toHaveLength(bars.length);
    // One fill per segment, rounded on one end: four corners, two of them curved.
    const arcs = bars.map((p) => (p.d.match(/[Aa]/g) ?? []).length);
    expect(new Set(arcs)).toEqual(new Set([2]));
  });

  it("draws the zero rule across the plot, after the bars it divides", () => {
    const rules = paths.filter((p) => p.dashed);
    expect(rules).toHaveLength(1);
    const [rule] = rules;
    expect(rule.stroke).toBe(T.axis);
    // Horizontal, and spanning the plot rather than a mark's width.
    const [x0, y0, x1, y1] = rule.d.match(/-?\d+(\.\d+)?/g)!.map(Number);
    expect(y1).toBe(y0);
    expect(x1 - x0).toBeGreaterThan(200);
    // Above the data: the rule divides the two halves of each bar instead of
    // disappearing behind them.
    expect(paths.indexOf(rule)).toBeGreaterThan(
      paths.map((p) => p.fill === T.positive || p.fill === T.negative).lastIndexOf(true),
    );
  });
});
