import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import type { AllocationRow } from "@/api/types";
import {
  notDrawnSentence,
  tileColour,
  tileText,
  treemapData,
  treemapFinding,
  treemapOption,
} from "@/lib/treemapChart";
import { DIM } from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

const T = {
  axis: "#111111", split: "#222222", label: "#333333", surface: "#444444", border: "#555555",
  fillInk: "#eeeeee", fg: "#666666", accent: "#777777", positive: "#888888", negative: "#999999",
  warning: "#aaaaaa",
  series: ["#123456", "#234567", "#345678", "#456789", "#56789a", "#6789ab"],
} as unknown as ChartTokens;

/** A household's groups, from a big one down to a speck — and one short. */
const ROWS = [
  { key: "cash", label: "Cash", value_base: "12000.0000", percent: "40.0000", holdings: 2, sources: [] },
  { key: "sec-vti", label: "VTI", value_base: "9000.0000", percent: "30.0000", holdings: 1, sources: [] },
  { key: "sec-bnd", label: "BND", value_base: "6000.0000", percent: "20.0000", holdings: 1, sources: [] },
  { key: "sec-aapl", label: "AAPL", value_base: "2990.0000", percent: "9.9667", holdings: 1, sources: [] },
  { key: "sec-dust", label: "DUST", value_base: "10.0000", percent: "0.0333", holdings: 1, sources: [] },
  { key: "sec-short", label: "SHORT", value_base: "-1000.0000", percent: "-3.3333", holdings: 1, sources: [] },
] as unknown as AllocationRow[];

/**
 * The same rows with the figures the server actually sends.
 *
 * `percent` is `value_base / total_base` (`services/investments.py`), so the six
 * shares sum to 100 over all six rows and the short's is negative — and the five
 * tiles sum to $29,990.00 while the allocation's total is $28,990.00. That gap is
 * the whole point of the fixture: it is where a share of the allocation and a
 * share of the ink stop being the same number.
 */
const CONSISTENT = [
  { key: "cash", label: "Cash", value_base: "12000.0000", percent: "41.3936", holdings: 2, sources: [] },
  { key: "sec-vti", label: "VTI", value_base: "9000.0000", percent: "31.0452", holdings: 1, sources: [] },
  { key: "sec-bnd", label: "BND", value_base: "6000.0000", percent: "20.6968", holdings: 1, sources: [] },
  { key: "sec-aapl", label: "AAPL", value_base: "2990.0000", percent: "10.3139", holdings: 1, sources: [] },
  { key: "sec-dust", label: "DUST", value_base: "10.0000", percent: "0.0345", holdings: 1, sources: [] },
  { key: "sec-short", label: "SHORT", value_base: "-1000.0000", percent: "-3.4495", holdings: 1, sources: [] },
] as unknown as AllocationRow[];

/** The same shape under `group by account`, where the names are long. */const ACCOUNTS = [
  { key: "acc-1", label: "Report Acct 1790445900682", value_base: "18000", percent: "60", holdings: 3, sources: [] },
  { key: "acc-2", label: "Report Acct 1790449427616", value_base: "9000", percent: "30", holdings: 2, sources: [] },
  { key: "acc-3", label: "Brokerage", value_base: "2900", percent: "9.67", holdings: 1, sources: [] },
  { key: "acc-4", label: "Checking", value_base: "100", percent: "0.33", holdings: 1, sources: [] },
] as unknown as AllocationRow[];

const PHONE = { width: 310, height: 300 };
const WIDE = { width: 1104, height: 300 };

const series = (rows: AllocationRow[], box: { width: number; height: number }) =>
  (treemapOption(treemapData(rows), T, "USD", box).series as Record<string, unknown>[])[0];

const label = (rows: AllocationRow[], box: { width: number; height: number }) =>
  series(rows, box).label as { color: string; formatter: (p: { name?: string }) => string };

/**
 * What ECharts actually drew, as SVG.
 *
 * The label rule in this module is a claim about the *drawn* chart: a tile names
 * itself only if the room its area gives it holds the name, because ECharts
 * truncates an over-long label to its own tile rather than letting it collide
 * (§2.9's "no chart text is drawn as a fragment"). That is ECharts' business to
 * honour, and only the library can answer it — the same reason `donutChart.test`
 * renders the ring instead of trusting `label: {show: false}`.
 */
const drawn = (rows: AllocationRow[], box: { width: number; height: number }) => {
  const chart = echarts.init(null as never, null, { renderer: "svg", ssr: true, ...box });
  chart.setOption(treemapOption(treemapData(rows), T, "USD", box));
  const svg = chart.renderToSVGString();
  chart.dispose();
  // One entry per drawn label, its lines in order. zrender draws each line of a
  // multi-line label as its own `<text>` element — two siblings sharing one
  // `translate`, the first at y=-6 and the second at y=+6 — so the lines are
  // grouped back by the point they are centred on. Grouping matters: the claim
  // is "this tile says its name and its share", and a flat list of drawn strings
  // cannot tell that from one tile saying a name and another saying a share.
  const byPoint = new Map<string, { y: number; text: string }[]>();
  for (const m of svg.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/g)) {
    const point = /transform="translate\(([^)]+)\)"/.exec(m[1])?.[1];
    if (point === undefined) continue;
    const y = Number(/ y="([-+]?[\d.]+)"/.exec(m[1])?.[1] ?? 0);
    const lines = byPoint.get(point) ?? [];
    lines.push({ y, text: m[2].replace(/<[^>]+>/g, "").trim() });
    byPoint.set(point, lines);
  }
  const labels = [...byPoint.values()].map((lines) =>
    lines.sort((a, b) => a.y - b.y).map((line) => line.text),
  );
  return { labels, texts: labels.flat(), svg };
};

describe("treemapData", () => {
  it("gives a tile to every row that has area to give, and keeps the rest", () => {
    const data = treemapData(ROWS);
    expect(data.tiles.map((r) => r.key)).toEqual(["cash", "sec-vti", "sec-bnd", "sec-aapl", "sec-dust"]);
    // Not dropped: a short position is a row the report keeps (ADR-0032), so it is
    // handed back for the sentence under the chart.
    expect(data.skipped.map((r) => r.key)).toEqual(["sec-short"]);
  });

  it("treats a zero-valued row as having no area either", () => {
    const zero = [{ key: "z", label: "Zero", value_base: "0.0000", percent: "0", holdings: 1, sources: [] }];
    const data = treemapData(zero as unknown as AllocationRow[]);
    expect(data.tiles).toHaveLength(0);
    expect(data.skipped).toHaveLength(1);
  });
});

describe("tileColour", () => {
  it("follows the row's place in the list, and starts the palette again past ten", () => {
    expect(tileColour(T, 0)).toBe("#123456");
    expect(tileColour(T, 1)).toBe("#234567");
    expect(tileColour(T, T.series.length)).toBe("#123456");
  });
});

describe("tileText", () => {
  const name = "Cash";
  const percent = "40.0%";

  it("says nothing on a tile with no room for the name", () => {
    // A 0.2% tile of a 310×300 canvas: the estimate gives it a short side well
    // under one line of text.
    expect(tileText(PHONE, 0.002, name, percent, false)).toBe("");
  });

  it("names a phone-sized tile, and withholds the share on a phone", () => {
    expect(tileText(PHONE, 0.4, name, percent, false)).toBe("Cash");
  });

  it("writes the share as a second line only where a wide tile has two lines", () => {
    expect(tileText(WIDE, 0.4, name, percent, true)).toBe("Cash\n40.0%");
    // 1.2% of a wide canvas: a short side near 32 px. Room for four characters
    // and not for a second line, so the tile says its name and no more.
    expect(tileText(WIDE, 0.012, name, percent, true)).toBe("Cash");
    expect(tileText(WIDE, 0.004, name, percent, true)).toBe("");
    // The phone/wide decision is the caller's (`phoneCanvas`), not this
    // function's: given the room, a phone-sized box writes both lines too.
    expect(tileText(PHONE, 0.4, name, percent, true)).toBe("Cash\n40.0%");
  });

  it("drops the share rather than drawing a second line the tile cannot hold", () => {
    // The name is not always the wider of the two lines: `VTI` is 21 px at the
    // 7 px character this estimate uses, and `30.0%` is 35. So a 1.43% tile of a
    // wide canvas — a short side of 34.4 px — holds the name and not the share
    // under it. The share is dropped, because the alternative is ECharts
    // truncating it to the tile (`30.…`), the fragment §2.9 bans.
    expect(tileText(WIDE, 0.0143, "VTI", "30.0%", true)).toBe("VTI");
    // At 4% the short side is 57.5 px, and the tile says both.
    expect(tileText(WIDE, 0.04, "VTI", "30.0%", true)).toBe("VTI\n30.0%");
  });

  it("withholds a name longer than the room the tile has", () => {
    const long = "Report Acct 1790445900682";
    expect(tileText(WIDE, 0.6, long, percent, true)).toBe(`${long}\n40.0%`);
    // 3% of a wide canvas: a short side around 50 px, short of the 56 px this
    // name needs — so it is withheld rather than drawn as `Report A…`.
    expect(tileText(WIDE, 0.03, long, percent, true)).toBe("");
    // The same 3% tile does have room for eight characters.
    expect(tileText(WIDE, 0.04, "Checking", percent, true)).toBe("Checking\n40.0%");
  });
});

describe("notDrawnSentence", () => {
  it("is null when the picture drew every row", () => {
    expect(notDrawnSentence([], "USD")).toBeNull();
  });

  it("names the rows it could not draw, with their figures", () => {
    const sentence = notDrawnSentence(treemapData(ROWS).skipped, "USD")!;
    expect(sentence).toContain("this row is not drawn");
    expect(sentence).toContain("SHORT (−$1,000.00)");
    expect(sentence).toContain("in the list below");
  });

  it("says rows, plural, for more than one", () => {
    const two = treemapData([
      ...ROWS.slice(0, 1),
      { key: "a", label: "Short A", value_base: "-5", percent: "0", holdings: 1, sources: [] },
      { key: "b", label: "Short B", value_base: "-6", percent: "0", holdings: 1, sources: [] },
    ] as unknown as AllocationRow[]).skipped;
    expect(notDrawnSentence(two, "USD")).toContain("these rows are not drawn");
  });
});

describe("treemapFinding", () => {
  it("states the total, the count, the largest by name, and what it left out", () => {
    const finding = treemapFinding(treemapData(ROWS), "29290.0000", "security", "Sep 20", "USD");
    expect(finding).toContain("Allocation by security, as of Sep 20");
    // The figure is the allocation's own total, not a sum taken here — and the
    // count is the six rows that total is made of, of which five are drawn.
    expect(finding).toContain("$29,290.00 across 6 groups, 5 of them drawn");
    expect(finding).toContain("Cash is the largest at $12,000.00 (40.0%)");
    expect(finding).toContain("SHORT (−$1,000.00)");
  });

  it("counts the groups the total is made of, and says how many of them it drew", () => {
    // Six rows and one total: five with area, and the short the picture cannot
    // draw. A count of the tiles alone would be a sentence whose own figures do
    // not add up to the total in front of them — the five tiles sum to $29,990
    // while the allocation is $28,990 — so the count is the rows the total is
    // made of, and the sentence says how much of that count is in the picture.
    const finding = treemapFinding(treemapData(CONSISTENT), "28990.0000", "security", "Sep 20", "USD");
    expect(finding).toContain("$28,990.00 across 6 groups, 5 of them drawn");
    expect(finding).toContain("SHORT (−$1,000.00)");
  });

  it("says nothing about drawing when it drew every group it counted", () => {
    const finding = treemapFinding(treemapData(ROWS.slice(0, 5)), "29990.0000", "security", "Sep 20", "USD");
    expect(finding).toContain("$29,990.00 across 5 groups.");
    expect(finding).not.toContain("of them drawn");
  });

  it("prints each share against the allocation's total, not against the ink", () => {
    // The tiles sum to $29,990.00, so Cash covers 40.0% of the drawn area and is
    // 41.4% of the allocation. The printed share is the one a reader can check
    // against the list under the chart, the detail sheet and the total beside
    // them; the ink is a shape, and the row the picture cannot draw is named
    // rather than folded into a denominator of its own.
    expect(label(CONSISTENT, WIDE).formatter({ name: "cash" })).toBe("Cash\n41.4%");
    const tooltip = treemapOption(treemapData(CONSISTENT), T, "USD", WIDE).tooltip as {
      formatter: (p: { name: string }) => string;
    };
    expect(tooltip.formatter({ name: "cash" })).toBe("Cash: $12,000.00 (41.4%)");
  });

  it("finds the largest rather than trusting the order it was handed", () => {
    const shuffled = [ROWS[2], ROWS[1], ROWS[0]] as unknown as AllocationRow[];
    expect(treemapFinding(treemapData(shuffled), "27000", "security", "Sep 20", "USD")).toContain(
      "Cash is the largest",
    );
  });

  it("says so plainly when there is nothing to draw", () => {
    expect(treemapFinding(treemapData([]), "0", "security", "Sep 20", "USD")).toBe(
      "Allocation by security, as of Sep 20: nothing to draw.",
    );
  });
});

describe("treemapOption", () => {
  it("is a flat treemap: no drill-down, no breadcrumb, no roaming", () => {
    const s = series(ROWS, WIDE) as { type: string; nodeClick: boolean; roam: boolean; breadcrumb: { show: boolean } };
    expect(s.type).toBe("treemap");
    expect(s.nodeClick).toBe(false);
    expect(s.roam).toBe(false);
    expect(s.breadcrumb.show).toBe(false);
  });

  it("separates tiles with the card behind them, and draws every row that has area", () => {
    const s = series(ROWS, WIDE) as {
      itemStyle: { gapWidth: number; borderColor: string };
      visibleMin: number;
    };
    expect(s.itemStyle.gapWidth).toBe(2);
    // The gaps are not empty canvas: ECharts paints every node's rectangle in
    // its stroke colour underneath the tile, and that underlay is what shows
    // through. Default `#fff` would draw a white grid over a dark card.
    expect(s.itemStyle.borderColor).toBe(T.surface);
    // ECharts hides a node under `visibleMin` px², and a hidden node is a row
    // missing from the picture with nothing saying so.
    expect(s.visibleMin).toBe(1);
  });

  it("keys its tiles, colours them by their row, and hands the share to the labels", () => {
    const s = series(ROWS, WIDE) as {
      data: { name: string; value: number; itemStyle: { color: string } }[];
    };
    expect(s.data.map((d) => d.name)).toEqual(["cash", "sec-vti", "sec-bnd", "sec-aapl", "sec-dust"]);
    expect(s.data.map((d) => d.itemStyle.color)).toEqual([
      "#123456", "#234567", "#345678", "#456789", "#56789a",
    ]);
    // By name, because ECharts counts `dataIndex` from a synthetic root at 0:
    // an index into this list would label every tile with the next row and
    // leave the largest one blank.
    expect(label(ROWS, WIDE).formatter({ name: "cash" })).toBe("Cash\n40.0%");
    expect(label(ROWS, WIDE).formatter({ name: "sec-bnd" })).toBe("BND\n20.0%");
    // The root is a node in the layout with no name, and must draw nothing.
    expect(label(ROWS, WIDE).formatter({ name: "" })).toBe("");
    expect(label(ROWS, WIDE).formatter({})).toBe("");
    expect(label(ROWS, WIDE).formatter({ name: "nothing" })).toBe("");
  });

  it("spotlights one tile by dimming the others, to a legible value", () => {
    // Read as an optional key, the way `netWorthChart.test` reads the strip's:
    // rule 8 watches test files too, and a bare `emphasis:` in a cast is the
    // hand-written interaction the rule is there to catch.
    const s = series(ROWS, WIDE) as {
      emphasis?: {
        focus?: string;
        blur?: { itemStyle?: { opacity?: number }; label?: { opacity?: number } };
      };
    };
    expect(s.emphasis?.focus).toBe("self");
    expect(s.emphasis?.blur?.itemStyle?.opacity).toBe(DIM);
    expect(s.emphasis?.blur?.label?.opacity).toBe(DIM);
  });

  it("prints money at full precision and the row's own share on the tooltip", () => {
    const tooltip = treemapOption(treemapData(ROWS), T, "USD", WIDE).tooltip as {
      trigger: string;
      formatter: (p: { name: string }) => string;
    };
    expect(tooltip.trigger).toBe("item");
    expect(tooltip.formatter({ name: "cash" })).toBe("Cash: $12,000.00 (40.0%)");
    expect(tooltip.formatter({ name: "sec-dust" })).toBe("DUST: $10.00 (<0.1%)");
    expect(tooltip.formatter({ name: "nothing" })).toBe("");
  });

  it("paints the name on the fill in the ink that reads on one", () => {
    expect(label(ROWS, WIDE).color).toBe(T.fillInk);
  });
});

describe("as ECharts draws it", () => {
  it("writes no fragment on any tile, at either width", () => {
    for (const box of [PHONE, WIDE]) {
      const { labels, texts, svg } = drawn(ROWS, box);
      for (const text of texts) expect(text).not.toContain("…");
      // The four tiles with room say their name; the speck does not, and is read
      // from the list under the chart instead.
      expect(labels.some(([first]) => first === "Cash")).toBe(true);
      expect(labels.some(([first]) => first === "VTI")).toBe(true);
      expect(texts).not.toContain("DUST");
      // Every tile is painted, whether or not it names itself: the tiles take the
      // first five palette slots, in the server's row order.
      for (const c of T.series.slice(0, 5)) expect(svg).toContain(c);
      // The row with no area is not in the picture at all.
      expect(svg).not.toContain("SHORT");
    }
  });

  it("draws the gaps between tiles on the card, not on ECharts' white default", () => {
    const { svg } = drawn(ROWS, WIDE);
    expect(svg).toContain(T.surface);
    // `#fff` is the treemap's default `borderColor` — the underlay a node paints
    // beneath its tile, which is what a gap shows.
    expect(svg).not.toContain("#fff");
  });

  it("writes the share on a wide tile, and never on a phone tile", () => {
    const wide = drawn(ROWS, WIDE);
    expect(wide.labels).toContainEqual(["Cash", "40.0%"]);
    expect(wide.labels).toContainEqual(["VTI", "30.0%"]);
    // The largest tile is labelled and not left blank — the failure mode of
    // reading a tile's label from `dataIndex`, which counts from the root.
    expect(wide.texts.filter((t) => t === "Cash")).toHaveLength(1);
    const phone = drawn(ROWS, PHONE);
    expect(phone.labels).toContainEqual(["Cash"]);
    expect(phone.texts.some((text) => text.includes("%"))).toBe(false);
  });

  it("withholds a name that the tile has no room for, rather than truncating it", () => {
    const { labels, texts } = drawn(ACCOUNTS, WIDE);
    // The 60% tile holds its 24-character name and its share.
    expect(labels).toContainEqual(["Report Acct 1790445900682", "60.0%"]);
    expect(labels).toContainEqual(["Brokerage", "9.7%"]);
    // The 30% tile does not, and that is the price of this rule rather than a
    // defect in it: the estimate allows a tile to be drawn as lopsided as 4:1,
    // which gives this one a short side of about 158 px against the 168 px its
    // name needs, and a label that *might* not fit is not drawn — ECharts would
    // otherwise print `Report Acct 17…` inside the tile, which is §2.9's
    // fragment. The row states its own figure in the list below.
    expect(texts).not.toContain("Report Acct 1790449427616");
    for (const text of texts) expect(text).not.toContain("…");
    // "Checking" is 0.33% of this canvas: no room even for its eight characters.
    expect(texts).not.toContain("Checking");
    expect(texts.some((t) => t.startsWith("Check"))).toBe(false);
    // The two tiles that do name themselves are the two the estimate gives room.
    expect(labels).toHaveLength(2);
  });
});
