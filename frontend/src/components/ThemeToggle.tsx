import type { ReactNode } from "react";
import { setTheme, useTheme, type ThemeChoice } from "@/theme/theme";

// Two controls, two jobs:
//   ThemeToggle — the canonical three-way control, with visible labels. Lives in
//                 Settings and on the design-system page.
//   ThemeButton — a compact one-tap light/dark flip for the app header.
// "System" is only reachable from ThemeToggle; that is a deliberate limit on the
// header control, which has no room to explain three states.
//
// Both are built on real form controls rather than divs with ARIA roles: the
// keyboard behaviour, the accessible name and the checked state then come from
// the platform instead of from us. (Settings' tablist is the counter-example —
// it declares role="tablist" without implementing the keyboard model those roles
// promise, which is worse than not declaring them.)

const CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/*
 * The segment *is* the <label>, wrapping a visually-hidden radio. Two details
 * are load-bearing:
 *
 *   - `has-[:checked]` rather than `peer-checked:`. `peer-*` needs the peer to be
 *     a previous *sibling*, so it only works if the label is flattened away with
 *     `display: contents` — and `display: contents` on a <label> drops its
 *     semantics in some engines.
 *   - no `overflow-hidden` on the track. It is the obvious way to round the ends,
 *     but it also clips the focus ring, and a clipped ring fails 2.4.11. The
 *     track is inset by p-0.5 and each segment carries its own radius instead.
 */
const SEGMENT =
  "min-h-11 cursor-pointer rounded-md px-3 py-2 text-sm font-medium " +
  "text-fg-muted transition duration-state hover:text-fg " +
  "has-[:checked]:bg-accent has-[:checked]:text-accent-fg " +
  "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 " +
  "has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent";

export function ThemeToggle({ className }: { className?: string }) {
  const { choice } = useTheme();

  return (
    <fieldset className={className}>
      <legend className="mb-1.5 block text-xs font-medium text-fg-muted">Theme</legend>
      <div
        className="inline-flex rounded-control border border-border-strong bg-surface-inset p-0.5"
        data-testid="theme-toggle"
      >
        {CHOICES.map((c) => (
          <label key={c.value} className={SEGMENT}>
            <input
              type="radio"
              name="theme"
              value={c.value}
              checked={choice === c.value}
              onChange={() => setTheme(c.value)}
              className="sr-only"
              data-testid={`theme-${c.value}`}
            />
            {c.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="h-5 w-5"
    >
      {children}
    </svg>
  );
}

/** One-tap light/dark flip for the header. 44×44 so it is a thumb target. */
export function ThemeButton() {
  const { resolved } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      // Names the *action*, not the state — "Switch to light theme" is what a
      // screen-reader user needs to hear before activating it.
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="grid h-11 w-11 place-items-center rounded-control text-fg-muted transition duration-state hover:bg-surface-inset hover:text-fg"
      data-testid="theme-button"
    >
      {resolved === "dark" ? (
        <Icon>
          {/* sun */}
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </Icon>
      ) : (
        <Icon>
          {/* moon */}
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </Icon>
      )}
    </button>
  );
}
