// A horizontal strip of tabs that scrolls sideways on a phone instead of
// wrapping or squeezing — the one shape DESIGN.md §5 allows to scroll
// sideways at all, because a picker reads worse than tabs when there are this
// many sections and a swipeable row keeps "where am I" visible. Extracted
// from Settings.tsx (session 09), which was the only caller before Insights
// became the second: same fade mask, same roving-tabindex/arrow-key model,
// same scroll-selected-into-view. Both callers carry `data-scroll-x-ok`
// through this one component, which is what makes it the sanctioned strip
// rather than a pattern every new tab list reinvents.
import { useEffect, useRef } from "react";
import { useIsDesktop } from "@/lib/media";

export interface ScrollTab {
  id: string;
  label: string;
}

export default function ScrollTabs({
  tabs,
  selected,
  onSelect,
  ariaLabel,
  testidPrefix,
  rail = false,
}: {
  tabs: readonly ScrollTab[];
  selected: string;
  onSelect: (id: string) => void;
  /** `aria-label` on the `role="tablist"` element. */
  ariaLabel: string;
  /** `data-testid` per tab is `${testidPrefix}-${id}`. */
  testidPrefix: string;
  /**
   * Becomes a vertical rail at `lg:` instead of staying a horizontal strip
   * (Settings' §9.3 layout — the tab list is the first column, the panel the
   * second). Default `false`: a plain scrolling strip at every width, which
   * is what Insights uses (its tabs are driven by one short declared list,
   * not eleven sections, so a rail buys it nothing).
   */
  rail?: boolean;
}) {
  // §9.3's rail is the one thing a media query alone cannot decide: `aria-
  // orientation` has to say which axis is on screen, and that is an ARIA
  // attribute, not a style, so no class can carry it. The hook always runs
  // (Rules of Hooks) even for a caller that never uses a rail; `rail` gates
  // the result instead of the call.
  const isDesktop = useIsDesktop();
  const isRail = rail && isDesktop;

  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Keep the chosen tab in view in the phone's swiping strip.
  useEffect(() => {
    const i = tabs.findIndex((t) => t.id === selected);
    tabRefs.current[i]?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
    // `tabs` is a fresh array identity on most renders (callers tend to
    // declare it inline or filter it per-render); keying on `selected` alone
    // is what the roving-tabindex model below already assumes.
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  // Roving tabindex: the tablist is one tab stop and the arrow keys move
  // inside it. Declaring role="tablist" without that model announces a tab
  // widget that ignores the keys a screen reader user will reach for
  // (docs/DESIGN.md §7.4).
  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    const i = tabs.findIndex((t) => t.id === selected);
    const last = tabs.length - 1;
    let next: number;
    // Both pairs move the same way — Right/Down forward, Left/Up back — in
    // both presentations. ARIA's own guidance names the pair that matches the
    // axis and is silent on the other; accepting both costs one `||` and
    // saves a reader who guessed wrong from concluding the tablist is broken.
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = i === last ? 0 : i + 1;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = i === 0 ? last : i - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else return;
    e.preventDefault();
    // Automatic activation: selecting on focus saves the Enter that manual
    // activation would cost, and every current caller swaps a local panel
    // rather than navigating away.
    onSelect(tabs[next].id);
    tabRefs.current[next]?.focus();
  }

  return (
    <div
      data-scroll-x-ok
      className={
        "-mx-4 flex snap-x gap-1 overflow-x-auto border-b border-border px-4 " +
        "[mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)] " +
        "[scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:[mask-image:none] " +
        (rail ? "lg:w-56 lg:flex-col lg:flex-nowrap lg:border-b-0" : "")
      }
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation={isRail ? "vertical" : "horizontal"}
    >
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            tabRefs.current[i] = el;
          }}
          type="button"
          role="tab"
          id={`tab-${t.id}`}
          aria-selected={selected === t.id}
          aria-controls={`panel-${t.id}`}
          tabIndex={selected === t.id ? 0 : -1}
          onClick={() => onSelect(t.id)}
          onKeyDown={onKeyDown}
          // The `lg:` overrides ride on Tailwind's own order — every variant
          // block is emitted after the unvariant utilities — so `lg:border-b-0`
          // and `lg:rounded-control` beat `border-b-2` and `rounded-t-lg`
          // without a `!` or a duplicated branch.
          className={
            "inline-flex min-h-11 shrink-0 snap-start items-center rounded-t-lg px-3 text-sm whitespace-nowrap " +
            (rail ? "lg:justify-start lg:rounded-control lg:border-b-0 " : "") +
            (selected === t.id
              ? "border-b-2 border-accent text-fg " + (rail ? "lg:bg-surface-inset lg:font-semibold lg:text-fg" : "")
              : "text-fg-muted hover:text-fg " + (rail ? "lg:hover:bg-surface-inset lg:hover:text-fg" : ""))
          }
          data-testid={`${testidPrefix}-${t.id}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
