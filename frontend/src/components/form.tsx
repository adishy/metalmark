// Reusable, accessible form primitives. Every control is paired with a visible
// <label> tied via htmlFor/id. Inputs render an inline error region tied via
// aria-describedby. Styling matches the app's slate/teal dark theme.
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

const CONTROL =
  "w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand disabled:opacity-50";

/** Label + control + optional inline error. Wrap any control with it. */
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
  return (
    <div className={`space-y-1 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-slate-400">
        {label}
        {required && <span className="ml-0.5 text-red-400">*</span>}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-slate-500">{hint}</p>}
      {error && (
        <p id={`${htmlFor}-error`} className="text-xs text-red-400" data-testid={`${htmlFor}-error`}>
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

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...props }, ref) {
    return (
      <select ref={ref} className={`${CONTROL} ${className ?? ""}`} {...props}>
        {children}
      </select>
    );
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={`${CONTROL} ${className ?? ""}`} {...props} />;
  },
);

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-slate-950 hover:brightness-110",
  secondary: "bg-slate-700 text-slate-100 hover:bg-slate-600",
  ghost: "text-slate-300 hover:bg-slate-800",
  danger: "bg-red-500/90 text-white hover:bg-red-500",
};

export function Button({
  variant = "primary",
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium transition disabled:opacity-50 ${VARIANTS[variant]} ${className ?? ""}`}
      {...props}
    >
      {children}
    </button>
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
