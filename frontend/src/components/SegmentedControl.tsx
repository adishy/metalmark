// A segmented control, built the way `Settings.tsx` builds its tab strip: real
// buttons carrying `role="tab"` inside a `role="tablist"`, with a roving
// tabindex so the group is one tab stop and the arrow keys move inside it.
//
// It is a component rather than a class string because the keyboard behaviour is
// the part that is easy to leave out and impossible to notice: declaring
// `role="tablist"` without the roving model announces a tab widget that ignores
// the keys a screen reader user will reach for (docs/DESIGN.md §7.4). Activation
// is automatic — the panel is a local swap, so selecting on focus saves the
// Enter that manual activation would cost.
//
// Hand-built: no Radix, no CVA, no clsx, like every other control in this app
// (§4.14).

import { useRef, type KeyboardEvent, type ReactNode } from "react";

export interface Segment<T extends string> {
  id: T;
  label: ReactNode;
}

/** The `id` of the tab button for a segment, so its panel can point back at it
 *  with `aria-labelledby`. */
export function segmentTabId(testid: string, id: string): string {
  return `${testid}-${id}`;
}

/** The `id` of the panel a segment selects. Every segment gets one even though
 *  only the selected panel is mounted, which is the shape `Settings.tsx` uses —
 *  the alternative is an `aria-controls` that resolves to nothing. */
export function segmentPanelId(testid: string, id: string): string {
  return `${testid}-panel-${id}`;
}

export default function SegmentedControl<T extends string>({
  label,
  segments,
  value,
  onChange,
  testid,
  className,
}: {
  /** The group's accessible name — "Settings sections", "Group allocation by". */
  label: string;
  segments: readonly Segment<T>[];
  value: T;
  onChange: (id: T) => void;
  /** Also the prefix of every id this control emits, so ids stay deterministic
   *  and two controls on one page cannot collide. */
  testid: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = segments.findIndex((s) => s.id === value);
  const last = segments.length - 1;

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    let next: number;
    if (e.key === "ArrowRight") next = current === last ? 0 : current + 1;
    else if (e.key === "ArrowLeft") next = current === 0 ? last : current - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    else return;
    e.preventDefault();
    // The roving index walks `segments` — the list actually rendered — so it
    // cannot land on a segment that is not there.
    onChange(segments[next].id);
    refs.current[next]?.focus();
  }

  return (
    <div
      className={`flex flex-wrap gap-1 border-b border-border ${className ?? ""}`}
      role="tablist"
      aria-label={label}
      data-testid={testid}
    >
      {segments.map((s, i) => (
        <button
          key={s.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          // Not a submit button: these sit inside pages that contain forms, and
          // a tab that submits one is a bug with no visible cause.
          type="button"
          role="tab"
          id={segmentTabId(testid, s.id)}
          aria-selected={value === s.id}
          aria-controls={segmentPanelId(testid, s.id)}
          tabIndex={value === s.id ? 0 : -1}
          onClick={() => onChange(s.id)}
          onKeyDown={onKeyDown}
          className={`inline-flex min-h-11 shrink-0 items-center rounded-t-lg px-3 text-sm whitespace-nowrap ${
            value === s.id ? "border-b-2 border-accent text-fg" : "text-fg-muted hover:text-fg"
          }`}
          data-testid={`${testid}-${s.id}`}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
