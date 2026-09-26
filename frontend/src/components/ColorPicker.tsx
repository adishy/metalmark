// The colour control for the two records that carry one: a category and a tag.
//
// Replaces a bare `<input type="color">`, which on Linux and Windows opens a
// modal OS dialog over a colour wheel the app has no say over, reads as an
// unstyled rectangle in the middle of a form, and offers no way to reach the
// app's own palette — the colours the charts beside it are drawn in.
//
// What it is instead: a radio group of the ten categorical series colours,
// read from the same CSS custom properties the charts read (`theme/chartTokens`)
// so the palette cannot drift from index.css, plus the native control as the
// last resort for a value that is not in the palette.
//
// The platform still owns the semantics, the way it does for Checkbox: these
// are real radios sharing a `name`, so the roving tabindex, the arrow keys, the
// checked state and the announced "2 of 11" all come from the browser, and the
// value stays a hex string because that is what `categories.color` stores.
import { useId, useMemo } from "react";
import { tokenHex } from "@/theme/chartTokens";
import { useTheme } from "@/theme/theme";

/** The ten series slots, named the way index.css names the hue behind each. */
const PALETTE = [
  { token: "chart-1", name: "Teal" },
  { token: "chart-2", name: "Sky" },
  { token: "chart-3", name: "Indigo" },
  { token: "chart-4", name: "Pink" },
  { token: "chart-5", name: "Amber" },
  { token: "chart-6", name: "Emerald" },
  { token: "chart-7", name: "Red" },
  { token: "chart-8", name: "Violet" },
  { token: "chart-9", name: "Blue" },
  { token: "chart-10", name: "Orange" },
];

/*
 * The radio *is* the swatch: `appearance-none` restyles the platform's control
 * rather than replacing it, the same way Checkbox does, so the checked state,
 * the arrow keys within the group and the radio role all stay the browser's.
 *
 * It is the whole 44 px target rather than a `sr-only` input inside a `<label>`
 * the way ThemeToggle does it. That pattern needs the label to be the visual
 * because a segment has text to show; here the swatch has no text at all, so
 * the input can be the drawing *and* the thing you click. (A clipped 1×1 input
 * is also not clickable at all: pointer events land on whatever is painted
 * over it, which Playwright reports as "intercepts pointer events" and a person
 * never notices, because the label forwards the click either way.)
 *
 * The selected marker is an outline with an offset, never a tick inside the
 * swatch: a tick would have to be white or black, and this palette holds both
 * pale amber and near-black teal, so one of the two is always under 3:1 on its
 * own fill. The offset leaves a ring of card between the swatch and the marker,
 * which reads on every colour in the set.
 */
const SWATCH =
  "size-11 shrink-0 cursor-pointer appearance-none rounded-full border border-border-strong " +
  "checked:outline checked:outline-2 checked:outline-offset-2 checked:outline-fg " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** The escape hatch: any colour at all, through the platform's own picker. */
const CUSTOM =
  "size-11 shrink-0 cursor-pointer rounded-control border border-border-strong bg-surface-inset p-0.5";

export default function ColorPicker({
  label,
  value,
  onChange,
  testid,
  className,
}: {
  /** The group's name, rendered as a `<legend>`: "Color". */
  label: string;
  /** `#rrggbb`, or "" for nothing chosen yet. */
  value: string;
  onChange: (hex: string) => void;
  /** Prefix for every id this control emits, so two on a page cannot collide. */
  testid: string;
  className?: string;
}) {
  const { resolved } = useTheme();
  const groupName = useId();
  // Keyed on `resolved` for the reason `useChartTokens()` documents: apply()
  // flips the class on <html> before it notifies React, so by the time this
  // re-reads, the computed values are already the new theme's. A swatch is
  // therefore the colour this theme's charts draw, and the value it stores is
  // the one you were looking at.
  const options = useMemo(
    () =>
      PALETTE.flatMap((p) => {
        const hex = tokenHex(p.token);
        return hex ? [{ ...p, hex }] : [];
      }),
    [resolved],
  );

  const chosen = options.find((o) => o.hex === value.toLowerCase());

  return (
    <fieldset className={className} data-testid={testid}>
      <legend className="mb-1.5 block text-xs font-medium text-fg-muted">{label}</legend>
      {/* Wraps rather than scrolls: on a phone the strip is two rows, and the
          page never gains a horizontal scrollbar (§5). */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-3">
        {options.map((o) => (
          <input
            key={o.token}
            type="radio"
            name={groupName}
            // The name is the only thing the swatch can say for itself: a bare
            // colour is nothing to a screen reader, and this is what makes the
            // group announce "Teal, 1 of 11".
            aria-label={o.name}
            value={o.hex}
            checked={o.hex === value.toLowerCase()}
            onChange={() => onChange(o.hex)}
            className={SWATCH}
            style={{ backgroundColor: o.hex }}
            data-testid={`${testid}-${o.token}`}
          />
        ))}
        <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-xs text-fg-muted">
          <input
            type="color"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className={CUSTOM}
            data-testid={`${testid}-custom`}
          />
          Custom
        </label>
      </div>
      {/* The value in words. A ring says "one of these" to a sighted user and
          nothing at all to anyone else, and the ten swatches are otherwise
          indistinguishable without sight (1.4.1); this is also what announces
          the change, since the radios themselves are never re-announced when
          the arrow keys move the selection. */}
      <p className="mt-1.5 text-xs text-fg-muted" role="status" data-testid={`${testid}-current`}>
        {chosen
          ? `Selected: ${chosen.name} · ${value.toLowerCase()}`
          : value
            ? `Selected: Custom · ${value.toLowerCase()}`
            : "No colour chosen yet."}
      </p>
    </fieldset>
  );
}
