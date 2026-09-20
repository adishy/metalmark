// The window every report on the page is cut to, and how finely it is cut.
//
// The state lives in the URL, not in `useState`. A report is a thing people send
// each other — "here is last quarter" is a link — and all three charts on the
// page are functions of the same window, so the window belongs somewhere a link
// can carry it and a reload preserves. Nothing else on this page is linkable, and
// that asymmetry is deliberate: the owner filter is a lens for right now, while a
// window is a question you ask again tomorrow.
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { GranularityParam } from "@/api/types";
import {
  DEFAULT_GRANULARITY,
  DEFAULT_PRESET,
  GRANULARITY_CHOICES,
  MODES,
  granularityFromParams,
  isUsable,
  rangeFromParams,
  type RangeMode,
} from "@/lib/reportRange";

export interface ReportRangeState {
  mode: RangeMode;
  /** The window asked for. A `null` start means *all time*: the server opens it
   *  where the household's data does and echoes back the day it used. */
  start: string | null;
  end: string;
  granularity: GranularityParam;
  setMode: (mode: RangeMode) => void;
  setCustomStart: (day: string) => void;
  setCustomEnd: (day: string) => void;
  setGranularity: (g: GranularityParam) => void;
}

/**
 * The report window, read from and written to the address bar.
 *
 * `?range=<preset>`, or `?range=custom&start=…&end=…`, or nothing at all for the
 * default. Switching to a preset clears the dates rather than leaving them to
 * contradict it, so the URL never holds two answers to one question.
 */
export function useReportRange(): ReportRangeState {
  const [params, setParams] = useSearchParams();
  // Pinned once at mount: a report left open overnight keeps the window it was
  // opened with rather than silently re-scoping itself at midnight, and every
  // caller derives from this same "now", so the chips, the inputs and the
  // requests cannot disagree about what day it is.
  const today = useMemo(() => new Date(), []);
  const { mode, range } = rangeFromParams(params, today);

  /*
   * `replace`, not push. The chips are a lens on one page, and pushing a history
   * entry per press would put every way the page had been drawn between the
   * reader and whatever they came from. The URL still carries the window, so it
   * is still shareable; back leaves Reports instead of stepping through it.
   */
  const write = (next: Record<string, string | null>) => {
    const q = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null) q.delete(key);
      else q.set(key, value);
    }
    setParams(q, { replace: true });
  };

  return {
    mode,
    start: range.start,
    end: range.end,
    granularity: granularityFromParams(params),

    setMode: (next: RangeMode) => {
      if (next === "custom") {
        // Seed the inputs with the window already on screen, so choosing Custom
        // shows the reader where they were instead of two empty boxes. An open
        // start has no day to seed from, so the end serves as both — the reader
        // is about to type over it either way.
        write({ range: "custom", start: range.start ?? range.end, end: range.end });
        return;
      }
      // The default is spelled by the *absence* of the param, so a URL that says
      // nothing is the same URL as one that says "this-year", and the two share
      // a cache entry rather than being two keys for one window.
      write({
        range: next === DEFAULT_PRESET ? null : next,
        start: null,
        end: null,
      });
    },

    setCustomStart: (day: string) => {
      // An emptied `<input type="date">` fires with `""`. Ignoring it keeps the
      // last complete window on screen while the reader retypes, instead of
      // snapping the whole page back to the default preset mid-edit.
      if (!day) return;
      write({ range: "custom", start: day, end: range.end });
    },

    setCustomEnd: (day: string) => {
      if (!day) return;
      // `range.start` is non-null in custom mode (`rangeFromParams` only returns
      // custom when both sides parsed), but falling back to the day being set
      // keeps a degenerate one-day window rather than writing "null" as text.
      write({ range: "custom", start: range.start ?? day, end: day });
    },

    setGranularity: (g: GranularityParam) =>
      write({ granularity: g === DEFAULT_GRANULARITY ? null : g }),
  };
}

export default function RangeControl({ state }: { state: ReportRangeState }) {
  const { mode, start, end, granularity } = state;
  const usable = isUsable({ start, end });

  // `min-h-11` (44px) and `text-sm`, matching OwnerFilterChips: chips are thumb
  // targets (§4.5), and selection is carried by `aria-pressed` as well as the
  // fill, so it is never colour alone.
  const chip = (on: boolean) =>
    `inline-flex min-h-11 items-center rounded-full border px-4 text-sm ${
      on
        ? "border-accent bg-accent/20 font-medium text-accent"
        : "border-border-strong text-fg-muted hover:text-fg"
    }`;

  return (
    <div className="flex flex-wrap gap-x-8 gap-y-4" data-testid="range-control">
      <div>
        <p className="mb-2 text-xs font-medium text-fg-muted">Range</p>
        <div className="flex flex-wrap gap-2" data-testid="range-presets">
          {MODES.map((m) => {
            const on = mode === m.id;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => state.setMode(m.id)}
                aria-pressed={on}
                className={chip(on)}
                data-testid={`range-${m.id}`}
              >
                {m.label}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-fg-muted">Granularity</p>
        <div className="flex flex-wrap gap-2" data-testid="range-granularity">
          {GRANULARITY_CHOICES.map((g) => {
            const on = granularity === g.id;
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => state.setGranularity(g.id)}
                aria-pressed={on}
                className={chip(on)}
                data-testid={`granularity-${g.id}`}
              >
                {g.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* `range-dates`, not `range-custom`: that name belongs to the *chip* that
          selects this mode, and two elements answering to one testid is a
          strict-mode violation in every test that reaches for it. */}
      {mode === "custom" && (
        <div className="w-full" data-testid="range-dates">
          <div className="flex flex-wrap items-end gap-3">
            {/* Native date inputs. Their ISO `value` is the wire format the
                control element requires, not text anyone reads (§8 rule 9), and
                the picker is the one date UI that already knows the reader's
                locale, calendar and keyboard. */}
            <label className="text-xs font-medium text-fg-muted">
              From
              <input
                type="date"
                value={start ?? ""}
                onChange={(e) => state.setCustomStart(e.target.value)}
                className="mt-1 block min-h-11 rounded-control border border-border-strong bg-surface px-3 text-sm text-fg"
                data-testid="range-start"
              />
            </label>
            <label className="text-xs font-medium text-fg-muted">
              To
              <input
                type="date"
                value={end}
                onChange={(e) => state.setCustomEnd(e.target.value)}
                className="mt-1 block min-h-11 rounded-control border border-border-strong bg-surface px-3 text-sm text-fg"
                data-testid="range-end"
              />
            </label>
          </div>
          {!usable && (
            // Said here, in the control, next to the two fields that disagree.
            // The page does not try to draw an inverted window: the server
            // refuses one with a 422, and an empty chart would read as "this
            // household has no money" — a wrong answer to a question nobody
            // managed to ask.
            <p className="mt-2 text-xs text-negative" role="alert" data-testid="range-invalid">
              The start date is after the end date.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
