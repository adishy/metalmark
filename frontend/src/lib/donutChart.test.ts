import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import type { CategorySpendRow } from "@/api/types";
import { donutOption } from "@/lib/donutChart";
import { phoneCanvas, type TooltipPoint } from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#111111", split: "#222222", label: "#333333", surface: "#444444", border: "#555555",
  fg: "#666666", accent: "#777777", positive: "#888888", negative: "#999999", warning: "#aaaaaa",
  series: ["#123456", "#234567", "#345678", "#456789", "#56789a", "#6789ab"],
} as unknown as ChartTokens;

/** The demo household's categories, with the widest name in them. */
const ROWS = [
  { key: "cat:1", category_id: "a", category_name: "Groceries", total: "2200.0000" },
  { key: "cat:2", category_id: "b", category_name: "Restaurants", total: "960.0000" },
  { key: "cat:3", category_id: "c", category_name: "Utilities", total: "700.0000" },
  { key: "cat:4", category_id: "d", category_name: "Clothing", total: "420.0000" },
  { key: "cat:5", category_id: "e", category_name: "Public Transit", total: "300.0000" },
  { key: "fee", category_id: null, category_name: "Investment fees", total: "100.0000" },
] as unknown as CategorySpendRow[];

const PHONE = { width: 310, height: 280 };
const WIDE = { width: 1104, height: 280 };

const series = (box: { width: number; height: number }) =>
  (donutOption(ROWS, T, "USD", box).series as Record<string, unknown>[])[0];

/**
 * What ECharts actually drew, as SVG.
 *
 * The point of these two tests is the *picture*, not the option: `label: {show:
 * false}` is ECharts' business to honour, and `labelLine` is ECharts' business to
 * keep showing — `PieView` keeps a slice's guide line on the strength of the
 * label's position, not its visibility, so hiding a label alone would have left a
 * phone with six strokes pointing at nothing. Neither is visible in the option.
 */
const drawn = (box: { width: number; height: number }) => {
  const chart = echarts.init(null as never, null, { renderer: "svg", ssr: true, ...box });
  chart.setOption(donutOption(ROWS, T, "USD", box));
  const svg = chart.renderToSVGString();
  chart.dispose();
  const texts = [...svg.matchAll(/<text[^>]*>(.*?)<\/text>/g)].map((m) =>
    m[1].replace(/<[^>]+>/g, ""),
  );
  return { texts, guideLines: (svg.match(/<polyline/g) ?? []).length, svg };
};

describe("phoneCanvas", () => {
  it("is about the canvas, and turns over at the one threshold the charts share", () => {
    expect(phoneCanvas(PHONE)).toBe(true);
    expect(phoneCanvas(WIDE)).toBe(false);
    expect(phoneCanvas({ width: 479, height: 280 })).toBe(true);
    expect(phoneCanvas({ width: 480, height: 280 })).toBe(false);
    // A chart whose box is not laid out yet is a phone card, not a sliver.
    expect(phoneCanvas({ width: 0, height: 280 })).toBe(true);
  });
});

describe("donutOption", () => {
  it("draws no slice label on a phone card, and no line pointing at one", () => {
    expect(series(PHONE)).toMatchObject({ label: { show: false }, labelLine: { show: false } });
  });

  it("leaves the wide canvas alone: labels on, named from the row's key", () => {
    const wide = series(WIDE) as { label: { show?: boolean; formatter: (p: TooltipPoint) => string } };
    expect(wide.label.show).toBeUndefined();
    // Keyed, not named: ECharts merges data items that share a name (§ the reason
    // `data` carries keys at all), so the label reads the name back out of the rows.
    expect(wide.label.formatter({ name: "cat:1" })).toBe("Groceries");
    expect(wide.label.formatter({ name: "fee" })).toBe("Investment fees");
  });

  it("keys its slices, so two rows sharing a name stay two slices", () => {
    const data = series(PHONE).data as { name: string; value: number }[];
    expect(data.map((d) => d.name)).toEqual(ROWS.map((r) => r.key));
    expect(data.map((d) => d.value)).toEqual(ROWS.map((r) => Number(r.total)));
  });
});

describe("as ECharts draws it", () => {
  it("paints the phone's ring with nothing written on it", () => {
    const phone = drawn(PHONE);
    expect(phone.guideLines).toBe(0);
    // The names survive in the legend under the ring; the ring itself carries the
    // six slices and their colours, and the length of a slice is the reading.
    for (const c of T.series as string[]) expect(phone.svg).toContain(c);
    for (const row of ROWS) expect(phone.texts).toContain(row.category_name);
    // Nothing is drawn as a fragment — the defect this change removes was
    // `Clo…`, `Ut…` and, for one slice, a bare `…`.
    for (const text of phone.texts) expect(text).not.toContain("…");
  });

  it("draws every wide label whole, from its own leader line", () => {
    const wide = drawn(WIDE);
    expect(wide.guideLines).toBe(ROWS.length);
    // Once on the slice, once in the legend.
    for (const row of ROWS) {
      expect(wide.texts.filter((t) => t === row.category_name)).toHaveLength(2);
    }
    for (const text of wide.texts) expect(text).not.toContain("…");
  });
});
