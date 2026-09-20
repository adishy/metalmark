/**
 * Semantic design tokens. The full rationale, the measured contrast ratios and
 * the component rules live in docs/DESIGN.md — this file is the wiring.
 *
 * Components name a *role* (`bg-surface-raised`, `text-fg-muted`), never a
 * palette colour. The palette itself lives in the `:root` / `.dark` blocks in
 * `src/index.css`, so light mode is a value swap rather than a second set of
 * classes. A `dark:` variant anywhere in the app means a token is missing.
 *
 * `rgb(var(--x) / <alpha-value>)` is load-bearing: it is what keeps Tailwind's
 * opacity modifiers (`bg-accent/20`, `bg-black/60`) working against a
 * CSS-variable colour. Writing `var(--x)` bare silently breaks every `/opacity`
 * in the app.
 *
 * @type {import('tailwindcss').Config}
 */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Three surface levels, darkest-to-lightest role: the page, the cards
        // that sit on it, and the wells recessed into those cards.
        surface: {
          DEFAULT: token("surface"),
          raised: token("surface-raised"),
          inset: token("surface-inset"),
        },
        border: {
          // Decorative dividers — no contrast requirement.
          DEFAULT: token("border"),
          // What identifies a form control, so it must clear 3:1 (WCAG 1.4.11).
          strong: token("border-strong"),
        },
        fg: {
          DEFAULT: token("fg"),
          // The floor for text. There is deliberately no third, lighter tone:
          // slate-500 fails in both themes and is what this replaced.
          muted: token("fg-muted"),
        },
        accent: {
          DEFAULT: token("accent"),
          // Label/icon *on* an accent fill. Flips with the theme because
          // teal-500 needs dark text and teal-700 needs light text.
          fg: token("accent-fg"),
        },
        // Money, not status. An expense is a normal event and is NOT red — see
        // DESIGN.md §6.2. These two are for gains/losses and negative balances.
        positive: token("positive"),
        negative: token("negative"),
        warning: token("warning"),
        // The destructive *fill*. Theme-independent, so a delete button is the
        // same red everywhere and never has to be re-reasoned.
        danger: token("danger"),
        focus: token("focus"),
        chart: {
          1: token("chart-1"),
          2: token("chart-2"),
          3: token("chart-3"),
          4: token("chart-4"),
          5: token("chart-5"),
          6: token("chart-6"),
          7: token("chart-7"),
          8: token("chart-8"),
          9: token("chart-9"),
          10: token("chart-10"),
        },
      },
      // Bare `border` should mean our token, not Tailwind's default grey —
      // otherwise `border` and `border-border` differ, which is a trap.
      borderColor: ({ theme }) => ({
        ...theme("colors"),
        DEFAULT: "rgb(var(--border) / <alpha-value>)",
      }),
      borderRadius: {
        control: "0.5rem", //  8px — buttons, inputs, chips
        card: "0.75rem", //    12px — sections, cards, list containers
        overlay: "1rem", //    16px — dialogs, sheets
      },
      transitionDuration: { state: "120ms", overlay: "200ms" },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};
