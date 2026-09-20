import { describe, it, expect } from "vitest";
import type { CashFlowSankey, CashFlowSankeyRow } from "@/api/types";
import {
  LEFTOVER_NODE,
  SAVINGS_NODE,
  WINDOW_NODE,
  buildSankey,
  sideRows,
  type SankeyGraph,
} from "@/lib/sankey";

// The graph is the only part of this chart a test can hold: a canvas cannot be
// read back in jsdom, and every failure mode below draws a *plausible* picture —
// a link to the wrong node, two nodes merged into one — which is exactly why the
// arithmetic is split out into a pure module and pinned here.

/** One server row. `total` is a magnitude; the side it is in carries direction. */
const r = (key: string, label: string, total: string): CashFlowSankeyRow => ({
  key,
  label,
  category_id: null,
  total,
});

/**
 * A response, with the three figures the server computes stated **by hand**.
 *
 * Deliberately not derived from the rows: the module under test recomputes the
 * graph from these, so a fixture that summed its own rows would agree with a
 * server that had sent the wrong total. Writing them out is what makes the two
 * independent.
 */
function sankey({
  income = [],
  expense = [],
  total_income,
  total_expense,
}: {
  income?: CashFlowSankeyRow[];
  expense?: CashFlowSankeyRow[];
  total_income: string;
  total_expense: string;
}): CashFlowSankey {
  return {
    base_currency: "USD",
    start: "2026-01-01",
    end: "2026-01-31",
    income,
    expense,
    total_income,
    total_expense,
    net: "unused-by-the-builder",
    attribution: "row",
    warnings: [],
  };
}

/**
 * What actually passes through the middle node, read off the ribbons.
 *
 * The property that makes the picture drawable at all: a Sankey whose two sides
 * carry different totals has a dangling end, and `buildSankey`'s docstring claims
 * it cannot happen. Both figures are `max(income, expense)` by construction, from
 * two different expressions — so a sign slip in either one shows up here.
 */
function throughput(g: SankeyGraph): { in: number; out: number } {
  const sum = (v: number[]) => v.reduce((a, b) => a + b, 0);
  return {
    in: sum(g.links.filter((l) => l.target === WINDOW_NODE).map((l) => l.value)),
    out: sum(g.links.filter((l) => l.source === WINDOW_NODE).map((l) => l.value)),
  };
}

const names = (g: SankeyGraph) => g.nodes.map((n) => n.name);

/** What a node carries, which for a residual node is the only place it is stated. */
function ribbon(g: SankeyGraph, node: string): number {
  const link = g.links.find((l) => l.source === node || l.target === node);
  return link?.value ?? NaN;
}

describe("buildSankey", () => {
  it("builds no graph for a window with nothing moving in it", () => {
    // A window where money sat still is not a degenerate picture to be drawn
    // defensively — there is no picture, and a chart of zero-width ribbons would
    // claim otherwise.
    expect(buildSankey(sankey({ total_income: "0", total_expense: "0" }))).toBeNull();
  });

  it("sends what was not spent to a residual node, so both sides carry the same total", () => {
    const g = buildSankey(
      sankey({
        income: [r("salary", "Salary", "2000.0000")],
        expense: [r("cat:food", "Groceries", "245.0000")],
        total_income: "2000.0000",
        total_expense: "245.0000",
      }),
    )!;

    expect(names(g)).toEqual(["in:salary", WINDOW_NODE, "out:cat:food", LEFTOVER_NODE]);
    expect(g.nodes.map((n) => n.depth)).toEqual([0, 1, 2, 2]);
    expect(g.links).toEqual([
      { source: "in:salary", target: WINDOW_NODE, value: 2000 },
      { source: WINDOW_NODE, target: "out:cat:food", value: 245 },
      { source: WINDOW_NODE, target: LEFTOVER_NODE, value: 1755 },
    ]);
    expect(g.flow).toBe(2000);
    expect(throughput(g)).toEqual({ in: 2000, out: 2000 });
  });

  it("draws the shortfall as its own source when the window spent more than it took in", () => {
    const g = buildSankey(
      sankey({
        income: [r("salary", "Salary", "100.0000")],
        expense: [r("cat:rent", "Rent", "250.0000")],
        total_income: "100.0000",
        total_expense: "250.0000",
      }),
    )!;

    expect(names(g)).toEqual(["in:salary", SAVINGS_NODE, WINDOW_NODE, "out:cat:rent"]);
    expect(g.nodes.map((n) => n.depth)).toEqual([0, 0, 1, 2]);
    expect(throughput(g)).toEqual({ in: 250, out: 250 });
    // The middle holds the larger side, not the income: this is the case where
    // the two differ, so it is the case that would catch `flow: income`.
    expect(g.flow).toBe(250);
  });

  it("draws neither residual node when the window paid for itself", () => {
    // `net === 0` is an ordinary month, not an edge case. A zero-value node is a
    // node with nothing behind it, and the graph must not invent one.
    const g = buildSankey(
      sankey({
        income: [r("salary", "Salary", "100.0000")],
        expense: [r("cat:rent", "Rent", "100.0000")],
        total_income: "100.0000",
        total_expense: "100.0000",
      }),
    )!;

    expect(names(g)).not.toContain(SAVINGS_NODE);
    expect(names(g)).not.toContain(LEFTOVER_NODE);
    expect(throughput(g)).toEqual({ in: 100, out: 100 });
  });

  it("draws a window that only took money in, and one that only paid out", () => {
    // Both are true statements about a window rather than reasons to draw nothing.
    const only = buildSankey(
      sankey({ income: [r("salary", "Salary", "900.0000")], total_income: "900.0000", total_expense: "0" }),
    )!;
    expect(ribbon(only, LEFTOVER_NODE)).toBe(900);
    expect(throughput(only)).toEqual({ in: 900, out: 900 });

    const out = buildSankey(
      sankey({ expense: [r("cat:rent", "Rent", "900.0000")], total_income: "0", total_expense: "900.0000" }),
    )!;
    expect(ribbon(out, SAVINGS_NODE)).toBe(900);
    expect(throughput(out)).toEqual({ in: 900, out: 900 });
  });

  it("gives two rows that share a label a node each", () => {
    // The bug this module exists to prevent, and the reason a node's id is not
    // its label: ECharts keys sankey nodes by name, so two nodes called
    // "Uncategorized" are *merged* — the amounts add together and the picture
    // balances while quietly misstating where the money went. A household can
    // name a category "Uncategorized" and can have unfiled rows at the same time.
    const g = buildSankey(
      sankey({
        // Breaks even, so no residual node joins the expense side: this test is
        // about the rows, and one more node on the depth would only be noise.
        income: [r("salary", "Salary", "50.0000")],
        expense: [r("uncategorized", "Uncategorized", "20.0000"), r("cat:misc", "Uncategorized", "30.0000")],
        total_income: "50.0000",
        total_expense: "50.0000",
      }),
    )!;

    const out = g.nodes.filter((n) => n.depth === 2).map((n) => n.name);
    expect(out).toEqual(["out:uncategorized", "out:cat:misc"]);
    // Both still read the same to a human — the ids are doing the separating, not
    // the words, which is the whole arrangement.
    expect(out.map((n) => g.labels.get(n))).toEqual(["Uncategorized", "Uncategorized"]);
    expect(out.map((n) => g.links.find((l) => l.target === n)!.value)).toEqual([20, 30]);
  });

  it("keeps a category that appears on both sides apart", () => {
    // A purchase and a refund in one category are two flows going opposite ways.
    // The side prefix is what stops ECharts from merging them, and merging them
    // would draw the difference while telling the reader nothing happened.
    const g = buildSankey(
      sankey({
        income: [r("cat:refunds", "Refunds", "30.0000"), r("salary", "Salary", "100.0000")],
        expense: [r("cat:refunds", "Refunds", "80.0000")],
        total_income: "130.0000",
        total_expense: "80.0000",
      }),
    )!;

    expect(names(g)).toContain("in:cat:refunds");
    expect(names(g)).toContain("out:cat:refunds");
    // Stated as an invariant rather than as the two names above, because a
    // duplicate anywhere in the graph is the same bug wherever it comes from.
    expect(new Set(names(g)).size).toBe(g.nodes.length);
  });
});

describe("sideRows", () => {
  it("lists a side off the graph, including the residual row the payload never had", () => {
    const g = buildSankey(
      sankey({
        income: [r("salary", "Salary", "100.0000")],
        expense: [r("cat:rent", "Rent", "250.0000")],
        total_income: "100.0000",
        total_expense: "250.0000",
      }),
    )!;

    // "From savings" is in the picture and not in the payload. A text equivalent
    // built from the payload would silently omit the one row that makes the two
    // sides balance — the figure without which the list contradicts the graph.
    expect(sideRows(g, "in")).toEqual([
      { key: "in:salary", label: "Salary", total: 100 },
      { key: SAVINGS_NODE, label: "From savings", total: 150 },
    ]);
    expect(sideRows(g, "out")).toEqual([{ key: "out:cat:rent", label: "Rent", total: 250 }]);
  });

  it("totals each side to the same figure, read off the ribbons", () => {
    const g = buildSankey(
      sankey({
        income: [r("salary", "Salary", "2000.0000")],
        expense: [r("cat:food", "Groceries", "245.0000")],
        total_income: "2000.0000",
        total_expense: "245.0000",
      }),
    )!;

    const total = (side: "in" | "out") => sideRows(g, side).reduce((sum, row) => sum + row.total, 0);
    expect(total("in")).toBe(2000);
    expect(total("out")).toBe(2000);
    expect(total("out")).toBe(g.flow);
  });
});
