import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import {
  BAR_RADIUS,
  barEndRadius,
  brushEvents,
  chartAxis,
  DIM,
  emphasisStrip,
  emphasisTreemap,
  linkedPointer,
  pointerLineOnly,
  rangeBrush,
  valueTicks,
  zeroRule,
  zoomWindow,
} from "@/theme/chartInteraction";
import { token } from "@/theme/chartTokens";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#111111", split: "#222222", label: "#333333", surface: "#444444", border: "#555555",
  fg: "#666666", accent: "#777777", positive: "#888888", negative: "#999999", warning: "#aaaaaa",
  series: [],
} as unknown as ChartTokens;

describe("rangeBrush", () => {
  const brush = rangeBrush(T, { top: 0, height: 20 });

  it("is a slider across both grids, not a drag on the plot", () => {
    expect(brush.type).toBe("slider");
    expect(brush.top).toBe(0);
    expect(brush.height).toBe(20);
    // A chart with a strip under its line has two x axes, and a brush that moved
    // only the first would slide the line's days while the bars stayed put.
    expect(brush.xAxisIndex).toEqual([0, 1]);
    // `inside` would make a drag across the plot zoom instead of reading a point,
    // which is the gesture tap-to-tooltip is built on.
    expect(brush.type).not.toBe("inside");
  });

  it("tints the window from the accent, and states its own readout nowhere", () => {
    expect(brush.fillerColor).toBe(token("accent", 0.16));
    expect(brush.backgroundColor).toBe("transparent");
    expect(brush.borderColor).toBe(T.border);
    // The window is named by the axis under the strip, in days; the handle's own
    // readout would print a raw epoch over the thing being dragged.
    expect(brush.showDetail).toBe(false);
    expect(brush.brushSelect).toBe(false);
  });
});

describe("the pointer across two grids", () => {
  it("links every x axis so the crosshair crosses the strip", () => {
    expect(linkedPointer().axisPointer.link).toEqual([{ xAxisIndex: "all" }]);
  });

  it("keeps the chip off the grid whose dates are stated below it", () => {
    expect(pointerLineOnly().axisPointer.label).toEqual({ show: false });
  });
});

describe("zoomWindow", () => {
  it("reads the slider's own payload: two percentages", () => {
    expect(zoomWindow({ type: "dataZoom", start: 12, end: 88 })).toEqual([12, 88]);
  });

  it("reads a batch, and gives up rather than guessing", () => {
    expect(zoomWindow({ batch: [{ start: 0, end: 50 }] })).toEqual([0, 50]);
    expect(zoomWindow([{ start: 25, end: 75 }])).toEqual([25, 75]);
    for (const nothing of [null, undefined, {}, { start: 10 }, { start: "10", end: 20 }, 7, []]) {
      expect(zoomWindow(nothing)).toBeNull();
    }
  });

  // The event *name* is part of the module's vocabulary for the same reason the
  // tooltip's keys are: two charts must not listen for two spellings of it.
  it("hands the handler the window, under the name ECharts sends", () => {
    const seen: ([number, number] | null)[] = [];
    const map = brushEvents((window) => seen.push(window));
    expect(Object.keys(map)).toEqual(["dataZoom"]);
    map.dataZoom({ start: 10, end: 60 });
    expect(seen).toEqual([[10, 60]]);
  });

  // The window is the instance's state, so what a drag changes about the picture
  // is merged into the chart the event came from — which the handler is given.
  it("hands the handler the chart the event came from, when it is one", () => {
    const charts: unknown[] = [];
    const map = brushEvents((_, chart) => charts.push(chart));
    const chart = { setOption: () => {} };
    map.dataZoom({ start: 10, end: 60 }, chart);
    map.dataZoom({ start: 10, end: 60 });
    map.dataZoom({ start: 10, end: 60 }, { notAChart: true });
    expect(charts).toEqual([chart, null, null]);
  });
});

describe("emphasisStrip", () => {
  it("has no highlight of its own, and still dims under a spotlight", () => {
    // Its bars are coloured by their own sign, and the bar helper states one
    // colour for a whole series: highlighting a rise with it would repaint the
    // rise as a fall.
    expect(emphasisStrip().disabled).toBe(true);
    expect(emphasisStrip().blur.itemStyle.opacity).toBe(DIM);
  });
});

describe("emphasisTreemap", () => {
  it("spotlights one tile of the single series, dimming the tiles and their captions", () => {
    expect(emphasisTreemap()).toEqual({
      // `self`, not `series`: a treemap is one series whose tiles are data
      // items, so `series` has no sibling to blur — the same reason the pie's
      // is `self`.
      focus: "self",
      blur: {
        itemStyle: { opacity: DIM },
        // A caption at full ink on a dimmed tile reads as a labelling bug.
        label: { opacity: DIM },
      },
    });
  });
});

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

describe("valueTicks", () => {
  it("asks a short canvas for fewer rules, and never asks for a hatch", () => {
    expect(valueTicks({ width: 310, height: 280 })).toBe(4);
    expect(valueTicks({ width: 296, height: 220 })).toBe(3);
    // A canvas that has not been laid out yet is short, not tall.
    expect(valueTicks({ width: 310, height: 0 })).toBe(3);
    // Whatever the height, the ask stays in the band that draws readable rules —
    // the numbers are a request to ECharts, whose nice-number search may return a
    // tick or two more, so the band is what keeps a tall canvas from asking for
    // the eight-rule hatch this exists to remove.
    for (const height of [140, 220, 280, 360, 700]) {
      expect(valueTicks({ width: 310, height })).toBeGreaterThanOrEqual(3);
      expect(valueTicks({ width: 310, height })).toBeLessThanOrEqual(4);
    }
  });
});

describe("chartAxis", () => {
  it("turns the ticks off: a tick marks a position its label already names", () => {
    expect(chartAxis(T).axisTick).toEqual({ show: false });
    expect(chartAxis(T, { grid: true, splitNumber: 4 }).axisTick).toEqual({ show: false });
  });

  it("carries the count it was given, and neither key when it was not", () => {
    expect(chartAxis(T, { splitNumber: 3 }).splitNumber).toBe(3);
    expect("splitNumber" in chartAxis(T)).toBe(false);
    // The rules belong to the value axis; a category axis carrying them would
    // draw a hatch across the plot nothing is measured against.
    expect("splitLine" in chartAxis(T)).toBe(false);
    expect(chartAxis(T, { grid: true }).splitLine).toEqual({ lineStyle: { color: T.split } });
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

  // The count above is an ask; this is the answer. ECharts' nice-number search
  // decides both the step and how far the axis runs past the data, which is how a
  // default `splitNumber` returns eight or nine rules on the two extents this app
  // actually plots — and why the band in `valueTicks` was tuned against the drawn
  // result rather than derived.
  const drawnRules = (range: [number, number], splitNumber?: number) => {
    const chart = echarts.init(null as never, null, {
      renderer: "svg",
      ssr: true,
      width: 310,
      height: 280,
    });
    chart.setOption({
      grid: { top: 16, right: 8, bottom: 8, left: 8, containLabel: true },
      xAxis: { type: "category", data: ["a", "b"], ...chartAxis(T) },
      // The value axis extends to include zero by default, exactly as the app's do.
      yAxis: { type: "value", ...chartAxis(T, { grid: true, tick: (v) => `$${v}`, splitNumber }) },
      series: [{ type: "bar", data: [range[0], range[1]] }],
    });
    const svg = chart.renderToSVGString();
    chart.dispose();
    return [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)]
      .map((m) => m[1])
      .filter((text) => text.startsWith("$"));
  };

  it("replaces the default hatch with rules a reader can count off", () => {
    // The demo household's own extents: cash flow over a month, and the accounts
    // card's net worth.
    const cashFlow: [number, number] = [-990, 5235];
    const accounts: [number, number] = [0, 34000];
    expect(drawnRules(cashFlow)).toHaveLength(8);
    expect(drawnRules(cashFlow, valueTicks({ width: 310, height: 280 }))).toHaveLength(5);
    expect(drawnRules(accounts)).toHaveLength(8);
    expect(drawnRules(accounts, valueTicks({ width: 296, height: 220 }))).toHaveLength(5);
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
