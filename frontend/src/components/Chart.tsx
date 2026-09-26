import { useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useReducedMotion } from "framer-motion";
import { useTheme } from "@/theme/theme";
import { chartMotion, type ChartBox } from "@/theme/chartInteraction";

// Thin ECharts wrapper. Charts are lenses over the same filter model; each page
// passes a fully-formed option built from `useChartTokens()` and the interaction
// helpers in `theme/chartInteraction.ts`.
//
// Three things this wrapper is responsible for, because a per-page chart would
// forget all three:
//
//   - `label` is required. Canvas is invisible to assistive technology, so the
//     wrapper carries `role="img"` and an accessible name. It must state the
//     *finding* ("Net worth rose to $12,340 in March"), not the chart type — a
//     screen reader user cannot see the shape being described. A chart is never
//     the only way to read a value (§2.9), so this complements a text rendering
//     rather than replacing one.
//   - `key={resolved}`. ECharts reads its colours once, at mount, into canvas
//     pixels — flipping the theme's CSS variables changes nothing it can see.
//     Re-mounting is what makes a theme switch repaint the chart.
//   - Motion. Merged here rather than in each option so no page has to remember
//     it, and so `prefers-reduced-motion` is honoured in one place. ECharts
//     animates in JS onto a canvas, which the CSS rule in index.css cannot
//     reach, so it has to be switched off explicitly (§2.8). A page that sets
//     its own `animation` still wins — this only supplies the default.
//
// `option` may be a function of the chart's own box. A chart has no viewport —
// only the width of the card it was put in, which is 310 px on a phone and 1104 px
// on a wide page for the *same* chart — so an option that has to place things in px
// takes the measurement from here, where the box is known, rather than guessing it
// from the window. Measured in a layout effect, before the first paint: a chart laid
// out for a width it does not have would draw, then jump to the one it does.
export default function Chart({
  option,
  label,
  height = 280,
  testid,
  onEvents,
}: {
  option: EChartsOption | ((box: ChartBox) => EChartsOption);
  /** The finding, in a sentence. Read aloud in place of the canvas. */
  label: string;
  height?: number;
  testid?: string;
  /**
   * ECharts' own events, as `echarts-for-react` takes them: one handler per
   * event name (`dataZoom`, `legendselectchanged`). The parameter is the
   * library's payload — `unknown`, so each handler narrows what it reads
   * (`zoomWindow()` in `theme/chartInteraction.ts` does that narrowing for the
   * one event this app listens to).
   *
   * **Memoise it at the call site.** The wrapper compares this prop by value on
   * every update and corrects the bindings when it differs: an object literal of
   * fresh arrow functions is a different value on every render, so the handler
   * would be unbound and rebound each time. That is not a leak — the library
   * unbinds by name — but it is churn the chart does not need, and the same
   * `useMemo`/`useCallback` the options already use costs one line.
   */
  onEvents?: Record<string, (params: unknown, instance: unknown) => void>;
}) {
  const { resolved } = useTheme();
  const reduced = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(Math.round(el.getBoundingClientRect().width));
    measure();
    // A card changes width without the window doing anything — the two-up grid at
    // `lg:` re-pairs, a sidebar collapses — so the box is watched, not sampled once.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const sized = useMemo(() => ({ width, height }), [width, height]);
  const resolvedOption = useMemo(
    () => (typeof option === "function" ? option(sized) : option),
    [option, sized],
  );
  const merged = useMemo(
    () => ({ ...chartMotion(!!reduced), ...resolvedOption }),
    [reduced, resolvedOption],
  );

  return (
    // `touch-action: pan-y`: a finger dragging up and down over a chart scrolls
    // the page, as it does everywhere else; only a tap (or a sideways drag along
    // the axis) belongs to the chart. Without it a chart the width of the phone
    // was a dead zone the page could not be scrolled from.
    <div ref={box} role="img" aria-label={label} data-testid={testid} style={{ touchAction: "pan-y" }}>
      {/* **Mount only once the box has been measured.** This is what removes the
          double-blink, and it is not a cosmetic guard — the chart is *fed* the
          measurement, so before it arrives there is no option to draw and the one
          that would be drawn is laid out for a zero-width canvas.

          Without the guard the sequence on every chart mount is:

            1. this component renders with `width: 0`, so `sized` is `{0, h}` and
               `resolvedOption` is an option laid out for a canvas with no width;
            2. `ReactECharts` mounts and `echarts-for-react` starts `echarts.init`
               on the container — and `init` is *asynchronous* here, because the
               library builds a throwaway instance, waits for its `finished` event,
               disposes it and builds the real one (`node_modules/echarts-for-react/
               lib/core.js::initEchartsInstance`);
            3. the layout effect above settles `width`, React re-renders, and the
               `option` prop is now a *different object* — so
               `componentDidUpdate` calls `setOption` on the instance that exists
               at that moment, which is the throwaway one. It draws, and its entry
               animation plays;
            4. that animation's `finished` is the event the library was waiting
               for: it disposes the instance — wiping the canvas mid-view — and
               builds the real one, which `setOption`s the same data and animates
               in from nothing a second time.

          Measured, on the built frontend: ink climbs to its full 35,785 px over
          200 ms, is wiped to 1,788 px, and climbs again — two complete entry
          animations, back to back.

          With the guard, step 1 is the only render that does not hand an option
          over, so nothing changes while `init` is pending: the throwaway instance
          is disposed before it ever draws, and the real instance is the first one
          to see an option. One animation. It costs no visible delay — the effect
          runs before the browser paints, so the measured render is still the
          first frame anyone sees. */}
      {width > 0 && (
        <ReactECharts
          key={resolved}
          option={merged}
          style={{ height, touchAction: "pan-y" }}
          opts={{ renderer: "canvas" }}
          notMerge
          onEvents={onEvents}
        />
      )}
    </div>
  );
}
