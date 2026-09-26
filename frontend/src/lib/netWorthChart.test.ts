import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import type { NetWorthSeries } from "@/api/types";
import { coverageNotes, netWorthOption, startsHere, tooltipBody } from "@/lib/netWorthChart";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#1", split: "#2", label: "#3", surface: "#4", border: "#5", fg: "#6",
  accent: "#7", positive: "#8", negative: "#9", warning: "#a", series: [],
} as unknown as ChartTokens;

const checking = (reason: "not_started" | "no_rate" | "no_price" | "no_balance") => ({
  account_id: "a1", name: "Checking", reason,
});

const POINTS: NetWorthSeries["points"] = [
  { date: "2026-01-01", net_worth: "0.0000", missing: [checking("not_started")] },
  { date: "2026-01-31", net_worth: "22000.0000", missing: [] },
  { date: "2026-02-28", net_worth: "21000.0000", missing: [{ ...checking("no_rate"), name: "Euro <b>" }] },
];

const series = (points = POINTS) =>
  ({ base_currency: "USD", points }) as unknown as NetWorthSeries;

type LineSeries = { smooth: boolean; data: { value: [number, number]; symbol?: string }[] };

describe("netWorthOption", () => {
  const option = netWorthOption(series(), T);
  const line = (option.series as LineSeries[])[0];

  it("puts points on a time axis with straight segments", () => {
    expect((option.xAxis as { type: string }).type).toBe("time");
    expect(line.smooth).toBe(false);
    expect(line.data[1].value).toEqual([new Date(2026, 0, 31).getTime(), 22000]);
  });

  it("draws a partial point and a starting point hollow, and a whole one solid", () => {
    expect(line.data.map((d) => d.symbol)).toEqual([undefined, "emptyCircle", "emptyCircle"]);
  });

  // The axis is the one place a reader learns what the numbers *are*. It used to
  // print ECharts' own `10,000` — no currency anywhere on a money chart — and the
  // gutter was a hardcoded 60 px regardless of what the labels needed.
  it("labels the value axis as money, in the series' own currency", () => {
    const y = option.yAxis as { axisLabel: { formatter: (v: number) => string } };
    expect(y.axisLabel.formatter(20000)).toBe("$20k");
    expect(y.axisLabel.formatter(0)).toBe("$0");
    expect(y.axisLabel.formatter(1234)).toBe("$1,234.00");
  });

  it("measures the gutter from its labels instead of reserving one", () => {
    expect(option.grid).toMatchObject({ containLabel: true, left: 8 });
  });

  // The formatter assertions above say what the axis *asks* for. This one says
  // what it *draws*: a canvas cannot be read back in a test, but ECharts' SVG
  // renderer runs headless and its text elements are plain nodes, so the labels
  // are checkable as text — which is the only way to catch an option that
  // ECharts quietly ignores (the axis would go back to bare `40,000` and every
  // assertion above would still pass).
  it("draws those money labels onto the axis", () => {
    const chart = echarts.init(null as never, null, {
      renderer: "svg",
      ssr: true,
      width: 310,
      height: 280,
    });
    chart.setOption(
      netWorthOption(
        series([
          { date: "2026-01-01", net_worth: "17372.2200", missing: [] },
          { date: "2026-02-28", net_worth: "38579.2400", missing: [] },
        ]),
        T,
      ),
    );
    const drawn = [...chart.renderToSVGString().matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map(
      (m) => m[1],
    );
    chart.dispose();
    // The value labels are the money; the axis also carries day labels, so this
    // asserts on the money half rather than on the whole list.
    const money = drawn.filter((text) => text.startsWith("$"));
    expect(money.length).toBeGreaterThan(1);
    expect(money[0]).toBe("$0");
    expect(drawn.some((text) => /^\d{1,3},\d{3}$/.test(text))).toBe(false);
  });
});

describe("coverage", () => {
  it("says where an account starts being counted", () => {
    expect(startsHere(POINTS, 1)).toEqual(["Checking"]);
    expect(startsHere(POINTS, 2)).toEqual([]);
  });

  it("names every account a point cannot value, and why", () => {
    const notes = coverageNotes(POINTS);
    expect(notes[0]).toBe(
      "Some points leave out accounts the ledger cannot value: Euro <b> (no exchange rate).",
    );
    expect(notes[1]).toMatch(/^Counted partway through: Checking from /);
  });

  it("does not call an account started when it is only left out for another reason", () => {
    const points: NetWorthSeries["points"] = [
      { date: "2026-01-01", net_worth: "0", missing: [checking("not_started")] },
      { date: "2026-01-31", net_worth: "0", missing: [checking("no_rate")] },
    ];
    expect(startsHere(points, 1)).toEqual([]);
    expect(coverageNotes(points)).toEqual([
      "Some points leave out accounts the ledger cannot value: Checking (no exchange rate).",
    ]);
  });

    it("says nothing when every point counts everything", () => {
    expect(coverageNotes([{ date: "2026-01-01", net_worth: "1.00", missing: [] }])).toEqual([]);
  });

  it("escapes account names in the tooltip, which ECharts renders as HTML", () => {
    const body = tooltipBody(POINTS[2], [], "USD");
    expect(body).toContain("Euro &#60;b&#62; — no exchange rate");
    expect(body).not.toContain("<b>");
  });
});
