// Cash flow as a graph: two sets of buckets, and the one node between them.
//
// The server sends **rows, not a graph** — grouping is the arithmetic, and the
// shape follows from it mechanically. That split is deliberate: a layout sent
// over the wire is a decision no reader could check, and it would put the
// grouping rule in two places. So the grouping arrives as data and this module is
// the only place the picture is built.
//
// Pure and React-free on purpose. A canvas cannot be read back in jsdom, so the
// only part of this chart a test can actually hold is the graph itself — which is
// exactly the part where a bug would be invisible on screen (a link to the wrong
// node still draws a plausible picture).
import type { CashFlowSankey } from "@/api/types";

/** The node every flow passes through. */
export const WINDOW_NODE = "window";
/** Where the money came from when the window spent more than it took in. */
export const SAVINGS_NODE = "from-savings";
/** Where it went when it took in more than it spent. */
export const LEFTOVER_NODE = "left-over";

export interface SankeyNode {
  /**
   * The node's **id**, unique across the whole graph — which is why it is not the
   * label. ECharts keys sankey nodes by name, so two nodes sharing one name are
   * merged into a single node with the flows added together: a category that
   * appears on both sides (a purchase and a refund) would silently become one, and
   * the picture would balance while saying something untrue. `labels` maps these
   * ids back to words.
   */
  name: string;
  /** 0 = where money came from, 1 = the window, 2 = where it went. */
  depth: number;
}

export interface SankeyLink {
  source: string;
  target: string;
  value: number;
}

export interface SankeyGraph {
  nodes: SankeyNode[];
  links: SankeyLink[];
  /** Node id → the words a reader sees, for captions and the tooltip alike. */
  labels: Map<string, string>;
  /**
   * What the middle node carries: the larger of the two sides, which is the
   * window's total throughput once the residual node is added. Both sides equal
   * this by construction, so the picture has no dangling end.
   */
  flow: number;
}

/**
 * True when the window has no flow at all — nothing in and nothing out.
 *
 * Not exported: `buildSankey` returning `null` *is* this answer, and a second way
 * to ask the same question is a second thing to keep in step. A caller that needs
 * to know asks the builder.
 */
function isEmpty(data: CashFlowSankey): boolean {
  return Number(data.total_income) <= 0 && Number(data.total_expense) <= 0;
}

export interface SankeySideRow {
  key: string;
  label: string;
  total: number;
}

/**
 * One side of the graph as a list — the chart's **text equivalent** (§2.9).
 *
 * Read off the graph rather than off the payload, so the list and the picture
 * cannot disagree: the residual node ("From savings" / "Left over") is in the
 * graph and not in the payload, and a list built from the payload would silently
 * omit the very row that makes the two sides balance. Totals are summed from the
 * links, which is what the ribbons are drawn with.
 */
export function sideRows(graph: SankeyGraph, side: "in" | "out"): SankeySideRow[] {
  const depth = side === "in" ? 0 : 2;
  return graph.nodes
    .filter((node) => node.depth === depth)
    .map((node) => ({
      key: node.name,
      label: graph.labels.get(node.name) ?? node.name,
      total: graph.links
        .filter((link) => (side === "in" ? link.source === node.name : link.target === node.name))
        .reduce((sum, link) => sum + link.value, 0),
    }));
}

/**
 * Build the graph, or `null` when the window has no money moving in it.
 *
 * A window with income and no spending still draws: all of it flows to "Left
 * over", which is a true statement about the window rather than a degenerate
 * picture. The mirror case — spending with no income — draws "From savings".
 *
 * **It balances by construction, and that is the whole reason `net` is computed
 * here rather than trusted.** The left side carries `total_income` plus the
 * shortfall when there is one; the right carries `total_expense` plus the surplus.
 * Both are `max(income, expense)`, from two different expressions, so a sign slip
 * in either would leave the graph with a dangling end — visible immediately, and
 * asserted in the test.
 */
export function buildSankey(data: CashFlowSankey): SankeyGraph | null {
  if (isEmpty(data)) return null;

  const income = Number(data.total_income);
  const expense = Number(data.total_expense);
  const net = income - expense;

  const nodes: SankeyNode[] = [];
  const links: SankeyLink[] = [];
  const labels = new Map<string, string>();
  const push = (name: string, label: string, depth: number) => {
    nodes.push({ name, depth });
    labels.set(name, label);
  };

  for (const row of data.income) push(`in:${row.key}`, row.label, 0);
  // Only when it is real: a zero-value node is a node with nothing behind it, and
  // `net === 0` is the ordinary case of a window that paid for itself.
  if (net < 0) push(SAVINGS_NODE, "From savings", 0);
  push(WINDOW_NODE, "This window", 1);
  for (const row of data.expense) push(`out:${row.key}`, row.label, 2);
  if (net > 0) push(LEFTOVER_NODE, "Left over", 2);

  for (const row of data.income) {
    links.push({ source: `in:${row.key}`, target: WINDOW_NODE, value: Number(row.total) });
  }
  if (net < 0) links.push({ source: SAVINGS_NODE, target: WINDOW_NODE, value: -net });
  for (const row of data.expense) {
    links.push({ source: WINDOW_NODE, target: `out:${row.key}`, value: Number(row.total) });
  }
  if (net > 0) links.push({ source: WINDOW_NODE, target: LEFTOVER_NODE, value: net });

  return { nodes, links, labels, flow: Math.max(income, expense) };
}
