// The cash-flow graph as an ECharts option.
//
// Pure, like `netWorthChart.ts` and `sankey.ts` beside it, and split out of the page
// for the same reason: the shape of the picture is a decision, and a decision that
// can only be read on a canvas is a decision no test holds. What lives here is
// *where* the columns go and what a reader sees on them; what money moves through
// them is `buildSankey`'s, and the page's job is to hand the two together.
import type { EChartsOption } from "echarts";
import { formatMoney } from "@/lib/format";
import {
  LEFTOVER_NODE,
  SAVINGS_NODE,
  WINDOW_NODE,
  type SankeyGraph,
  type SankeyNode,
} from "@/lib/sankey";
import {
  chartLayout,
  chartTooltip,
  emphasisSankey,
  type ChartBox,
  type TooltipEdge,
  type TooltipPoint,
} from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

export function sankeyOption(
  graph: SankeyGraph,
  t: ChartTokens,
  ccy: string,
  box: ChartBox,
): EChartsOption {
  // Colour by *side*, not by position in the palette. A Sankey is read as a
  // direction of travel, so the two sides have to be told apart at a glance and
  // must not change colour when the window changes — which a cycling palette
  // would do, since a node's index moves as rows are added and removed. The
  // three nodes that are not a category at all (the window itself and the two
  // residual nodes) take the accent, which reads as "the household's own money"
  // rather than as one more source or destination.
  const color = (node: SankeyNode) =>
    node.name === WINDOW_NODE || node.name === SAVINGS_NODE || node.name === LEFTOVER_NODE
      ? t.accent
      : node.depth === 0
        ? t.positive
        : t.negative;

  // Read from the canvas the chart is actually drawn on, never from the window:
  // the same card is 310 px wide on a phone and 1104 px on a wide page, and the
  // margins that fit a label are a fact of one and not the other.
  const layout = chartLayout(box);

  return {
    tooltip: chartTooltip(t, {
      trigger: "item",
      // Node captions and the tooltip both need *words* where the graph stores
      // an id: an id has to be unique across the picture and a label does not,
      // so they are different strings and only one of them is readable.
      formatter: (p: TooltipPoint) => {
        const label = (id: unknown) => graph.labels.get(String(id)) ?? String(id);
        // The pointer can be on a node or on a ribbon, and ECharts types the
        // item as either. A link is the case with endpoints — its own `name` is
        // empty, and what it means is the two nodes it joins.
        const edge = p.data as TooltipEdge | null | undefined;
        if (edge?.source !== undefined && edge.target !== undefined) {
          return `${label(edge.source)} → ${label(edge.target)}: ${formatMoney(
            Number(edge.value),
            ccy,
          )}`;
        }
        return `${label(p.name)}: ${formatMoney(Number(p.value), ccy)}`;
      },
    }),
    series: [
      {
        type: "sankey",
        // Not the usual hairline inset: these margins are where the node labels
        // live. Each column's words are placed *outward* from its node — sources
        // to the left, destinations to the right — so a label sits beside the
        // graph instead of on top of a ribbon, which is where ECharts puts it by
        // default. The middle node's goes above it, having no margin of its own.
        left: layout.left,
        right: layout.right,
        // Deep enough for the middle column's label, which ECharts draws *above*
        // its node: at a hairline inset the word is cut in half by the canvas
        // edge, which reads as a rendering fault rather than as a crop.
        top: 26,
        bottom: 8,
        // Wide enough that a ribbon reads as a flow rather than as a hairline.
        nodeWidth: layout.nodeWidth,
        nodeGap: layout.nodeGap,
        data: graph.nodes.map((n) => ({
          name: n.name,
          depth: n.depth,
          itemStyle: { color: color(n) },
          label: { position: n.depth === 0 ? "left" : n.depth === 2 ? "right" : "top" },
        })),
        links: graph.links,
        label: {
          color: t.label,
          // Bounded so a long name cannot run back over the graph or off the
          // canvas — a household names its own categories, and one can be longer
          // than any margin. Truncation is the honest failure: the alternative is
          // a clipped word or a label over the ribbons, and the list under the
          // chart carries every name in full either way.
          width: layout.labelWidth,
          overflow: "truncate",
          formatter: (p: TooltipPoint) => graph.labels.get(String(p.name)) ?? "",
        },
        // `source` is not decoration: a ribbon is tinted by where it came from,
        // which is what makes "this came out of Salary" readable without a
        // legend. `curveness` keeps two flows from a shared origin apart.
        lineStyle: { color: "source", opacity: 0.4, curveness: 0.5 },
        emphasis: emphasisSankey(t),
      },
    ],
  };
}
