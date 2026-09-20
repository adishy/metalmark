// How a date reaches the screen. Two components, because there are two frames
// and picking the wrong one is the bug this pair exists to prevent.
//
// The rule they enforce is the one a call site would otherwise have to remember:
// **the ISO form is never the visible text.** It is the `title` (hover, and on
// focus where the date sits inside a focusable row) and the `<time datetime>`
// value (the machine-readable one, and what a screen reader reads out of the
// element's semantics). What a person reads is the vocabulary from `lib/dates`.
//
// ```tsx
// <Day value={txn.transacted_at} style="compact" />   // a calendar day
// <Instant value={run.started_at} style="relative" /> // a moment
// ```
//
// `<Day>` and `<Instant>` rather than one component with a `kind` prop: the
// choice of frame is the decision, and it should be visible in the JSX that made
// it. See `lib/dates.ts` for what each frame means.
import type { ComponentProps } from "react";
import {
  calendarDay,
  formatDay,
  formatInstant,
  formatTime,
  type DateStyle,
  type DayStyle,
} from "@/lib/dates";

/**
 * Everything a `<time>` takes except the four props these components own.
 *
 * `style` is in that list: on a component whose whole job is to choose a
 * *date style*, `style` is the vocabulary (`<Day style="compact">`) and not the
 * inline CSS attribute. `className` is still there for the CSS.
 */
type BaseProps = Omit<ComponentProps<"time">, "children" | "dateTime" | "title" | "style">;

interface DayProps extends BaseProps {
  /** An ISO value naming a calendar day: `2026-09-20`, or any instant whose
   *  date half is the day meant. */
  value: string;
  style?: DayStyle;
}

interface InstantProps extends BaseProps {
  /** An ISO instant. Rendered in the viewer's local time. */
  value: string;
  style?: DateStyle;
}

/**
 * A calendar day. `title` and `datetime` carry the day as `YYYY-MM-DD` — the
 * precision the value actually has, so hovering a bank import does not claim a
 * time of day it never had.
 */
export function Day({ value, style = "medium", ...rest }: DayProps) {
  const day = calendarDay(value);
  return (
    <time dateTime={day} title={day} {...rest}>
      {formatDay(value, style)}
    </time>
  );
}

/**
 * An instant. `title` carries the full ISO timestamp, because that is the
 * precision this value has and the reason to hover it.
 */
export function Instant({ value, style = "medium", ...rest }: InstantProps) {
  return (
    <time dateTime={value} title={value} {...rest}>
      {formatInstant(value, style)}
    </time>
  );
}

interface TimeProps extends BaseProps {
  /** An ISO instant. Rendered as the viewer's local time of day. */
  value: string;
  /** Seconds matter in a log, where two events can share a minute. Off for a
   *  timestamp read as a value rather than as a position in a sequence. */
  seconds?: boolean;
}

/**
 * The clock time of an instant, for the one surface that needs the time and not
 * the date: a run's event log. Fixed 24-hour and fixed-width, so the column
 * stays a column (DESIGN.md §6.3), and the full ISO still sits in `title`.
 */
export function Time({ value, seconds = true, ...rest }: TimeProps) {
  return (
    <time dateTime={value} title={value} {...rest}>
      {formatTime(value, seconds)}
    </time>
  );
}
