// The spending donut, as an ECharts option built from the report and nothing else.
//
// Pure, and a function of the canvas it is drawn on for the reason the Sankey is
// (`lib/sankeyChart.ts`): a chart has no viewport, only the box its card gave it,
// and one option has to serve a 310 px phone card and a 1104 px page. What the
// measured box decides here is text.
//
// * **A phone card draws no slice labels.** ECharts places a pie's outside labels
//   at the end of their leader lines and clips each one to the room left inside the
//   canvas, so on a phone the labels arrived as `Clo…`, `Ut…`, and — for the slice
//   whose leader ends nearest the edge — a bare `…`. A label a reader cannot read is
//   not a label. The ring keeps its shape and its colours; the names are in the
//   legend, in the tap tooltip, and in the list of categories and totals printed
//   under the chart, which is the donut's own text equivalent (§2.9). Measured on
//   the demo household at 390 and 360 px: every one of those channels names all six
//   categories, while the leader labels named three and a fragment.
// * **The wide layout is untouched.** At 1104 px all six labels are drawn whole
//   ("Groceries" … "Uncategorized") and the legend is a single page, so nothing
//   there needed changing.
import type { EChartsOption } from "echarts";
import type { CategorySpendRow } from "@/api/types";
import { formatMoney } from "@/lib/format";
import {
  chartLegend,
  chartTooltip,
  emphasisPie,
  phoneCanvas,
  type ChartBox,
  type TooltipPoint,
} from "@/theme/chartInteraction";
import type { ChartTokens } from "@/theme/chartTokens";

/**
 * How a ring's slices are labelled on a canvas this size — the rule, in one place,
 * for every ring in the app (this chart, and the sample on the design-system page).
 *
 * Named on a wide canvas; silent on a phone card, where ECharts would clip each
 * label to whatever room its leader line ended in. The leader line goes with the
 * label: `PieView` keeps a slice's guide line on the strength of the label's
 * *position*, not its visibility, so hiding the text alone leaves strokes pointing
 * at nothing.
 */
export function sliceLabels(
  t: ChartTokens,
  box: ChartBox,
  formatter?: (p: TooltipPoint) => string,
) {
  if (phoneCanvas(box)) return { label: { show: false }, labelLine: { show: false } };
  return { label: { color: t.label, ...(formatter ? { formatter } : {}) } };
}

export function donutOption(
  rows: CategorySpendRow[],
  t: ChartTokens,
  ccy: string,
  box: ChartBox,
): EChartsOption {
  const total = rows.reduce((sum, r) => sum + Number(r.total), 0);
  // Slices are named by the row's **key**, not by the words a reader sees, for the
  // reason the graph gives: ECharts merges data items that share a name, so two rows
  // with the same label would become one slice with their totals added — a chart
  // that balances against a legend that is wrong. Two categories may share a name,
  // and a household may name a category "Investment fees" while also paying real
  // ones. The names live in this map instead, and the legend, the tooltip and the
  // labels all read them back out.
  const nameOf = (key: unknown) =>
    rows.find((r) => r.key === key)?.category_name ?? String(key);
  return {
    tooltip: chartTooltip(t, {
      trigger: "item",
      formatter: (p: TooltipPoint) => {
        const value = Number(p.value);
        const share = total === 0 ? 0 : (value / total) * 100;
        // `share` replaces ECharts' `{d}`, which is only available to the
        // template-string form — and the template cannot map a key to a name.
        return `${nameOf(p.name)}: ${formatMoney(value, ccy)} (${share.toFixed(0)}%)`;
      },
    }),
    legend: chartLegend(t, { bottom: 0, type: "scroll", formatter: nameOf }),
    color: t.series,
    series: [
      {
        type: "pie",
        radius: ["45%", "70%"],
        center: ["50%", "45%"],
        // The gap between slices is the card behind them, not a fixed navy.
        itemStyle: { borderColor: t.surface, borderWidth: 2 },
        ...sliceLabels(t, box, (p: TooltipPoint) => nameOf(p.name)),
        emphasis: emphasisPie(t),
        data: rows.map((r) => ({ name: r.key, value: Number(r.total) })),
      },
    ],
  };
}
