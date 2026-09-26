// The allocation treemap, as an ECharts option built from the allocation and
// nothing else — the ring's sibling (`lib/donutChart.ts`), and a function of the
// canvas it is drawn on for the same reason.
//
// What the shape says: **a tile's area is its value**, so the picture answers
// "which group is big" without a reader comparing rows. Three things follow from
// that and are the whole of this module's opinion:
//
// * **A row with no positive value gets no tile.** ECharts lays a treemap out by
//   value; a short position (ADR-0032 keeps them rather than rounding them away)
//   has no area to draw and a negative one would invert the rectangle. It stays
//   in the list — which states its figure — and it is named in words under the
//   chart (`notDrawnSentence`), because a row missing from a picture that claims
//   to be the allocation must not go unmentioned.
// * **A tile names itself only if the tile can hold the name.** ECharts truncates
//   a treemap label to its own tile (`lineOverflow: "truncate"`, set in
//   `TreemapView`), so an over-long label does not collide with its neighbour —
//   it prints `Clo…` inside its tile, which is the fragment §2.9 bans. So the
//   decision is made here, before the layout runs, from the two things the layout
//   is a function of: the row's share of the total, and the box.
// * **Colour follows the row, not the rank.** Each tile's colour is stated per
//   item from the row's place in the list, so the swatch beside that row in the
//   `<ul>` (the chart's legend, and §2.9's rule that identity is never
//   colour-alone) is the same hue by construction, and a row that grows past its
//   neighbour keeps the hue it had.
import type { EChartsOption } from "echarts";
import type { AllocationRow, Money } from "@/api/types";
import { formatMoney } from "@/lib/format";
import { formatPercent } from "@/lib/investments";
import {
  chartTooltip,
  emphasisTreemap,
  phoneCanvas,
  type ChartBox,
  type TooltipPoint,
} from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

/** The rows a treemap can draw, and the rows it cannot. */
export interface TreemapData {
  tiles: AllocationRow[];
  skipped: AllocationRow[];
}

export function treemapData(rows: AllocationRow[]): TreemapData {
  const tiles: AllocationRow[] = [];
  const skipped: AllocationRow[] = [];
  for (const row of rows) (Number(row.value_base) > 0 ? tiles : skipped).push(row);
  return { tiles, skipped };
}

/**
 * A tile's colour: `chart.1…10` in the order the server lists the rows.
 *
 * The order is the server's, not the drawn one, so this is a function of the
 * *row* — the one property a legend has to have. Ten slots and no generated
 * eleventh hue: past ten the palette starts again, exactly as ECharts' own
 * palette does for the donut, and identity is not carried by colour alone here
 * anyway — the name is on the tile and in the list.
 */
export function tileColour(t: ChartTokens, index: number): string {
  return t.series[index % t.series.length];
}

/** What one character of a chart label takes, at the library's 12 px face. */
const CHAR_WIDTH = 7;
/** One line of label: a 12 px face plus its leading. */
const LINE = 16;
/** Two lines and the gap between them. */
const TWO_LINES = LINE * 2 + 2;
/**
 * How lopsided a tile is assumed to be drawn, for the room it is estimated to have.
 *
 * A tile of area `a` drawn no more lopsided than this has a short side of at
 * least `sqrt(a / MAX_ASPECT)`, and that is what its label's room is estimated
 * from. It is an assumption about the layout, not a theorem about rectangles: the
 * drawn demo household at 390 px holds one band at about 5:1, so a tile can come
 * out narrower than this estimate gives it.
 *
 * The value is a deliberate trade, checked against the drawn chart rather than
 * derived from the algorithm — the reason §2.9 gives about tick counts is that the
 * library's own answer is the thing, and squarify's is not a formula. Estimating
 * low costs a label on a tile that could have held its name (a 34% band on a
 * phone, whose account name is long); estimating high risks printing `Report A…`
 * inside a tile, which is the fragment §2.9 bans outright. So the estimate errs
 * low, and the list under the chart states every figure either way.
 */
const MAX_ASPECT = 4;

/**
 * The label a tile may draw: its name, its name and share, or nothing.
 *
 * `share` is the row's fraction of the tiles' total, and it is used *only* to
 * decide room — every figure a reader sees is formatted from the row's own
 * strings (`value_base`, `percent`), never from this division.
 */
export function tileText(
  box: ChartBox,
  share: number,
  name: string,
  percent: string,
  wide: boolean,
): string {
  const side = Math.sqrt((share * box.width * box.height) / MAX_ASPECT);
  if (side < LINE) return "";
  if (side < name.length * CHAR_WIDTH) return "";
  // A phone card gets the name only: the share is in the row below, and a second
  // line on a 310 px canvas is a line taken from the tile's neighbours.
  if (!wide) return name;
  // The share is a second line only where the tile holds *both* lines — and the
  // wider of the two is not always the name. `30.0%` is 35 px against `VTI`'s
  // 21, so a tile with room for the name can still be too narrow for the line
  // under it; the share is dropped rather than drawn into a truncation, which is
  // the fragment this function exists to prevent, arriving through the line
  // nobody checked because it was not the name.
  if (side >= TWO_LINES && side >= percent.length * CHAR_WIDTH) return `${name}\n${percent}`;
  return name;
}

/**
 * The rows the picture could not draw, in words — or null when it drew them all.
 *
 * The same contract as `excludedSentence` (null rather than an empty string,
 * because it renders in a live region and an empty one still announces) and the
 * same concern: a total that is missing something has to say so. It names the
 * rows and their figures, because "one row is not drawn" without a name is a
 * riddle.
 */
export function notDrawnSentence(skipped: AllocationRow[], ccy: string): string | null {
  if (skipped.length === 0) return null;
  const names = skipped.map((r) => `${r.label} (${formatMoney(r.value_base, ccy)})`).join(", ");
  const one = skipped.length === 1;
  return (
    `A treemap's area is value, so ${one ? "this row is" : "these rows are"} not drawn: ${names}.` +
    ` ${one ? "It is" : "They are"} in the list below, at the figure${one ? "" : "s"} shown.`
  );
}

export function treemapOption(
  data: TreemapData,
  t: ChartTokens,
  ccy: string,
  box: ChartBox,
): EChartsOption {
  // Layout, not money: the shares below decide how much room a tile has, and no
  // figure a reader sees is computed from them — the tooltip and the labels
  // print the row's own `value_base` and `percent`.
  const total = data.tiles.reduce((sum, r) => sum + Number(r.value_base), 0);
  const wide = !phoneCanvas(box);
  const rowOf = (key: unknown) => data.tiles.find((r) => r.key === key);
  // One decision per tile, taken together with the box it is drawn on and read
  // back by the formatter below — never re-derived inside ECharts' own layout.
  //
  // Keyed by the row's key, not by position: ECharts' treemap `data` carries a
  // synthetic **root** at index 0 (its own node for the whole area, whose name is
  // empty), so `dataIndex` counts from the root and every lookup by it is one
  // row out — the largest tile would draw the second row's name.
  const labels = new Map(
    data.tiles.map((r) => [
      r.key,
      tileText(box, total === 0 ? 0 : Number(r.value_base) / total, r.label, formatPercent(r.percent), wide),
    ]),
  );

  return {
    tooltip: chartTooltip(t, {
      trigger: "item",
      formatter: (p: TooltipPoint) => {
        const row = rowOf(p.name);
        if (!row) return "";
        return `${row.label}: ${formatMoney(row.value_base, ccy)} (${formatPercent(row.percent)})`;
      },
    }),
    series: [
      {
        type: "treemap",
        // No drill-down and no breadcrumb: every tile is a leaf, so `zoomToNode`
        // would zoom into a rectangle with nothing inside it and leave the reader
        // looking at one group with no way back but a tap the chart does not draw.
        nodeClick: false,
        roam: false,
        breadcrumb: { show: false },
        // The gap between tiles is the card behind them, as the donut's slice
        // borders are — stated as a 2 px gap rather than a border, so two
        // neighbours get two pixels between them rather than four. (`borderWidth`
        // would do the same insetting and is what ECharts documents as the tile
        // border; `gapWidth` is the one that means "space between two tiles".)
        //
        // `borderColor` is not decoration here: ECharts paints every node's own
        // rectangle in the item's stroke colour *underneath* its tile, and that
        // underlay is what shows through the gaps and the inset frame around the
        // whole treemap. The default is the CSS keyword `#fff`, which draws a
        // white grid over a dark card — so it is stated as the surface the figure
        // is drawn on, the same colour `Chart` gives the canvas.
        itemStyle: { gapWidth: 2, borderColor: t.surface },
        // ECharts hides a node whose area falls under this many px², and a node
        // it hides is a row missing from the picture with nothing saying so. At
        // 1 px² every row with area at all is drawn, so the only rows absent are
        // the ones without a positive value — which `notDrawnSentence` names.
        visibleMin: 1,
        // The name is drawn *on* the fill, so it takes the colour that reads on
        // one (`fillInk`): the axis and legend colour is the one thing it cannot
        // be, since it sits on the fill rather than beside it.
        label: {
          color: t.fillInk,
          // Empty for anything this list does not key — the root above all, which
          // is a node in the layout and not a row of the allocation.
          formatter: (p: { name?: string }) => labels.get(String(p.name ?? "")) ?? "",
        },
        emphasis: emphasisTreemap(),
        data: data.tiles.map((r, i) => ({
          name: r.key,
          value: Number(r.value_base),
          itemStyle: { color: tileColour(t, i) },
        })),
      },
    ],
  };
}

/**
 * The finding, for `Chart`'s required `label` — canvas is invisible, so this is
 * what a screen reader is told the picture says.
 *
 * It reads like the ring's sentence on the Insights overview: the total and how
 * many groups it is spread across, then the largest by name, because "which is
 * biggest" is the one thing a treemap is asked. The rows the picture could not
 * draw are named at the end rather than left to the visual.
 *
 * The total is passed in rather than summed here: it is the allocation's own
 * `total_base`, so the figure beside the chart and the figure in this sentence
 * are one number from one place, and no money is added up in the client.
 */
export function treemapFinding(
  data: TreemapData,
  total: Money,
  groupBy: string,
  asOf: string,
  ccy: string,
): string {
  const head = `Allocation by ${groupBy}, as of ${asOf}`;
  if (data.tiles.length === 0) return `${head}: nothing to draw.`;
  // Found rather than taken from the head of the list: the server happens to
  // list rows largest first today, and a sentence that says "the largest" must
  // not be a claim about someone else's sort order.
  const top = data.tiles.reduce((a, b) =>
    Number(b.value_base) > Number(a.value_base) ? b : a,
  );
  const n = data.tiles.length;
  const skipped = notDrawnSentence(data.skipped, ccy);
  return (
    `${head}: ${formatMoney(total, ccy)} across ${n} ${n === 1 ? "group" : "groups"}. ` +
    `${top.label} is the largest at ${formatMoney(top.value_base, ccy)} ` +
    `(${formatPercent(top.percent)}).` +
    (skipped ? ` ${skipped}` : "")
  );
}
