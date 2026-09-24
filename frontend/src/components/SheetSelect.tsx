// A choice from a short list, as a pill that opens a sheet.
//
// The phone's answer to a row of chips that does not fit and to the native
// <select>, which draws a different, unfinished-looking control on every
// platform. The pill says what is chosen ("Range · This year"); the sheet lists
// the choices as full-width, 48 px rows with a tick on the current one. On a
// phone the Dialog is a bottom sheet, so the list comes up under the thumb.
import { useState } from "react";
import Dialog from "@/components/Dialog";
import { CheckIcon, ChevronDownIcon } from "@/components/icons";

export interface SheetOption<T extends string> {
  id: T;
  label: string;
  hint?: string;
}

export default function SheetSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  testid,
  className,
}: {
  /** What is being chosen — "Range", "Section". The sheet's title too. */
  label: string;
  value: T;
  options: readonly SheetOption<T>[];
  onChange: (id: T) => void;
  testid: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.id === value);
  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className={`inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-border-strong bg-surface-raised px-4 text-sm ${className ?? ""}`}
        data-testid={testid}
      >
        <span className="shrink-0 text-fg-muted">{label}</span>
        <span className="truncate font-medium text-fg">{current?.label ?? "—"}</span>
        <ChevronDownIcon className="ml-auto size-4 shrink-0 text-fg-muted" />
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title={label} testid={`${testid}-sheet`}>
        <ul className="-mx-2" role="listbox" aria-label={label}>
          {options.map((o) => {
            const on = o.id === value;
            return (
              <li key={o.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => {
                    onChange(o.id);
                    setOpen(false);
                  }}
                  className={`flex min-h-12 w-full items-center gap-3 rounded-control px-3 text-left text-base ${
                    on ? "bg-accent/15 font-medium text-accent-ink" : "hover:bg-surface-inset"
                  }`}
                  data-testid={`${testid}-option-${o.id}`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{o.label}</span>
                    {o.hint && <span className="block text-xs text-fg-muted">{o.hint}</span>}
                  </span>
                  {on && <CheckIcon className="size-5 shrink-0" />}
                </button>
              </li>
            );
          })}
        </ul>
      </Dialog>
    </>
  );
}
