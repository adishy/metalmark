// The multi-select sibling of `SheetSelect`: the same pill, opening the same
// kind of panel, for a filter that narrows by *several* values at once.
//
// Why a sibling and not a `multiple` prop on `SheetSelect`: a listbox is a
// single-choice contract. `role="option"` + `aria-selected` means "one of these
// is the value", and the moment two can be selected the roles have to become
// something else or they lie to whoever is listening. Rather than make one
// component report two different things depending on a boolean, this one uses
// the app's actual selection control (§4.14): label rows wrapping checkboxes, a
// fill *and* a tick for checked, the row as the target.
//
// The panel is a `Dialog`, so it is a bottom sheet under 640 px and a centred
// modal above (§4.8) — at `lg:` a filter panel is never a bottom sheet (§9.6).
// Picks apply as they are made and the panel stays open, because a multi-select
// that closed on the first tick would be a single-select with extra steps; the
// state lives in the page, not in here, so closing the panel cannot lose it.
//
// One deliberate difference from `SheetSelect`'s pill: this one takes an accent
// border while something is selected. Its text is still the state — "2 selected"
// is the honest, non-colour signal (§4.5) — but a count is not a name, and a
// count of two reads as a count of zero at a glance. The mark is what makes
// "this filter is on" scannable without reading.
import { useState } from "react";
import Dialog from "@/components/Dialog";
import { Button, Checkbox } from "@/components/form";
import { ChevronDownIcon } from "@/components/icons";
import type { SheetOption } from "@/components/SheetSelect";

export default function SheetMultiSelect<T extends string>({
  label,
  values,
  options,
  onToggle,
  onClear,
  emptyLabel = "All",
  emptyNote = "Nothing to choose from yet.",
  testid,
  className,
}: {
  /** What is being chosen — "Accounts", "Categories". The panel's title too. */
  label: string;
  /** The selected ids. Empty is the default and is not a filter. */
  values: readonly T[];
  options: readonly SheetOption<T>[];
  onToggle: (id: T) => void;
  /** Offered in the panel's footer once something is selected. */
  onClear?: () => void;
  /** What the pill says when nothing is selected. */
  emptyLabel?: string;
  /** What the panel says when it has no options at all. */
  emptyNote?: string;
  testid: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const chosen = options.filter((o) => values.includes(o.id));
  // The pill's summary is the *state* of the filter, in words: "All", the one
  // value, or how many. That is what keeps the pill from carrying its selection
  // in colour alone (§4.5), and what makes it readable to a screen reader,
  // whose only view of the pill is its text.
  const summary =
    chosen.length === 0
      ? emptyLabel
      : chosen.length === 1
        ? chosen[0].label
        : `${chosen.length} selected`;

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className={`inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border bg-surface-raised px-4 text-sm ${
          chosen.length > 0 ? "border-accent" : "border-border-strong"
        } ${className ?? ""}`}
        data-testid={testid}
      >
        <span className="shrink-0 text-fg-muted">{label}</span>
        <span className="truncate font-medium text-fg">{summary}</span>
        <ChevronDownIcon className="ml-auto size-4 shrink-0 text-fg-muted" />
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={label}
        testid={`${testid}-sheet`}
        footer={
          <>
            {onClear && chosen.length > 0 && (
              <Button variant="ghost" onClick={onClear} data-testid={`${testid}-clear`}>
                Clear
              </Button>
            )}
            <Button
              variant="secondary"
              onClick={() => setOpen(false)}
              data-testid={`${testid}-done`}
            >
              Done
            </Button>
          </>
        }
      >
        {/* A `Checkbox` row per option, not a listbox of `role="option"`s: these
            answer one question together, and §4.14's control is what says so.
            The panel's own heading is the group label — it is already the
            dialog's `aria-labelledby` — so a `<fieldset>` inside it would
            repeat the same words twice. */}
        <div className="space-y-1">
          {options.map((o) => (
            <Checkbox
              key={o.id}
              label={o.label}
              hint={o.hint}
              checked={values.includes(o.id)}
              onChange={() => onToggle(o.id)}
              data-testid={`${testid}-option-${o.id}`}
            />
          ))}
          {options.length === 0 && <p className="text-sm text-fg-muted">{emptyNote}</p>}
        </div>
      </Dialog>
    </>
  );
}
