import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import type { NetWorthSeries } from "@/api/types";
import {
  brushWindow,
  changesPerPoint,
  coverageNotes,
  coverageSummary,
  leftOutAccounts,
  measuredSpan,
  netWorthOption,
  startsHere,
  tooltipBody,
  xLabelPoints,
} from "@/lib/netWorthChart";
import { valueTicks } from "@/theme/chartInteraction";
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

/** Every point counts everything, so every interval is a real movement. */
const WHOLE: NetWorthSeries["points"] = [
  { date: "2026-01-01", net_worth: "100.0000", missing: [] },
  { date: "2026-01-31", net_worth: "160.0000", missing: [] },
  { date: "2026-02-28", net_worth: "140.0000", missing: [] },
];

/** An account starting, then a point that cannot value one: neither interval is
 *  a movement of money, and both sit between points that hold a figure. */
const UNEVEN: NetWorthSeries["points"] = [
  { date: "2026-01-01", net_worth: "100.0000", missing: [checking("not_started")] },
  { date: "2026-01-31", net_worth: "2200.0000", missing: [] },
  { date: "2026-02-28", net_worth: "2500.0000", missing: [checking("no_rate")] },
  { date: "2026-03-31", net_worth: "2400.0000", missing: [] },
];

const series = (points = POINTS) =>
  ({ base_currency: "USD", points }) as unknown as NetWorthSeries;

type Point = [number, number];
type LineSeries = {
  smooth: number;
  smoothMonotone?: string;
  showSymbol?: boolean;
  data: Point[];
  markPoint?: { silent?: boolean; data: { name: string; itemStyle: { borderColor: string } }[] };
};
type BarSeries = {
  type: string;
  data: { value: Point | [number, null]; itemStyle?: { color?: string; borderRadius?: number[] } }[];
  emphasis?: { disabled?: boolean; blur?: { itemStyle: { opacity: number } } };
};

const INSIGHTS = { width: 310, height: 280 };
const ACCOUNTS = { width: 296, height: 220 };
/** A wide page: past `PHONE_MAX`, so this canvas carries the strip and the brush. */
const WIDE = { width: 1104, height: 260 };

describe("netWorthOption", () => {
  const option = netWorthOption(series(), T, INSIGHTS);
  const line = (option.series as LineSeries[])[0];

  it("puts points on a time axis, on a curve that cannot pass its own values", () => {
    expect((option.xAxis as { type: string }).type).toBe("time");
    // A curve, because a reader asked for one — and a *monotone* one, so the
    // curvature between two carried-forward balances is flat and a step cannot
    // swing past the value it steps to. `smooth` alone does both wrong.
    expect(line.smooth).toBeGreaterThan(0);
    expect(line.smooth).toBeLessThan(1);
    expect(line.smoothMonotone).toBe("x");
    expect(line.data[1]).toEqual([new Date(2026, 0, 31).getTime(), 22000]);
  });

  // The line has no resting markers, so the two points that mean something are
  // drawn on purpose: a point that leaves an account out (ADR-0045) and one
  // where an account starts being counted.
  it("marks the partial and starting points on a line that otherwise has none", () => {
    expect(line.showSymbol).toBe(false);
    expect(line.data.every((d) => !(d as { symbol?: string }).symbol)).toBe(true);
    const marks = line.markPoint?.data ?? [];
    expect(line.markPoint?.silent).toBe(true);
    // The point where Checking starts being counted, and the one the ledger
    // cannot value. The first point is neither: an account "not tracked yet" is
    // how a history begins, not a hole in one, and both notes say so in words.
    expect(marks.map((m) => m.name)).toEqual(["2026-01-31", "2026-02-28"]);
    // The starting point is the accent; the one the ledger cannot value is the
    // warning role, and neither is a colour alone — both are in the tooltip.
    expect(marks[0].itemStyle.borderColor).toBe(T.accent);
    expect(marks[1].itemStyle.borderColor).toBe(T.warning);
  });

  // The option above says the marks are asked for; this says ECharts draws them.
  // `markPoint` is the only way a line with no symbols can still carry one, and
  // it is droppable in silence — every assertion above would pass while a reader
  // saw a step in the line with nothing saying an account had started being
  // counted. It has already happened once: an `emptyCircle` symbol puts the
  // item's colour in the *stroke* and fills the disc white, which draws a mark
  // that cannot be seen on the surface it sits on.
  it("draws those marks as rings on the canvas", () => {
    const rings = (points: NetWorthSeries["points"]) => {
      const chart = echarts.init(null as never, null, {
        renderer: "svg",
        ssr: true,
        width: WIDE.width,
        height: WIDE.height,
      });
      chart.setOption({ animation: false, ...netWorthOption(series(points), T, WIDE) });
      const svg = chart.renderToSVGString();
      chart.dispose();
      // A symbol is a path drawn at the origin and placed by its transform, so
      // the transform's translation is the point's pixel position.
      return [...svg.matchAll(/<path\b([^>]*)\/?>/g)].flatMap((m) => {
        const attrs = m[1];
        const at = (name: string) => new RegExp(`${name}="([^"]*)"`).exec(attrs)?.[1] ?? "";
        const placed = /matrix\([^)]*?,\s*(-?[\d.]+),\s*(-?[\d.]+)\)/.exec(at("transform"));
        const stroke = at("stroke");
        if (!placed || (stroke !== T.accent && stroke !== T.warning)) return [];
        return [{ cx: Number(placed[1]), cy: Number(placed[2]), stroke, fill: at("fill") }];
      });
    };

    const drawn = rings(POINTS);
    expect(drawn.map((r) => r.stroke)).toEqual([T.accent, T.warning]);
    // A disc of the surface with a coloured edge — not an outline in the surface
    // colour, which is the same as no mark at all.
    expect(drawn.map((r) => r.fill)).toEqual([T.surface, T.surface]);
    // Each ring is at its own point: 2026-01-31 comes before 2026-02-28, and
    // $22 000 is higher up the plot than $21 000.
    expect(drawn[0].cx).toBeLessThan(drawn[1].cx);
    expect(drawn[0].cy).toBeLessThan(drawn[1].cy);
    // On the line rather than beside it: the plot runs from $0 up, so both sit
    // inside it.
    for (const ring of drawn) {
      expect(ring.cy).toBeGreaterThan(0);
      expect(ring.cy).toBeLessThan(WIDE.height);
    }
    // One unbroken history asks for none of them.
    expect(rings(WHOLE)).toEqual([]);
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

  // A phone card is not a small wide page. It draws one grid with a measured
  // gutter: a strip would need a reserved one (§2.9), and 68 px of a 326 px
  // canvas is a fifth of the plot for a band the card has no room to read.
  it("leaves a phone card one grid, and gives a wide one the strip and the brush", () => {
    expect(option.grid).toMatchObject({ containLabel: true });
    expect(option.dataZoom).toBeUndefined();
    expect((option.series as BarSeries[]).length).toBe(1);

    const wide = netWorthOption(series(WHOLE), T, WIDE, { brush: true });
    const grids = wide.grid as { containLabel?: boolean; left: number }[];
    expect(grids).toHaveLength(2);
    expect((wide.series as BarSeries[]).length).toBe(2);
    expect(wide.dataZoom).toMatchObject({ type: "slider", xAxisIndex: [0, 1] });
    // The two grids have to start in the same place, or the bars sit under the
    // wrong days: a strip's grid has no labels of its own to measure, so both
    // are given the same number rather than `containLabel` each deciding.
    expect(grids[0].containLabel).toBe(false);
    expect(grids[0].left).toBe(grids[1].left);
    // Two grids are two x axes, and the pointer has to cross both.
    expect(wide.axisPointer).toMatchObject({ link: [{ xAxisIndex: "all" }] });
  });

  // The brush is not part of the look. Its window lives inside the ECharts
  // instance, so it belongs to the caller that can state it — a card whose
  // figures come from the report keeps its strip and its hover without one, and
  // a phone card draws none however it is asked.
  it("draws the brush only when the caller asks, and never on a phone", () => {
    const plain = netWorthOption(series(WHOLE), T, WIDE);
    expect(plain.dataZoom).toBeUndefined();
    expect((plain.series as BarSeries[]).length).toBe(2);
    expect(netWorthOption(series(WHOLE), T, INSIGHTS, { brush: true }).dataZoom).toBeUndefined();

    // And no band is reserved for a brush that is not drawn: the line's grid
    // starts at the canvas's own margin and keeps the 30 px the band would hold.
    const banded = (
      netWorthOption(series(WHOLE), T, WIDE, { brush: true }).grid as {
        top: number;
        height: number;
      }[]
    )[0];
    const bare = (plain.grid as { top: number; height: number }[])[0];
    expect(banded.top).toBeGreaterThan(bare.top);
    expect(banded.height).toBeLessThan(bare.height);
  });

  // The gutter above is shared by construction — both grids are handed the same
  // `left` — and this is the drawn proof of it, which is the part that can go
  // wrong: a strip whose grid started 60 px further left would put its bars under
  // the wrong days while every option-level assertion above still passed. The
  // line's rules and the strip's zero rule both span their whole grid, so their
  // x extents are the two plots' left and right edges.
  it("lines the strip's days up with the line's", () => {
    const chart = echarts.init(null as never, null, {
      renderer: "svg",
      ssr: true,
      width: WIDE.width,
      height: WIDE.height,
    });
    chart.setOption({ animation: false, ...netWorthOption(series(WHOLE), T, WIDE) });
    const svg = chart.renderToSVGString();
    chart.dispose();

    const spans = [...svg.matchAll(/<path\b([^>]*)\/?>(?:<\/path>)?/g)].flatMap((m) => {
      const attrs = m[1];
      const d = /d="([^"]*)"/.exec(attrs)?.[1] ?? "";
      const stroke = /stroke="([^"]*)"/.exec(attrs)?.[1] ?? "";
      const nums = (d.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
      // A straight line segment, drawn as `M x0 y0 L x1 y1`.
      if (nums.length !== 4 || nums[1] !== nums[3]) return [];
      return [
        {
          from: Math.min(nums[0], nums[2]),
          to: Math.max(nums[0], nums[2]),
          stroke,
          dashed: /stroke-dasharray/.test(attrs),
        },
      ];
    });

    const rule = spans.find((s) => s.stroke === T.split);
    const stripZero = spans.find((s) => s.dashed);
    expect(rule).toBeDefined();
    expect(stripZero).toBeDefined();
    expect(stripZero!.from).toBeCloseTo(rule!.from, 0);
    expect(stripZero!.to).toBeCloseTo(rule!.to, 0);
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
        { width: 310, height: 280 },
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
    expect(money.every((text) => /^\$\d+(\.\d+)?k$/.test(text))).toBe(true);
    expect(drawn.some((text) => /^\d{1,3},\d{3}$/.test(text))).toBe(false);
  });

  // What the value axis is ruled in, read off the drawn chart in dollars — the
  // option only *asks* (`scale`), and what a reader gets is the range ECharts
  // then chooses, so that is what these assert on.
  const ruledAt = (values: string[], box = { width: 310, height: 280 }) => {
    const chart = echarts.init(null as never, null, { renderer: "svg", ssr: true, ...box });
    chart.setOption(
      netWorthOption(
        series(
          values.map((net_worth, i) => ({
            date: `2026-0${i + 1}-01`,
            net_worth,
            missing: [],
          })),
        ),
        T,
        box,
      ),
    );
    const drawn = [...chart.renderToSVGString().matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map(
      (m) => m[1],
    );
    chart.dispose();
    return drawn
      .filter((text) => /^[−$]/.test(text))
      .map((text) => {
        const [, sign, digits, unit] = /^(−?)\$([\d.,]+)(k?)$/.exec(text)!;
        return (sign ? -1 : 1) * Number(digits.replace(/,/g, "")) * (unit ? 1000 : 1);
      })
      .sort((a, b) => a - b);
  };

  // The complaint this axis was changed for: $100k, $101k, $97k, $105k on an axis
  // that began at zero is four points in the top tenth of the plot — a straight
  // line. The axis is ruled around the figures instead, closely enough that the
  // movement is most of the plot's height and not a sliver of it.
  it("rules the value axis around the figures, not up from zero", () => {
    const moved = ["100000.0000", "101000.0000", "97000.0000", "105000.0000"];
    for (const box of [{ width: 310, height: 280 }, WIDE]) {
      const rules = ruledAt(moved, box);
      const [low, high] = [rules[0], rules[rules.length - 1]];
      expect(low).toBeGreaterThan(90000);
      expect(low).toBeLessThanOrEqual(97000);
      expect(high).toBeGreaterThanOrEqual(105000);
      // The $8k the figures moved by is at least half of what the axis spans.
      expect(high - low).toBeLessThanOrEqual(16000);
    }
  });

  // Zero is not banished, only no longer forced: a history that starts at
  // nothing, or goes under it, is still measured against it.
  it("keeps zero on the axis when the figures reach or cross it", () => {
    expect(ruledAt(["0.0000", "22000.0000", "21000.0000"])[0]).toBe(0);
    const crossed = ruledAt(["-4000.0000", "3000.0000", "6000.0000"]);
    expect(crossed[0]).toBeLessThanOrEqual(-4000);
    expect(crossed).toContain(0);
  });

  // ...and it does not follow the figures all the way down. Forty cents of
  // interest on $100k is a flat line; ruled in dimes it is a climb the height of
  // the plot under labels (`$100,000.10`) the gutter was never sized for.
  it("does not magnify a movement of cents into a slope", () => {
    const rules = ruledAt(["100000.0000", "100000.1500", "100000.4000"], WIDE);
    expect(rules[rules.length - 1] - rules[0]).toBeGreaterThanOrEqual(10);
    for (const rule of rules) expect(rule % 10).toBe(0);
  });

  // How many rules the axis draws follows from the *plot's* height, because that
  // is the direction they crowd in — and this chart is two different heights in
  // two different places (280 px in Insights, 260 px in the accounts card, where
  // the strip has taken the bottom sixth of the canvas).
  it("asks the value axis for a count the canvas's height can carry", () => {
    const y = (box: { width: number; height: number }) =>
      netWorthOption(series(), T, box).yAxis as { splitNumber: number };
    expect(y(INSIGHTS).splitNumber).toBe(valueTicks(INSIGHTS));
    expect(y(ACCOUNTS).splitNumber).toBe(valueTicks(ACCOUNTS));
    // Shorter canvas, fewer rules asked for.
    expect(y(ACCOUNTS).splitNumber).toBeLessThan(y(INSIGHTS).splitNumber);
    // And on a canvas with a strip, the count follows what is left of the plot.
    const wide = netWorthOption(series(), T, WIDE).yAxis as { splitNumber: number }[];
    expect(wide[0].splitNumber).toBeLessThan(valueTicks(WIDE));
  });
});

describe("the change strip", () => {
  const strip = (points = WHOLE) =>
    (netWorthOption(series(points), T, WIDE).series as BarSeries[])[1];

  it("draws each interval's movement, coloured and rounded by its sign", () => {
    const bars = strip();
    expect(bars.type).toBe("bar");
    expect(bars.data.map((d) => d.value[1])).toEqual([null, 60, -20]);
    // Nothing on the first point: there is no interval before it.
    expect(bars.data[0].itemStyle).toBeUndefined();
    expect(bars.data[1].itemStyle?.color).toBe(T.positive);
    expect(bars.data[1].itemStyle?.borderRadius).toEqual([4, 4, 0, 0]);
    expect(bars.data[2].itemStyle?.color).toBe(T.negative);
    expect(bars.data[2].itemStyle?.borderRadius).toEqual([0, 0, 4, 4]);
  });

  // The honesty rule. A difference between two points that count different
  // accounts is not a movement of money, and a bar for it would be the strip
  // inventing a figure — so there is no bar, and the tooltip says which of the
  // two reasons it is.
  it("draws no bar where the two points do not count the same accounts", () => {
    expect(strip(UNEVEN).data.map((d) => d.value[1])).toEqual([null, null, null, null]);
  });

  // `emphasisBar` states one colour for a whole series, and these bars carry
  // their own sign colour: highlighting a rise with the bar helper's colour
  // would repaint it as a fall.
  it("takes no highlight of its own, but still dims under another series' spotlight", () => {
    expect(strip().emphasis?.disabled).toBe(true);
    expect(strip().emphasis?.blur?.itemStyle.opacity).toBeGreaterThan(0);
  });
});

describe("changesPerPoint", () => {
  it("states each interval, and nothing for the point before the first", () => {
    expect(changesPerPoint(WHOLE)).toEqual([
      null,
      { kind: "value", value: 60, since: "2026-01-01" },
      { kind: "value", value: -20, since: "2026-01-31" },
    ]);
  });

  it("tells an account starting apart from a point that cannot value one", () => {
    expect(changesPerPoint(UNEVEN)).toEqual([
      null,
      { kind: "started" },
      { kind: "gap" },
      // The interval *out* of a partial point is not comparable either: a rise
      // measured from a total that was missing an account is not a rise.
      { kind: "gap" },
    ]);
  });

  it("does not call a reason changing from not-started to no-rate a start", () => {
    const points: NetWorthSeries["points"] = [
      { date: "2026-01-01", net_worth: "0", missing: [checking("not_started")] },
      { date: "2026-01-31", net_worth: "0", missing: [checking("no_rate")] },
    ];
    expect(changesPerPoint(points)[1]).toEqual({ kind: "gap" });
  });
});

describe("measuredSpan", () => {
  /** The window opens before the household's history does: the first two points
   *  count nothing at all, and are then carried forward from 2026-01-31. */
  const YOUNGER: NetWorthSeries["points"] = [
    { date: "2025-09-26", net_worth: "0.0000", missing: [checking("not_started")] },
    { date: "2025-12-31", net_worth: "0.0000", missing: [checking("not_started")] },
    { date: "2026-01-31", net_worth: "17372.2222", missing: [] },
    { date: "2026-04-30", net_worth: "17372.2222", missing: [] },
    { date: "2026-08-31", net_worth: "34352.2222", missing: [] },
    { date: "2026-09-26", net_worth: "40329.2412", missing: [] },
  ];
  /** The newest account's first balance lands on the window's last point, so
   *  the trailing interval is a start and the strip's last bar is earlier. */
  const STARTS_AT_END: NetWorthSeries["points"] = [
    { date: "2026-01-31", net_worth: "100.0000", missing: [checking("not_started")] },
    { date: "2026-03-31", net_worth: "150.0000", missing: [checking("not_started")] },
    { date: "2026-06-30", net_worth: "200.0000", missing: [] },
  ];

  it("takes the whole window when every interval in it is a movement", () => {
    expect(measuredSpan(WHOLE, 0, 2)).toEqual([0, 2]);
  });

  it("starts where the window stops counting fewer accounts than it closes on", () => {
    // The empty leading points are outside the span: a headline over the whole
    // window would state a rise out of a total that counted nothing.
    expect(measuredSpan(YOUNGER, 0, 5)).toEqual([2, 5]);
  });

  it("ends at the strip's last bar when the window's last point has just started counting one", () => {
    expect(measuredSpan(STARTS_AT_END, 0, 2)).toEqual([0, 1]);
  });

  it("reports no span where the window draws no bar at all", () => {
    const started: NetWorthSeries["points"] = [
      { date: "2026-01-01", net_worth: "0.0000", missing: [checking("not_started")] },
      { date: "2026-01-31", net_worth: "2200.0000", missing: [] },
    ];
    expect(measuredSpan(started, 0, 1)).toEqual([1, 1]);
    expect(measuredSpan(UNEVEN, 0, 3)).toEqual([3, 3]);
    // An empty series has no points to index, and no span.
    expect(measuredSpan([], 0, -1)).toEqual([-1, -1]);
  });

  it("never reports a span outside the window on screen", () => {
    // A brush that opens on 2025-12-31 cannot be widened by the span.
    expect(measuredSpan(YOUNGER, 1, 5)).toEqual([2, 5]);
    expect(measuredSpan(YOUNGER, 3, 5)).toEqual([3, 5]);
  });

  // The claim the headline makes, checked against the picture under it: the
  // bars the strip draws between the span's two ends are what the one
  // subtraction a headline performs adds up to.
  it("spans exactly the bars the strip draws", () => {
    for (const points of [WHOLE, UNEVEN, YOUNGER, STARTS_AT_END]) {
      const [first, last] = measuredSpan(points, 0, points.length - 1);
      const bars = changesPerPoint(points)
        .slice(first + 1, last + 1)
        .map((c) => (c?.kind === "value" ? c.value : null));
      expect(bars.every((b) => b !== null)).toBe(true);
      const sum = bars.reduce<number>((a, b) => a + (b ?? 0), 0);
      const delta =
        Number(points[last]?.net_worth ?? 0) - Number(points[first]?.net_worth ?? 0);
      expect(Math.abs(sum - delta)).toBeLessThan(1e-9);
    }
  });
});

describe("brushWindow", () => {
  const points = WHOLE;
  const day = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).getTime();
  };
  const pct = (iso: string) =>
    ((day(iso) - day("2026-01-01")) / (day("2026-02-28") - day("2026-01-01"))) * 100;

  it("maps the window's percentages onto the points it covers", () => {
    expect(brushWindow(points, pct("2026-01-31"), 100)).toEqual([1, 2]);
    expect(brushWindow(points, 0, pct("2026-01-31"))).toEqual([0, 1]);
  });

  it("says `whole range` rather than naming both ends of it", () => {
    expect(brushWindow(points, 0, 100)).toBeNull();
    // A window one point wide is not a range: the headline needs two ends.
    expect(brushWindow(points, pct("2026-01-31"), pct("2026-01-31"))).toBeNull();
  });

  it("declines a series too short to brush, or a window that is not numbers", () => {
    expect(brushWindow(points.slice(0, 2), 0, 50)).toBeNull();
    expect(brushWindow(points, Number.NaN, 100)).toBeNull();
  });
});

describe("the collapsed coverage summary", () => {
  const point = (missing: { account_id: string; name: string; reason: "not_started" | "no_balance" | "no_rate" | "no_price" }[]) =>
    ({ date: "2026-01-01", net_worth: "1.00", missing });

  it("is a neutral title when nothing is left out, or accounts only start partway", () => {
    expect(coverageSummary([point([])])).toEqual({ summary: "History coverage", tone: "neutral" });
    const started = [point([{ account_id: "a", name: "Savings", reason: "not_started" }])];
    expect(leftOutAccounts(started)).toBe(0);
    expect(coverageSummary(started).tone).toBe("neutral");
  });

  it("warns, counting each account once however many points leave it out", () => {
    const points = [
      point([{ account_id: "a", name: "Euro", reason: "no_rate" }]),
      point([
        { account_id: "a", name: "Euro", reason: "no_rate" },
        { account_id: "b", name: "Brokerage", reason: "no_price" },
        { account_id: "c", name: "New", reason: "not_started" },
      ]),
    ];
    expect(leftOutAccounts(points)).toBe(2);
    expect(coverageSummary(points)).toEqual({
      summary: "Chart leaves out 2 accounts at some points",
      tone: "warning",
    });
    expect(coverageSummary([points[0]]).summary).toBe("Chart leaves out 1 account at some points");
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

  // The strip's own figure, at full precision, in the one place a chart prints
  // one — and where the strip draws no bar, which of the two reasons it is.
  it("states the interval's change, and explains an absent bar", () => {
    const body = tooltipBody(WHOLE[1], [], "USD", changesPerPoint(WHOLE)[1]);
    expect(body).toContain("Change since Jan 01: ▲ $60.00");

    const down = tooltipBody(WHOLE[2], [], "USD", changesPerPoint(WHOLE)[2]);
    expect(down).toContain("Change since Jan 31: ▼ $20.00");

    // Carried-forward balances make most intervals a movement of nothing, and
    // `▲ $0.00` points a reader the wrong way. The test is the rounded figure,
    // because that is the figure on screen.
    const flat = tooltipBody(WHOLE[2], [], "USD", { kind: "value", value: 0, since: "2026-01-31" });
    expect(flat).toContain("No change since Jan 31");
    expect(flat).not.toContain("▲");
    expect(flat).not.toContain("▼");
    const rounding = tooltipBody(WHOLE[2], [], "USD", {
      kind: "value",
      value: 0.004,
      since: "2026-01-31",
    });
    expect(rounding).toContain("No change since Jan 31");

    expect(tooltipBody(UNEVEN[1], ["Checking"], "USD", { kind: "started" })).toContain(
      "No change shown: an account starts or stops being counted here.",
    );
    expect(tooltipBody(UNEVEN[2], [], "USD", { kind: "gap" })).toContain(
      "No change shown: a point here leaves an account out, so the two are not comparable.",
    );
    // Back-compatible: the body a caller without an interval still gets.
    expect(tooltipBody(WHOLE[1], [], "USD")).not.toContain("Change since");
  });
});

// Which points the x axis names. ECharts' own tick search under `hideOverlap`
// drew a week on a phone as seven labels touching, and a month as `Sep 13,
// Sep 21,` a hole, `Oct 01, Oct 05, Oct 09` — so the axis is told.
describe("xLabelPoints", () => {
  const PHONE_PLOT = 222;
  const WIDE_PLOT = 1004;
  const at = (dates: string[]) =>
    dates.map((date) => ({ date, net_worth: "1.0000", missing: [] })) as NetWorthSeries["points"];
  const days = (from: string, count: number) => {
    const [y, m, d] = from.split("-").map(Number);
    return at(
      Array.from({ length: count }, (_, i) => {
        const day = new Date(y, m - 1, d + i);
        const pad = (n: number) => String(n).padStart(2, "0");
        return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
      }),
    );
  };
  const ts = (date: string) => {
    const [y, m, d] = date.split("-").map(Number);
    return new Date(y, m - 1, d).getTime();
  };
  /** The picked labels, as the `MM-DD` of the point each sits on. */
  const named = (points: NetWorthSeries["points"], picked: number[]) =>
    picked.map((at) => points.find((p) => ts(p.date) === at)!.date.slice(5));

  it("names a week on a phone in three evenly spaced days, ending on the newest", () => {
    const week = days("2026-10-03", 8);
    expect(named(week, xLabelPoints(week, "day", PHONE_PLOT))).toEqual(["10-04", "10-07", "10-10"]);
  });

  it("keeps one stride across a month boundary", () => {
    const month = days("2026-09-10", 31);
    expect(named(month, xLabelPoints(month, "day", WIDE_PLOT))).toEqual([
      "09-10", "09-13", "09-16", "09-19", "09-22", "09-25", "09-28", "10-01", "10-04", "10-07", "10-10",
    ]);
  });

  it("names every day when there is room for every day", () => {
    const week = days("2026-10-03", 8);
    expect(xLabelPoints(week, "day", WIDE_PLOT)).toHaveLength(8);
  });

  // The window's two ends are not buckets: its first point is the opening day
  // and its newest is today, part-way into a month.
  const YEAR = at([
    "2025-10-10", "2025-10-31", "2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28",
    "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31",
    "2026-09-30", "2026-10-10",
  ]);

  it("always names the newest point, and drops a month-end that crowds it", () => {
    const picked = named(YEAR, xLabelPoints(YEAR, "month", WIDE_PLOT));
    expect(picked[picked.length - 1]).toBe("10-10");
    expect(picked).not.toContain("09-30");
    expect(picked).toContain("08-31");
  });

  it("does not name a coarse window's opening day", () => {
    for (const width of [PHONE_PLOT, WIDE_PLOT]) {
      expect(xLabelPoints(YEAR, "month", width)).not.toContain(ts(YEAR[0].date));
    }
  });

  it("never puts two labels closer than most of a pitch", () => {
    for (const [points, granularity, pitch] of [
      [days("2026-09-26", 15), "day", 72],
      [days("2026-09-10", 31), "day", 72],
      [YEAR, "month", 48],
    ] as const) {
      const span = ts(points[points.length - 1].date) - ts(points[0].date);
      for (const width of [PHONE_PLOT, WIDE_PLOT]) {
        const picked = xLabelPoints(points, granularity, width);
        expect(picked.length).toBeGreaterThan(1);
        for (let i = 1; i < picked.length; i++) {
          expect(((picked[i] - picked[i - 1]) / span) * width).toBeGreaterThanOrEqual(0.75 * pitch);
        }
      }
    }
  });

  it("has nothing to name on an empty series, and one label on a single point", () => {
    expect(xLabelPoints([], "day", PHONE_PLOT)).toEqual([]);
    expect(xLabelPoints(at(["2026-10-10"]), "day", PHONE_PLOT)).toHaveLength(1);
  });
});
