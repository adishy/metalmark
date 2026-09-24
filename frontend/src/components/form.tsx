// Reusable, accessible form primitives. Every control is paired with a visible
// <label> tied via htmlFor/id. Inputs render an inline error region tied via
// aria-describedby. Styling comes from the design tokens (docs/DESIGN.md §4).
import {
  cloneElement,
  forwardRef,
  isValidElement,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { CheckIcon, ChevronDownIcon } from "@/components/icons";

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
