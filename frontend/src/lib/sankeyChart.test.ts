import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import type { CashFlowSankey } from "@/api/types";
import { buildSankey } from "@/lib/sankey";
import { sankeyOption } from "@/lib/sankeyChart";
import { chartLayout } from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#111111", split: "#222222", label: "#333333", surface: "#444444", border: "#555555",
  fg: "#666666", accent: "#777777", positive: "#888888", negative: "#999999", warning: "#aaaaaa",
  series: [],
} as unknown as ChartTokens;

const DATA = {
  base_currency: "USD",
  start: "2026-01-01",
  end: "2026-12-31",
  income: [{ key: "in:pay", label: "Paychecks", category_id: "a", total: "26000.0000" }],
  expense: [
    { key: "out:food", label: "Groceries", category_id: "b", total: "2200.0000" },
    { key: "out:eat", label: "Restaurants", category_id: "c", total: "960.0000" },
    { key: "out:util", label: "Utilities", category_id: "d", total: "700.0000" },
  ],
  total_income: "26000.0000",
  total_expense: "3860.0000",
  net: "22140.0000",
  attribution: "",
  warnings: [],
} as unknown as CashFlowSankey;

/** A phone card's canvas and a wide page's, both measured on the demo household. */
const PHONE = { width: 310, height: 360 };
const WIDE = { width: 1104, height: 360 };

/** The width left for the two ribbon spans, in px. */
const ribbonRoom = (l: { left: number; right: number; nodeWidth: number }, width: number) =>
  (width - l.left - l.right - 3 * l.nodeWidth) / 2;

describe("chartLayout", () => {
  it("sizes a phone card's label columns to the labels, and gives the rest to the ribbons", () => {
    const phone = chartLayout(PHONE);
    // A column takes the label's own width plus ECharts' gap, and not a pixel more.
    expect(phone.left).toBe(phone.labelWidth + 5);
    expect(phone.right).toBe(phone.labelWidth + 5);
    // The ribbons are what the chart is for. Before this they had 38 px each on a
    // 310 px canvas; the floor is here so a later tweak cannot quietly eat them.
    expect(ribbonRoom(phone, PHONE.width)).toBeGreaterThanOrEqual(50);
  });

  it("keeps the wide layout the chart has always been drawn with", () => {
    expect(chartLayout(WIDE)).toEqual(chartLayout({ width: 900, height: 220 }));
    expect(chartLayout(WIDE)).toMatchObject({ left: 88, right: 104, nodeWidth: 14, nodeGap: 10 });
  });

  it("lays an unmeasured canvas out as a phone card rather than a sliver", () => {
    expect(chartLayout({ width: 0, height: 280 })).toEqual(chartLayout(PHONE));
  });
});

describe("sankeyOption", () => {
  const graph = buildSankey(DATA)!;
  const seriesAt = (box: { width: number; height: number }) =>
    (sankeyOption(graph, T, "USD", box).series as Record<string, unknown>[])[0];

  it("takes its margins, node width and gaps from the canvas it was given", () => {
    expect(seriesAt(PHONE)).toMatchObject({ left: 89, right: 89, nodeWidth: 10, nodeGap: 8 });
    expect(seriesAt(WIDE)).toMatchObject({ left: 88, right: 104, nodeWidth: 14, nodeGap: 10 });
  });

  it("leaves the label rule alone: outward, and truncated at the same width", () => {
    for (const box of [PHONE, WIDE]) {
      const s = seriesAt(box);
      expect((s.label as { width: number }).width).toBe(84);
      expect((s.label as { overflow: string }).overflow).toBe("truncate");
      const positions = (s.data as { label: { position: string } }[]).map((n) => n.label.position);
      // Sources left, the window above its node, destinations right.
      expect(positions[0]).toBe("left");
      expect(positions[1]).toBe("top");
      expect(positions.at(-1)).toBe("right");
    }
  });
});

// The layout above is a claim about numbers; this one is about the picture. ECharts'
// SVG renderer runs headless, so the columns, the node width and the ribbon span can
// be read back as geometry instead of trusted — an option ECharts ignores would leave
// the phone with the desk's layout and every assertion above would still pass.
describe("as ECharts draws it", () => {
  const drawn = (box: { width: number; height: number }) => {
    const chart = echarts.init(null as never, null, {
      renderer: "svg",
      ssr: true,
      width: box.width,
      height: box.height,
    });
    chart.setOption(sankeyOption(buildSankey(DATA)!, T, "USD", box));
    const svg = chart.renderToSVGString();
    chart.dispose();
    // A node is a rect translated into place; the clip-path shares the shape but is
    // painted in black, so the series colours tell the two apart.
    const nodes = [
      ...svg.matchAll(
        /<path d="M(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)l(-?\d+(?:\.\d+)?) 0l0 (-?\d+(?:\.\d+)?)l-\3 0Z" transform="translate\((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)" fill="(#[0-9a-f]{6})"/g,
      ),
    ]
      .filter((m) => [T.positive, T.negative, T.accent].includes(m[7]))
      .map((m) => ({ x: Number(m[5]) + Number(m[1]), width: Number(m[3]) }));
    const columns = [...new Set(nodes.map((n) => n.x))].sort((a, b) => a - b);
    const labels = [
      ...svg.matchAll(/<text[^>]*text-anchor="(\w+)"[^>]*transform="translate\((-?[\d.]+)/g),
    ].map((m) => ({ anchor: m[1], x: Number(m[2]) }));
    return { columns, nodeWidth: nodes[0]?.width, labels };
  };

  it("gives the phone's ribbons a third more room than the desk margins allowed", () => {
    const phone = drawn(PHONE);
    expect(phone.columns).toHaveLength(3);
    const spans = [phone.columns[1] - phone.columns[0], phone.columns[2] - phone.columns[1]];
    const nodeWidth = phone.nodeWidth!;
    const ribbons = spans.map((s) => s - nodeWidth);
    expect(new Set(ribbons)).toEqual(new Set([51]));
    // 38 px per span before this change, from margins sized for the desk.
    expect(ribbons[0]).toBeGreaterThan(38);
  });

  it("draws the desk exactly as it was", () => {
    const wide = drawn(WIDE);
    expect(wide.columns).toEqual([88, 537, 986]);
    expect(wide.nodeWidth).toBe(14);
  });

  it("keeps every label clear of the ribbons it is pinned to", () => {
    const phone = drawn(PHONE);
    const [first, , last] = phone.columns;
    const left = phone.labels.filter((l) => l.anchor === "end");
    const right = phone.labels.filter((l) => l.anchor === "start");
    expect(left).toHaveLength(1);
    expect(right).toHaveLength(4);
    // Outward from their own column, in the margin — never over a ribbon.
    expect(left[0].x).toBeLessThanOrEqual(first);
    expect(Math.min(...right.map((l) => l.x))).toBeGreaterThanOrEqual(last + phone.nodeWidth!);
  });
});
