// Reusable, accessible form primitives. Every control is paired with a visible
// <label> tied via htmlFor/id. Inputs render an inline error region tied via
// aria-describedby. Styling comes from the design tokens (docs/DESIGN.md §4).
import {
  cloneElement,
  forwardRef,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ChangeEvent,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { CheckIcon, ChevronDownIcon } from "@/components/icons";
import { useIsPhone } from "@/lib/media";

/*
 * `min-h-11` (44 px) is the target floor, not the text box's natural height:
 * `px-3 py-2 text-sm` computes to 36 px and is below both our floor and
 * WCAG 2.5.5. `text-base` (16 px) is not a style choice either — iOS Safari
 * zooms the whole viewport when a control under 16 px takes focus, and the page
 * never zooms back out.
 */
// No `outline-none` here. It is a no-op today — the global `:focus-visible` rule
// in index.css has equal specificity and comes later in source order, so it wins
// — but it only stays a no-op while that ordering holds. Move the focus rule into
// `@layer components` and every control in the app silently loses its ring. The
// border change below is an *addition* to the ring, never a replacement for it.
const CONTROL =
  "w-full min-w-0 max-w-full min-h-11 rounded-control border border-border-strong bg-surface-inset px-3 text-base text-fg " +
  "placeholder:text-fg-muted focus:border-accent " +
  "disabled:opacity-50 aria-[invalid=true]:border-negative";

/**
 * Label + control + optional hint or inline error.
 *
 * The error wiring is done by cloning the child rather than by asking all ~60
 * call sites to pass `aria-describedby` themselves: every one of them already
 * follows `<Field htmlFor={id}><Input id={id} /></Field>`, so threading it here
 * makes the association impossible to omit rather than merely conventional.
 */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  required,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string | null;
  hint?: ReactNode;
  required?: boolean;
  className?: string;
  children: ReactNode;
}) {
  // Hint and error share one slot: an error replaces the hint rather than
  // stacking a second line beneath it.
  const describedBy = error ? `${htmlFor}-error` : hint ? `${htmlFor}-hint` : undefined;

  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
        // The real attribute, not just the asterisk: a screen reader announces
        // "required" from this, and the asterisk alone is decorative.
        ...(required ? { required: true } : {}),
      })
    : children;

  return (
    <div className={`space-y-1 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-fg-muted">
        {label}
        {required && (
          <span aria-hidden="true" className="ml-0.5 text-negative">
            *
          </span>
        )}
      </label>
      {control}
      {hint && !error && (
        <p id={`${htmlFor}-hint`} className="text-xs text-fg-muted">
          {hint}
        </p>
      )}
      {error && (
        // role="alert" so it is announced without moving focus (WCAG 4.1.3).
        // An inline error that only changes colour is silent.
        <p
          id={`${htmlFor}-error`}
          role="alert"
          className="text-sm text-negative"
          data-testid={`${htmlFor}-error`}
        >
          <span aria-hidden="true">⚠ </span>
          {error}
        </p>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={`${CONTROL} ${className ?? ""}`} {...props} />;
  },
);

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { inline?: boolean }
>(
  function Select({ className, children, inline = false, ...props }, ref) {
    // The browser's own arrow is drawn differently on every platform and reads
    // as unfinished; ours is the app's chevron, in the muted tone, with the
    // control's padding making room for it. The wrapper carries the width.
    return (
      <span className={`relative min-w-0 ${inline ? "inline-block" : "block w-full"}`}>
        <select
          ref={ref}
          className={`${CONTROL} appearance-none truncate pr-10 ${className ?? ""}`}
          {...props}
        >
          {children}
        </select>
        <ChevronDownIcon className="pointer-events-none absolute top-1/2 right-3 size-5 -translate-y-1/2 text-fg-muted" />
      </span>
    );
  },
);

/** One choice in a `Combobox`. */
export interface ComboboxOption {
  value: string;
  /** The text the control shows once picked — "🛒 Groceries", "Alice". */
  label: string;
  /** A quieter trailing note ("2 accounts"): never the only place a meaning lives. */
  hint?: string;
}

type ComboboxProps = {
  id: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly ComboboxOption[];
  /**
   * The popup's accessible name. Required rather than defaulted: the visible
   * `<label>` names the input, and a listbox called "Options" tells a screen
   * reader nothing about which list it has opened.
   */
  listLabel: string;
  placeholder?: string;
  className?: string;
} & Pick<
  InputHTMLAttributes<HTMLInputElement>,
  // The props `Field` injects into whatever control it wraps, plus what a form
  // control carries. Listed rather than spread from `InputHTMLAttributes` in
  // full, because on a phone this renders a `<select>` and an input's event
  // handler types are not a select's — a passthrough type that only one of the
  // two presentations can accept is a type that lies about the component.
  "disabled" | "name" | "required" | "aria-label" | "aria-describedby" | "aria-invalid"
>;

/**
 * The searchable picker — what a `<select>` becomes once its list outgrows a
 * glance (§4.4).
 *
 * The native `<select>` is right for a handful of options and wrong for a
 * category list of seventy: its popup is drawn by the OS, it cannot be filtered,
 * and on a desktop it is a modal list that closes on the first pick. This is a
 * text input with a listbox under it, so typing narrows the list, and the value,
 * the search and the keyboard all live in the one control.
 *
 * **On a phone it renders the native `<select>` instead**, deliberately. The
 * platform picker is the control a thumb, a screen reader and the device's own
 * zoom already know (§4.4, §5), and a popover that hijacks it would be a
 * downgrade; the phone's answer to a long list is a sheet with a search field
 * (`SheetSelect`, `CategoryPicker`). The switch is by behaviour, not by CSS, so
 * it is `useIsPhone` rather than a `sm:` class — which is also what keeps the
 * e2e phone specs walking the same control they always walked.
 *
 * The APG combobox/listbox pattern is implemented in full, because §4.4 says a
 * custom picker must: `role="combobox"` + `aria-expanded` + `aria-controls` +
 * `aria-activedescendant` on the input, `role="listbox"`/`role="option"` +
 * `aria-selected` on the list, arrow keys, Home/End, Enter to commit, Escape to
 * dismiss, Tab to leave without committing, and type-ahead that filters.
 */
export function Combobox({ placeholder, listLabel, ...rest }: ComboboxProps) {
  const isPhone = useIsPhone();
  if (isPhone) return <ComboboxNative {...rest} />;
  return <ComboboxListbox placeholder={placeholder} listLabel={listLabel} {...rest} />;
}

/** The phone's presentation of a `Combobox`: today's `Select`, unchanged. */
function ComboboxNative(props: Omit<ComboboxProps, "placeholder" | "listLabel">) {
  const { id, value, onChange, options, className, ...rest } = props;
  return (
    <Select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={className}
      {...rest}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </Select>
  );
}

/** How tall the popup may grow before it scrolls: `max-h-72`. */
const LISTBOX_MAX_PX = 288;

function ComboboxListbox({
  id,
  value,
  onChange,
  options,
  listLabel,
  placeholder,
  className,
  ...rest
}: ComboboxProps) {
  const box = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  // Whether the text in the box is a *search* rather than the value. It is not
  // inferred from the text, because the label a person is looking at is not a
  // query: opening the list on "🛒 Groceries" must show every category.
  const [typed, setTyped] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [drop, setDrop] = useState<"down" | "up">("down");
  /*
   * How tall the list may be where it opens, when that is less than
   * `max-h-72`. A `Dialog` body scrolls, and `overflow-y-auto` clips what
   * overflows it *above* the scroll origin with no way to scroll back to it:
   * a 288 px list flipped up inside a 140 px body is a list whose upper rows
   * cannot be reached at all, by mouse or by keyboard. Bounding the popup to
   * the room it actually has turns that into a list that scrolls inside
   * itself. `null` means "not measured" — jsdom has no layout, and the class
   * alone is the bound there.
   */
  const [room, setRoom] = useState<number | null>(null);

  /*
   * The highlight as the keyboard handlers have to see it.
   *
   * React batches state updates, so two keys delivered before a re-render would
   * both read the previous render's `active` and the second would move from the
   * wrong row — holding ArrowDown would step once, not down the list. Every path
   * that moves the highlight writes this ref and the state together, so handlers
   * read what the last keystroke did rather than what the last paint showed.
   */
  const highlighted = useRef<string | null>(null);
  const highlight = (next: string | null) => {
    highlighted.current = next;
    setActive(next);
  };

  const listId = `${id}-listbox`;
  const selected = options.find((o) => o.value === value);
  const label = selected?.label ?? "";

  const matches = (o: ComboboxOption, q: string) =>
    `${o.label} ${o.hint ?? ""}`.toLocaleLowerCase().includes(q);
  const needle = typed ? query.trim().toLocaleLowerCase() : "";
  const rows = needle ? options.filter((o) => matches(o, needle)) : options;

  const activeIndex = active === null ? -1 : rows.findIndex((o) => o.value === active);
  const activeId = open && activeIndex >= 0 ? `${listId}-opt-${activeIndex}` : undefined;

  /*
   * Which way the list opens.
   *
   * Measured rather than assumed, because the control's own height says nothing
   * about the space under it: the same field sits near the bottom of a phone
   * sheet and in the middle of a desktop page. A `Dialog`'s body scrolls, so the
   * bound is the nearest scrollable ancestor rather than the viewport — a list
   * flipped against the viewport can still open into a clipped region.
   *
   * jsdom has no layout at all: every rect is zero and nothing is scrollable, so
   * this lands on "down" and the tests see a list in the only direction there is.
   */
  const measure = () => {
    const el = box.current;
    if (!el || typeof el.getBoundingClientRect !== "function") return;
    const rect = el.getBoundingClientRect();
    const viewport = typeof window === "undefined" ? LISTBOX_MAX_PX : window.innerHeight;
    let top = 0;
    let bottom = viewport;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const overflowY = typeof getComputedStyle === "function" ? getComputedStyle(p).overflowY : "";
      if (overflowY === "auto" || overflowY === "scroll") {
        const r = p.getBoundingClientRect();
        top = Math.max(top, r.top);
        bottom = Math.min(bottom, r.bottom);
        break;
      }
    }
    const below = bottom - rect.bottom;
    const up = below < LISTBOX_MAX_PX && rect.top - top > below;
    setDrop(up ? "up" : "down");
    // The room on the side it just chose, less the 4 px gap the list keeps
    // from the control (`mt-1` / `mb-1`).
    setRoom(Math.min(LISTBOX_MAX_PX, Math.max((up ? rect.top - top : below) - 4, 0)));
  };

  const show = (edge: "first" | "last" | "selected") => {
    const at =
      edge === "first" ? 0 : edge === "last" ? options.length - 1 : selected ? options.indexOf(selected) : -1;
    setOpen(true);
    setTyped(false);
    setQuery(label);
    highlight(options[at]?.value ?? null);
    measure();
    // Select what is already there, so the first keystroke replaces the value
    // instead of appending to it.
    box.current?.select();
  };

  const hide = () => {
    setOpen(false);
    setTyped(false);
    setQuery(label);
    highlight(null);
  };

  const commit = (next: string) => {
    onChange(next);
    hide();
    // A click on a row must leave the keyboard on the control it just changed,
    // or the next Tab starts from the top of the dialog.
    box.current?.focus();
  };

  const search = (text: string) => {
    setQuery(text);
    setTyped(true);
    const q = text.trim().toLocaleLowerCase();
    // Typing highlights the top match, so Enter commits what the user is looking
    // at rather than the row that used to be under the cursor.
    highlight((q ? options.find((o) => matches(o, q)) : options[0])?.value ?? null);
  };

  const onInput = (e: ChangeEvent<HTMLInputElement>) => {
    const next = e.target.value;
    if (open) {
      search(next);
      return;
    }
    // Typing into the closed control starts a fresh search rather than appending
    // to the label it is showing — otherwise "gro" after "🛒 Groceries" reads as
    // "🛒 Groceriesgro" and matches nothing.
    setOpen(true);
    measure();
    search(next.startsWith(label) ? next.slice(label.length) : next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const move = (to: "first" | "last" | "down" | "up") => {
      if (rows.length === 0) return;
      const from =
        highlighted.current === null ? -1 : rows.findIndex((o) => o.value === highlighted.current);
      const next =
        to === "first"
          ? 0
          : to === "last"
            ? rows.length - 1
            : to === "down"
              ? from < 0
                ? 0
                : (from + 1) % rows.length
              : from < 0
                ? rows.length - 1
                : (from - 1 + rows.length) % rows.length;
      highlight(rows[next].value);
    };

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        // Closed, the arrow *opens* the list — the native `<select>`'s own
        // behaviour, and the reason the control is reachable without a mouse.
        if (!open) show("first");
        else move("down");
        return;
      case "ArrowUp":
        e.preventDefault();
        if (!open) show("last");
        else move("up");
        return;
      case "Home":
        if (!open) return;
        e.preventDefault();
        move("first");
        return;
      case "End":
        if (!open) return;
        e.preventDefault();
        move("last");
        return;
      case "Enter": {
        if (!open) return; // Let the form submit; the list is not open.
        const value = highlighted.current;
        if (value === null) return;
        e.preventDefault();
        commit(value);
        return;
      }
      case "Escape":
        if (!open) return;
        // Dismiss the list, not the sheet it is in: a Dialog listens for Escape
        // on the document, and this event must not reach it.
        e.preventDefault();
        e.stopPropagation();
        hide();
        return;
      case "Tab":
        // Leaving abandons an unfinished search. A half-typed query is not a
        // value, and Tab must not commit one.
        if (open) hide();
        return;
      default:
        return;
    }
  };

  // The highlighted row has to be visible. `scrollIntoView` is absent in jsdom,
  // hence the optional call.
  useEffect(() => {
    if (!activeId) return;
    document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);

  return (
    <span className={`relative block min-w-0 ${className ?? ""}`}>
      <input
        {...rest}
        id={id}
        ref={box}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={activeId}
        aria-autocomplete="list"
        autoComplete="off"
        spellCheck={false}
        className={`${CONTROL} truncate pr-10`}
        placeholder={placeholder}
        value={open ? query : label}
        onChange={onInput}
        onKeyDown={onKeyDown}
        onClick={() => {
          if (!open) show("selected");
        }}
        onBlur={() => {
          if (open) hide();
        }}
      />
      <ChevronDownIcon className="pointer-events-none absolute top-1/2 right-3 size-5 -translate-y-1/2 text-fg-muted" />

      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={listLabel}
          // The pointer must never take focus off the input: the input owns the
          // keyboard and `aria-activedescendant` names the highlighted row, which
          // is exactly why these rows are not buttons.
          onMouseDown={(e) => e.preventDefault()}
          style={room === null ? undefined : { maxHeight: room }}
          className={`absolute left-0 right-0 z-50 max-h-72 overflow-y-auto rounded-control border border-border-strong bg-surface-raised py-1 shadow-lg ${
            drop === "up" ? "bottom-full mb-1" : "top-full mt-1"
          }`}
        >
          {rows.length === 0 && (
            <li role="presentation" className="px-3 py-2 text-sm text-fg-muted">
              No matches
            </li>
          )}
          {rows.map((o, i) => {
            const on = o.value === value;
            return (
              // Presentational wrapper: a listbox may hold options, not list
              // items, so the `<li>` that gives the popup its rows carries no
              // role of its own.
              <li key={o.value} role="presentation">
                <div
                  role="option"
                  id={`${listId}-opt-${i}`}
                  aria-selected={on}
                  onClick={() => commit(o.value)}
                  // Highlight follows the pointer as well as the keyboard, so
                  // what Enter would commit is always what is under the cursor.
                  onMouseMove={() => highlight(o.value)}
                  className={`flex min-h-11 cursor-pointer items-center gap-2 px-3 text-base ${
                    on
                      ? "bg-accent/15 font-medium text-accent-ink"
                      : i === activeIndex
                        ? "bg-surface-inset text-fg"
                        : "text-fg"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">{o.label}</span>
                  {o.hint && <span className="shrink-0 text-xs text-fg-muted">{o.hint}</span>}
                  {/* Selected is a shape as well as a tint: a 20 % wash is not
                      something every eye can pick out of a scrolled list. */}
                  {on && <CheckIcon className="size-4 shrink-0" />}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </span>
  );
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={`${CONTROL} ${className ?? ""}`} {...props} />;
  },
);

/**
 * A checkbox with its label, as one component.
 *
 * The label *is* the target: a native checkbox's own box is ~13 px, and the
 * whole row is what a thumb actually hits, so the row carries `min-h-11` and the
 * box is a 20 px visual inside it. The input stays a real `<input
 * type="checkbox">` — `appearance-none` restyles it, it does not replace it, so
 * the checked state, the keyboard behaviour and the accessible name all still
 * come from the platform. The tick is a sibling `<svg>` revealed by
 * `peer-checked:`, because a background-image would need a data URI in a class.
 */
export function Checkbox({
  label,
  hint,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-start gap-3 text-sm text-fg ${className ?? ""}`}
    >
      <span className="relative mt-2 grid size-5 shrink-0 place-items-center">
        <input
          type="checkbox"
          className="peer size-5 appearance-none rounded border border-border-strong bg-surface-inset checked:border-accent checked:bg-accent disabled:opacity-50"
          {...props}
        />
        <CheckIcon className="pointer-events-none absolute size-3.5 text-accent-fg opacity-0 peer-checked:opacity-100" />
      </span>
      <span className="min-w-0 py-2">
        {label}
        {hint && <span className="block text-xs text-fg-muted">{hint}</span>}
      </span>
    </label>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:opacity-90",
  secondary: "border border-border-strong bg-surface-inset text-fg hover:bg-surface-raised",
  ghost: "text-fg-muted hover:bg-surface-inset hover:text-fg",
  // `danger` is a theme-independent fill (6.47:1 against white in both themes).
  // It belongs *inside a confirmation only* — never as the first thing a user
  // sees, and never competing with the primary action (§4.1).
  danger: "bg-danger text-white hover:opacity-90",
};

const BUTTON =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-control px-4 text-sm font-medium " +
  "transition-colors duration-state disabled:opacity-50 disabled:pointer-events-none";

// No default `type` here, deliberately. `<Button type="submit">` is used inside
// eight forms across the app; defaulting to `type="button"` would quietly turn
// every one of them into a button that does nothing.
export function Button({
  variant = "primary",
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button className={`${BUTTON} ${VARIANTS[variant]} ${className ?? ""}`} {...props}>
      {children}
    </button>
  );
}

/**
 * The spinner half of a §4.1 loading button: keep the label, add this, set
 * `aria-busy="true"`, disable. It was inline in the reference page, which meant
 * every call site that needed one copied the class string — so they mostly
 * didn't, and swapped the label to "Saving…" instead.
 *
 * `aria-hidden` is not decoration: a spinner is never the accessible name, and
 * the button's own label is still there to be read.
 *
 * Under `prefers-reduced-motion` the global rule in index.css runs this once at
 * 0.01ms, so it renders as a static ring. That is intended, not broken — the
 * label, the disabled state and `aria-busy` all still say "working", so no
 * information is carried by the motion alone.
 */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent ${className ?? ""}`}
    />
  );
}

/** Give a control a stable id derived from React's useId (SSR/hydration-safe). */
export function useFieldId(prefix: string): string {
  const raw = useId();
  return `${prefix}-${raw.replace(/[:]/g, "")}`;
}

// ---- validation helpers -------------------------------------------------

/** Non-empty after trimming. */
export function requiredText(v: string): string | null {
  return v.trim() ? null : "Required";
}

/** A decimal amount like -54.32 / 12 / +0.5. */
export function validAmount(v: string): string | null {
  if (!v.trim()) return "Required";
  return /^[+-]?\d*\.?\d+$/.test(v.trim()) ? null : "Enter a valid number";
}

/** Exactly three ASCII letters, e.g. USD. */
export function validCurrency(v: string): string | null {
  if (!v.trim()) return "Required";
  return /^[A-Za-z]{3}$/.test(v.trim()) ? null : "Use a 3-letter code";
}

/** Positive decimal (FX rate). */
export function validRate(v: string): string | null {
  if (!v.trim()) return "Required";
  if (!/^\d*\.?\d+$/.test(v.trim())) return "Enter a valid number";
  return Number(v) > 0 ? null : "Must be greater than 0";
}
