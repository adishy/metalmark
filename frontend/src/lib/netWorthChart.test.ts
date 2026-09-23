import { describe, expect, it } from "vitest";
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
