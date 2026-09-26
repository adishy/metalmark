# MetalMark — Design Guidelines

The rules here are meant to be **checkable by reading code**. If a rule cannot be
verified by looking at a `.tsx` file, a `tailwind.config.js`, or a contrast
calculator, it does not belong in this document.

Scope: the `frontend/` React app. Not the API, not the finance rules.

---

## 1. Principles

1. **The number is the interface.** Every layout decision optimises for reading and
   comparing a figure correctly at arm's length, because silently misreading a digit
   is the failure mode that costs real money.
2. **Thumb first, mouse second.** Design at 360 px wide and one hand; anything that
   matters lives in the bottom third of the screen, because the phone case is used
   standing at a till.
3. **Colour is never the only signal.** Sign, label, or shape always carries the
   meaning and colour only reinforces it — our own semantic colours do *not* clear
   the 3:1 luminance-difference route out of WCAG 1.4.1 (verified in §6.2), so the
   glyph is load-bearing, not decorative.
4. **Destructive actions are two-step and never adjacent to the primary action.**
   A mis-tap on a ledger row is unrecoverable, and there is no support desk to call.
5. **Tokens, not palette classes.** No `bg-slate-800` survives in a `.tsx` file:
   every colour comes from a semantic role, so light mode and any future re-theme are
   config edits rather than a rewrite.
6. **Every async surface has three designed states.** Loading, empty, and error are
   distinct. An empty state that is really a failed request teaches the user their
   money is gone.

---

## 2. Design tokens

### 2.1 Colour roles

Seventeen tokens: the nine content roles, five support tokens
(`surface-inset`, `border-strong`, `accent-fg`, `danger`, `focus`), and three `-ink`
variants for text on a tint (below the table). Contrast ratios
below are computed against the surface each token is actually used on, and the
**worst case in each theme** is the number shown.

| Role | Light | Dark | Verified contrast (worst case) |
|---|---|---|---|
| `surface` — page background | `#f8fafc` | `#020617` | — (base) |
| `surface-raised` — cards, rows, sheets | `#ffffff` | `#0f172a` | — (base) |
| `surface-inset` — inputs, wells, chips | `#f1f5f9` | `#1e293b` | — (base) |
| `border` — dividers, decorative only | `#e2e8f0` | `#1e293b` | 1.23:1 light / 1.13:1 dark (no requirement) |
| `border-strong` — control boundaries | `#64748b` | `#64748b` | **4.34:1** light / **3.07:1** dark (needs 3:1) |
| `fg` — primary text | `#0f172a` | `#f1f5f9` | **16.30:1** light / **13.35:1** dark (needs 4.5:1) |
| `fg-muted` — secondary text, meta | `#475569` | `#94a3b8` | **6.92:1** light / **5.71:1** dark (needs 4.5:1) |
| `accent` — brand, primary action | `#0f766e` | `#2dd4bf` | **5.23:1** light / **9.59:1** dark (needs 4.5:1) |
| `accent-fg` — label on `accent` fill | `#ffffff` | `#020617` | **5.47:1** light / **10.84:1** dark (needs 4.5:1) |
| `positive` — income, gain | `#047857` | `#34d399` | **5.24:1** light / **9.29:1** dark (needs 4.5:1) |
| `negative` — loss, money owed | `#b91c1c` | `#f87171` | **6.18:1** light / **6.45:1** dark (needs 4.5:1) |
| `warning` — missing FX rate, stale | `#b45309` | `#fbbf24` | **4.80:1** light / **10.69:1** dark (needs 4.5:1) |
| `danger` — destructive button fill | `#b91c1c` | `#b91c1c` | **6.47:1** with `#ffffff` label, both themes |
| `focus` — focus ring | `#0f766e` | `#2dd4bf` | **5.23:1** light / **9.59:1** dark (needs 3:1) |

**Ink tokens — text on a tint.** A selected chip (`bg-accent/20`), a "needs review"
badge (`bg-warning/20`) and an error pill (`bg-negative/20`) put the role colour on a
20 % wash of itself, and in light mode that fails 4.5:1 — measured 3.96:1 (accent),
3.80:1 (warning), 4.4:1 (negative, on the page). Each role has an **`-ink`** token for
exactly this: one step darker in light mode (teal-800 5.9:1, amber-800 5.8:1, red-800
6.4:1), and equal to the role in dark mode, where it already clears 6.3:1. Rule: **text
on a `bg-{role}/N` tint is `text-{role}-ink`**, never `text-{role}`.

Three rules that follow from the table and are not negotiable:

- **`fg-muted` is the floor.** There is no third, lighter text colour. The current
  `text-slate-500` on `bg-slate-900` is 3.75:1 and fails WCAG 1.4.3; deleting that
  class from the codebase is the single cheapest accessibility win available.
- **`border` and `border-strong` are different jobs.** `border` is decorative and
  has no contrast requirement. `border-strong` is what identifies a form control, so
  it must clear 3:1 (WCAG 1.4.11) — `#94a3b8` looks like the natural light-mode
  choice but computes to 2.56:1 and fails.
- **`danger` is theme-independent.** `#b91c1c` works on both surfaces, so a
  destructive button is the same red in every theme and never has to be re-reasoned.

### 2.2 Wiring into Tailwind v3

Colours are stored as **space-separated RGB channels**, not hex, so Tailwind v3's
opacity modifier keeps working (`bg-accent/20`, `bg-black/60` — both already used in
the app). This is why `theme.extend.colors` wraps every token in
`rgb(var(--x) / <alpha-value>)` rather than pointing straight at a hex string.

```js
// tailwind.config.js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          raised: "rgb(var(--surface-raised) / <alpha-value>)",
          inset: "rgb(var(--surface-inset) / <alpha-value>)",
        },
        border: {
          DEFAULT: "rgb(var(--border) / <alpha-value>)",
          strong: "rgb(var(--border-strong) / <alpha-value>)",
        },
        fg: {
          DEFAULT: "rgb(var(--fg) / <alpha-value>)",
          muted: "rgb(var(--fg-muted) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          fg: "rgb(var(--accent-fg) / <alpha-value>)",
        },
        positive: "rgb(var(--positive) / <alpha-value>)",
        negative: "rgb(var(--negative) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
        focus: "rgb(var(--focus) / <alpha-value>)",
        chart: {
          1: "rgb(var(--chart-1) / <alpha-value>)",
          2: "rgb(var(--chart-2) / <alpha-value>)",
          3: "rgb(var(--chart-3) / <alpha-value>)",
          4: "rgb(var(--chart-4) / <alpha-value>)",
          5: "rgb(var(--chart-5) / <alpha-value>)",
          6: "rgb(var(--chart-6) / <alpha-value>)",
          7: "rgb(var(--chart-7) / <alpha-value>)",
          8: "rgb(var(--chart-8) / <alpha-value>)",
          9: "rgb(var(--chart-9) / <alpha-value>)",
          10: "rgb(var(--chart-10) / <alpha-value>)",
        },
      },
      // Make bare `border` mean our token, not Tailwind's grey.
      borderColor: ({ theme }) => ({
        ...theme("colors"),
        DEFAULT: "rgb(var(--border) / <alpha-value>)",
      }),
      borderRadius: {
        control: "0.5rem", //  8px  — buttons, inputs, chips-with-corners
        card: "0.75rem", //   12px  — sections, cards, list containers
        overlay: "1rem", //   16px  — dialogs, bottom sheets
      },
      transitionDuration: { state: "120ms", overlay: "200ms" },
      fontFamily: {
        sans: [
          "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI",
          "Roboto", "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
```

`min-h-11` (44 px) and `size-11` (44 px) are already in Tailwind v3.4's default
spacing scale — verified against the installed 3.4.19 — so no `minHeight` extension
is needed for the touch-target rules in §4 and §5.

### 2.3 The CSS variable block

Paste into `src/index.css`, replacing the current `:root { color-scheme: dark }`.

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Light is the default; .dark on <html> overrides it. Every value is
   "R G B" so Tailwind's /opacity modifier works. */
:root {
  --surface: 248 250 252;        /* #f8fafc */
  --surface-raised: 255 255 255; /* #ffffff */
  --surface-inset: 241 245 249;  /* #f1f5f9 */
  --border: 226 232 240;         /* #e2e8f0 */
  --border-strong: 100 116 139;  /* #64748b */
  --fg: 15 23 42;                /* #0f172a */
  --fg-muted: 71 85 105;         /* #475569 */
  --accent: 15 118 110;          /* #0f766e */
  --accent-fg: 255 255 255;      /* #ffffff */
  --accent-ink: 17 94 89;        /* #115e59 — text on an accent tint */
  --positive: 4 120 87;          /* #047857 */
  --negative: 185 28 28;         /* #b91c1c */
  --negative-ink: 153 27 27;     /* #991b1b — text on a negative tint */
  --warning: 180 83 9;           /* #b45309 */
  --warning-ink: 146 64 14;      /* #92400e — text on a warning tint */
  --danger: 185 28 28;           /* #b91c1c */
  --focus: 15 118 110;           /* #0f766e */

  /* Categorical series, ordered so neighbouring slices differ in lightness.
     All >= 5.02:1 on light surface (WCAG 1.4.11 needs 3:1). */
  --chart-1: 15 118 110;   --chart-2: 3 105 161;
  --chart-3: 79 70 229;    --chart-4: 190 24 93;
  --chart-5: 180 83 9;     --chart-6: 4 120 87;
  --chart-7: 185 28 28;    --chart-8: 109 40 217;
  --chart-9: 29 78 216;    --chart-10: 194 65 12;
}

.dark {
  --surface: 2 6 23;             /* #020617 */
  --surface-raised: 15 23 42;    /* #0f172a */
  --surface-inset: 30 41 59;     /* #1e293b */
  --border: 30 41 59;            /* #1e293b */
  --border-strong: 100 116 139;  /* #64748b */
  --fg: 241 245 249;             /* #f1f5f9 */
  --fg-muted: 148 163 184;       /* #94a3b8 */
  --accent: 45 212 191;          /* #2dd4bf */
  --accent-fg: 2 6 23;           /* #020617 */
  --accent-ink: 45 212 191;      /* = accent; already 6.3:1 on its tint */
  --positive: 52 211 153;        /* #34d399 */
  --negative: 248 113 113;       /* #f87171 */
  --negative-ink: 248 113 113;   /* = negative */
  --warning: 251 191 36;         /* #fbbf24 */
  --warning-ink: 251 191 36;     /* = warning; 6.9:1 on its tint */
  --danger: 185 28 28;           /* #b91c1c — same fill in both themes */
  --focus: 45 212 191;           /* #2dd4bf */

  /* Same ten hues, lifted for a dark surface. All >= 6.76:1. */
  --chart-1: 20 184 166;   --chart-2: 56 189 248;
  --chart-3: 129 140 248;  --chart-4: 244 114 182;
  --chart-5: 251 191 36;   --chart-6: 52 211 153;
  --chart-7: 248 113 113;  --chart-8: 167 139 250;
  --chart-9: 96 165 250;   --chart-10: 251 146 60;
}

html { color-scheme: light; }
html.dark { color-scheme: dark; }

html, body, #root { height: 100%; }

body {
  @apply bg-surface text-fg antialiased;
  margin: 0;
  font-family: theme("fontFamily.sans");
  /* Money alignment cannot be forgotten at a call site. */
  font-variant-numeric: tabular-nums;
  /* Kill the 300ms double-tap-zoom delay on every interactive element. */
  touch-action: manipulation;
}

/* One focus treatment for the whole app. focus-visible only, so a mouse
   click never paints a ring. */
:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid rgb(var(--focus));
  outline-offset: 2px;
  border-radius: inherit;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

### 2.4 Type scale

System font stack (`ui-sans-serif`), which already ships tabular figures on Apple,
Windows, and Android — that is why `font-variant-numeric` on `body` is enough.

| Token | px / line-height | Weight | Use |
|---|---|---|---|
| `text-xs` | 12 / 16 | 400–500 | Row meta only. **Absolute floor** — never money, never a label. |
| `text-sm` | 14 / 20 | 400–500 | Body and form labels (desktop). |
| `text-base` | 16 / 24 | 400–600 | **All inputs on touch**, transaction amounts, list primaries. |
| `text-lg` | 18 / 28 | 500 | Section headings. |
| `text-xl` | 20 / 28 | 600 | Page title (the one `<h1>`); card hero figure. |
| `text-2xl` | 24 / 32 | 600 | Report headline figure. |
| `text-3xl` | 30 / 36 | 600 | Net worth, one per screen. |

Three rules:

- **Never below 12 px.** 12 px is reserved for meta; if a value matters, it is 14 px
  or larger.
- **Inputs are `text-base` (16 px) on touch.** Anything smaller and iOS Safari zooms
  the viewport on focus, which loses the user's place mid-entry. The current
  `text-sm` control is a live version of this bug.
- **Amounts are `text-base font-semibold`.** An amount must be at least as prominent
  as the merchant name beside it — never smaller.

### 2.5 Spacing scale (4 px base)

Allowed steps: **4, 8, 12, 16, 24, 32, 48** (`p-1 p-2 p-3 p-4 p-6 p-8 p-12`).

| Gap | Value |
|---|---|
| Label → its control | 4 px (`space-y-1`) |
| Control → its hint/error | 4 px |
| Between form fields | 12 px (`gap-3`) |
| Between cards/sections | 24 px (`space-y-6`) |
| Page gutter | 16 px (`p-4`); 24 px at `lg:` |

No arbitrary values (`p-[13px]`). If a gap needs a number that is not on this list,
the layout is wrong.

### 2.6 Radii and elevation

| Element | Radius |
|---|---|
| Buttons, inputs, selects, small controls | 8 px (`rounded-control`) |
| Cards, sections, list containers | 12 px (`rounded-card`) |
| Dialogs, bottom sheets | 16 px (`rounded-overlay`) |
| Chips, pills, avatars, status dots | 9999 px (`rounded-full`) |

**Elevation is surface lightness plus a 1 px border, not a shadow.** In dark mode
layer by choosing `surface` → `surface-raised` → `surface-inset`; the border does the
separating. Shadows are reserved for true overlays and use exactly two steps:

- `shadow-lg` — dialog, bottom sheet, dropdown, tooltip.
- `shadow-2xl` — never. If a panel needs to feel more detached than `shadow-lg`, it
  should be a dialog instead.

### 2.7 Focus ring

One treatment, everywhere: **2 px solid `focus` at 2 px offset**, on
`:focus-visible` only. Delivered by the `:focus-visible` rule in §2.3 so it cannot be
forgotten per component.

Where a component sits on `surface-inset` rather than `surface`, the offset colour
must be set explicitly or the ring will look detached:

```tsx
className="focus-visible:outline-none focus-visible:ring-2
           focus-visible:ring-focus focus-visible:ring-offset-2
           focus-visible:ring-offset-surface-inset"
```

`outline-none` or `focus:outline-none` must **never** appear without a replacing ring
on the same element. This is a grep in §8.

### 2.8 Motion

| Transition | Duration | Easing |
|---|---|---|
| Hover, press, colour, opacity | 120 ms (`duration-state`) | `ease-out` |
| Dialog/sheet enter and exit | 200 ms (`duration-overlay`) | `ease-out` |
| Anything longer | — | not allowed |

Under `prefers-reduced-motion: reduce` all of it collapses to ~0 via the global rule
in §2.3, **and** framer-motion must be told explicitly — the CSS rule does not reach
JS-driven transforms:

```tsx
import { useReducedMotion } from "framer-motion";
const reduce = useReducedMotion();
// Review.tsx: keep drag working (it is direct manipulation, so it is the
// user's own motion) but drop the rotate/opacity flourish.
style={{ x, rotate: reduce ? 0 : rotate }}
```

### 2.9 Charts (ECharts)

Charts must **read the tokens at runtime**. Hardcoded greys in an option object — the
current pattern in `insights/Overview.tsx` (`#475569`, `#1e293b`, `#94a3b8`, `#0f172a`) — are
why a chart cannot follow a theme change.

**The module already exists** as `src/theme/chartTokens.ts`. Its architecture is right
— it reads the variables and is keyed on the resolved theme — but it asks for
`--success`, `--content` and `--content-muted`, none of which `index.css` defines.
Fix those three names to `--positive`, `--fg` and `--fg-muted`; do not add a second
module. Whatever it ends up being called, `token()` must **fail loudly** on a missing
variable:

```ts
// Reads the CSS variables the theme sets on <html>, so a theme switch is a
// re-read rather than a hardcoded second palette.
export function token(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${name}`)
    .trim();
  // This check is the whole point. getPropertyValue returns "" for a name that
  // does not exist, so without it a typo'd token yields an invalid colour and
  // the chart quietly paints the previous theme's palette, with no error.
  if (!raw) throw new Error(`Unknown design token: --${name}`);
  const [r, g, b] = raw.split(/\s+/).map(Number);
  // Comma-separated, NOT the CSS Color 4 "rgb(71 85 105)" form — zrender's
  // parser strips spaces and splits on commas, so the spaced form yields
  // `undefined`. See the note under this block; it is load-bearing.
  return alpha === 1 ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${alpha})`;
}

export const chartTokens = () => ({
  axis: token("border-strong"),
  split: token("border"),
  label: token("fg-muted"),
  surface: token("surface-raised"),
  border: token("border"),
  series: Array.from({ length: 10 }, (_, i) => token(`chart-${i + 1}`)),
});
```

**Why the output format matters.** zrender does not implement CSS Color 4: its parser
strips spaces and splits on commas, so `"rgb(71 85 105)"` parses to `undefined`, and so
does everything derived from it. Painting still looks right — zrender hands the string
to the canvas and the *browser* parses it — which is what kept this quiet for so long.
The damage lands on any path needing the numeric components, and the expensive one is
that ECharts **lifts** a colour when building a default emphasis state: with the lift
returning `undefined`, an emphasis that leaves a fill or stroke unspecified has
`undefined` written into its style, and zrender then declines to paint that element at
all. So every colour this module emits must be comma-separated.

The mapping, per chart:

| ECharts slot | Token |
|---|---|
| `axisLine.lineStyle.color`, `axisTick` | `border-strong` |
| `splitLine.lineStyle.color` | `border` |
| `axisLabel.color`, `legend.textStyle.color` | `fg-muted` |
| `tooltip.backgroundColor` / `borderColor` | `surface-raised` / `border` |
| `tooltip.textStyle.color` | `fg` |
| `pie.itemStyle.borderColor` | `surface-raised` (currently `#0f172a`) |
| series colours | `chart-1` … `chart-10` in order |
| sankey node colour | `positive` (money in), `negative` (money out), `accent` for the window and its two residual nodes |
| sankey link colour | its source node's, at `0.4` — `lineStyle.color: "source"` |

Seven further chart rules:

- **A money value axis says it is money, and stays short.** `chartAxis(t, { tick })`
  takes a currency formatter; every money axis passes `formatMoneyTick` (§6.5), so
  the ticks read `$5k`/`$40k` rather than a bare `5000`/`40000` whose currency the
  reader has to infer. The gutter is then **measured, not reserved**: those options
  set `grid: { containLabel: true, left: 8 }`, because a constant `left: 60` spends
  a fifth of a 310 px phone canvas on gutter when the ticks are short and clips them
  when they are long.

- **A bar stack is rounded at the end the value is at, and a chart that grows both
  ways rules its baseline.** `barEndRadius("top" | "bottom")` rounds the *outer* end
  of each stack and only that end: the inner ends are joins, and a pill on every
  segment would imply a gap the data does not have. The radius is 4 px and is
  deliberately not one of §2.6's — those size a surface, and a bar is a mark whose
  corner is a chart decision. It is measured rather than tasteful: a bar is 36 px
  wide on a 310 px phone canvas and 79 px at 620 px, so 4 px is a corner rather than
  a capsule, and zrender scales a radius down to fit a thinner or shorter bar instead
  of letting it spill past the axis. An income-vs-expense chart then draws
  `zeroRule(t)` — silent, dashed, 1 px, in the axis colour — because the line the
  bars are measured *from* is otherwise one gridline among five of the same weight,
  and which one it is becomes a guess. ECharts paints a mark line above its series,
  so the rule divides the two halves of each bar rather than hiding behind them.

- **A chart is laid out for its own box, not for the window.** `Chart.tsx` measures the
  box it was given — in a layout effect before the first paint, and through a
  `ResizeObserver` after that — and hands it to any option passed as a function of it.
  `chartLayout(box)` is the one place that says what a canvas of a given size affords.
  The same sankey is 310 px wide on a phone and 1104 px on a wide page **inside the same
  card**, so an option that places anything in px — a column of labels, a node width —
  cannot be a constant without being wrong on one of them. On a phone the sankey's label
  columns take what a label needs (84 px plus ECharts' own 5 px gap) and the ribbons take
  the rest, which is what they are for; a wide canvas keeps the roomy margins it has
  always been drawn with.

- **A chart is never the only way to read a value.** Canvas is invisible to screen
  readers, so every chart ships with a text equivalent: keep the `<ul>` of
  category + amount under the spending donut (already correct in `insights/Overview.tsx` —
  it is now required, not incidental), and give the `<Chart>` wrapper
  `role="img"` with an `aria-label` that states the finding.
- **A ring states its total in its own centre.** A donut's hole is the one piece of
  space on the chart that carries nothing, and the figure the ring is *about* — what
  the slices add up to — is otherwise only in the `aria-label` and the `<ul>`. It is
  **DOM text over the canvas**, not a canvas `graphic` and not a second pie series:
  real text is selectable and readable to assistive technology, and it takes its
  colour from the tokens, so a theme flip repaints it with nothing to keep in step.
  A figure too long for the hole steps **down** the type scale (§6.5 — shrink the
  type, never round it in the display), floored at `text-base font-semibold`.
- **Re-render on theme change.** `ReactECharts` will not notice a CSS variable
  change. Key the chart on the resolved theme (`key={theme}`) so it re-mounts.
- **Colour is a secondary channel in charts too** — the cash-flow chart's income and
  expense both carry a legend label, which is what makes it readable in greyscale.

### 2.10 Chart interaction

§2.9 covers what a chart is painted with; this covers what it does when pointed at.
Every option is assembled from `src/theme/chartInteraction.ts` — **no page writes a
`tooltip`, an `axisPointer` or an `emphasis` of its own**, so the seven charts cannot
drift apart. §8 rule 8 enforces that.

**Hovering never removes ink.** The rule exists because it was broken: the net-worth
line's fill vanished under the pointer while its stroke stayed, so the chart came
apart instead of highlighting. Two consequences worth knowing before editing an
option:

- **An emphasis `areaStyle` must name a colour.** ECharts re-draws a series from
  scratch in the emphasis state, and there an `areaStyle` carrying only an `opacity`
  does *not* inherit the series colour the way the resting style does — it resolves
  to nothing. An emphasis block that looks complete and does keep the line can still
  lose the fill. Use `chartArea()` for the resting fill and `emphasisLine()` for the
  emphasis, which state the same colour by construction. `emphasis: {disabled: true}`
  also hides the symptom, by removing hover feedback entirely.
  This is belt-and-braces rather than *the* cause: the underlying fault was the colour
  format in `token()` (§2.9), which is fixed. Stating the colour explicitly stays
  because the auto-lift otherwise captures a *lifted* colour as the new resting colour
  when a hovered series refreshes, so the hue walks brighter on each interaction.
- **A donut spotlights with `focus: "self"`, not `"series"`.** A pie is one series
  whose slices are data items, so `"series"` has nothing to blur and hovering dims
  nothing at all. Line and bar charts use `"series"`.

**Spotlight by dimming, to a legible value.** Hovering a series (or its legend entry,
or a donut slice) dims the others to `DIM` (30%) so the one being asked about reads
clearly. It must stay *readable*: ECharts' default blur is low enough that a dimmed
series reads as absent, which is the same complaint as the vanishing line arriving
from the other direction. A dimmed series is still drawn — tests that count drawn
pixels cannot see dimming and must measure intensity.

**Tooltips are confined** (`confine: true`), or a phone-width chart's tooltip
overhangs the card it belongs to.

**A tooltip says what the chart could not.** It is the one place in a chart a reader
gets an exact figure, so on a money chart it prints money at full precision through
`formatMoney` — a tick's licence to compact (`formatMoneyTick`, §6.5) stops at the
axis — and it names its bucket in the long form (`Sep 2026`), not the axis's short one
(`Sep`): the axis abbreviates because it has to fit, and the tooltip has the room to be
a date a reader can place in a year. A page's formatter goes through `chartTooltip`
(§8 rule 8) and formats with `formatMoney` and `formatBucket`; it is where the exact
figure behind every compacted tick lives.

**Motion.** Charts animate in JavaScript onto a canvas, which no CSS rule can reach —
so, like framer-motion (§2.8), ECharts has to be told about `prefers-reduced-motion`
explicitly. `<Chart>` does it once for every chart via `useReducedMotion()`; pages do
not. The durations are §2.8's own vocabulary rather than new numbers: 200 ms
(`duration-overlay`) to arrive, 120 ms (`duration-state`) to change state, `ease-out`.

**Keyboard.** Charts carry one `aria-label` sentence each and that is the whole of
their accessible content (§2.9); they are not focusable and have no keyboard
interaction of their own. Do not add a data list under the net-worth or cash-flow
charts — a chart is never the *only* way to read a value, but the text equivalent
belongs to the page, and only the spending donut's `<ul>` is load-bearing.

---

## 3. Light / dark mode

**Mechanism:** Tailwind's `class` strategy (already set) on `<html>`. The class is the
single source of truth; CSS `prefers-color-scheme` is read **once, in JS**, to pick
the default. Do not also write a `@media (prefers-color-scheme)` block in CSS — two
sources of truth means a user override that half-applies.

**State:** `localStorage["metalmark-theme"]` holds `"light" | "dark"`. Absent means
the OS preference decides. "System" is the *absence* of a stored key, not a third
stored value — that is what makes "follow the OS again" a one-line delete rather than
a value you have to remember to treat specially.

**No flash of wrong theme.** The class must be on `<html>` before the first paint, so
a small **blocking, non-module** script goes in `<head>` — module scripts are deferred
and will paint the wrong theme first:

```html
<!-- index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <title>MetalMark</title>
    <script>
      // Must run before first paint, and must NOT be type="module".
      (function () {
        try {
          var stored = localStorage.getItem("metalmark-theme");
          var dark =
            stored === "dark" ||
            (stored !== "light" &&
              window.matchMedia("(prefers-color-scheme: dark)").matches);
          document.documentElement.classList.toggle("dark", dark);
        } catch (e) {
          /* private mode: fall through to light */
        }
      })();
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Keep `class="dark"` on `<html>` in `index.html`. It is not a bug — it is the pre-JS
default, and it is what a user with JS disabled or still loading gets. The script's
job is to *remove* it when the resolution is light, and `classList.toggle` does that in
one line. Remove it and the no-JS state becomes light-on-a-dark-app instead.

**Mobile browser chrome.** The static `<meta name="theme-color" content="#0f172a">`
must be updated from JS on theme change (`#f8fafc` light, `#020617` dark) or the
phone's toolbar stays dark over a light page.

**The React side** is one tiny module, `src/theme/theme.ts`:

```ts
export type Theme = "light" | "dark" | "system";
const KEY = "metalmark-theme"; // keep in step with the inline script in index.html

export function stored(): Theme {
  try { return (localStorage.getItem(KEY) as Theme) || "system"; } catch { return "system"; }
}

export function resolve(pref: Theme): "light" | "dark" {
  if (pref !== "system") return pref;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function apply(pref: Theme) {
  const dark = resolve(pref) === "dark";
  document.documentElement.classList.toggle("dark", dark);
  try { localStorage.setItem(KEY, pref); } catch {}
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", dark ? "#020617" : "#f8fafc");
}
```

Also subscribe to `matchMedia("(prefers-color-scheme: dark)")` `change` so a
`"system"` user follows their OS without a reload.

**The toggle's placement and labelling.** Settings → **Appearance**, as a three-way
segmented control — a two-way switch cannot express "follow the system" once the user
has touched it, and that is the state most people want back.

```tsx
<fieldset>
  <legend id="appearance-label" className="text-sm font-medium text-fg">
    Appearance
  </legend>
  <div role="radiogroup" aria-labelledby="appearance-label" className="mt-2 flex gap-2">
    {(["system", "light", "dark"] as const).map((opt) => (
      <button
        key={opt}
        type="button"
        role="radio"
        aria-checked={pref === opt}
        onClick={() => setPref(opt)}
        className={`min-h-11 rounded-control px-4 text-sm ${
          pref === opt
            ? "bg-accent text-accent-fg"
            : "border border-border-strong bg-surface-inset text-fg"
        }`}
      >
        {opt[0].toUpperCase() + opt.slice(1)}
      </button>
    ))}
  </div>
</fieldset>
```

`role="radio"` + `aria-checked` is what makes the selection announced rather than
merely coloured — `bg-accent` alone would be a 1.4.1 failure. Arrow keys should also
move between the three options per the APG radio-group pattern.

---

## 4. Component rules

### 4.1 Buttons

| Variant | Classes | Use |
|---|---|---|
| Primary | `bg-accent text-accent-fg hover:opacity-90` | One per screen. The thing the user came to do. |
| Secondary | `border border-border-strong bg-surface-inset text-fg hover:bg-surface-raised` | The other option. Import CSV, Cancel. |
| Ghost | `text-fg-muted hover:bg-surface-inset hover:text-fg` | In-row and in-header actions. |
| Destructive | `bg-danger text-white hover:opacity-90` | **Only inside a confirmation.** Never as the first thing a user sees. |

Base (all variants): `inline-flex min-h-11 items-center justify-center gap-2 rounded-control px-4 text-sm font-medium transition-colors duration-state disabled:opacity-50 disabled:pointer-events-none`

- `min-h-11` = 44 px. This replaces the current `px-3 py-2 text-sm`, which computes to
  36 px and is below the floor.
- One primary button per screen. If two compete, one becomes secondary.
- A disabled button needs a reason adjacent to it. `disabled:opacity-50` is not a
  conformance problem (1.4.3 exempts inactive components) but it is a usability one:
  measured, it drops a label to **2.54:1** on a light accent button and **3.28:1** on
  a dark one. Keep the explanatory text, not just the faded fill.
- Loading state: keep the label, add a spinner, set `aria-busy="true"`, and disable.
  Never swap the label to "Loading…" with no other change — that is a status message
  and belongs in a live region (§7.6).

### 4.2 Icon-only buttons

**Forbidden: an icon-only button without an accessible name.** There is no exception
and no "the icon is obvious" case.

```tsx
<button
  type="button"
  aria-label={`Delete ${tag.name}`}
  className="inline-flex size-11 items-center justify-center rounded-control
             text-fg-muted hover:bg-surface-inset hover:text-negative"
>
  <TrashIcon aria-hidden="true" className="size-5" />
</button>
```

- `size-11` = 44×44 px. The icon glyph is 20 px (`size-5`) inside it — the *target*
  is what must be 44 px, not the drawing.
- The `<svg>` gets `aria-hidden="true"` so the accessible name is the `aria-label`
  once, not twice.
- A destructive icon-only button uses `text-negative` **and** an `aria-label` that
  says "Delete"; it never relies on red.
- `Dialog.tsx` already does this correctly (`aria-label="Close"`) except for size —
  `px-2 py-1` computes to 24 px and must become `size-11`.

### 4.3 Inputs and form fields

- **Label above the field, always visible**, bound with `htmlFor`/`id`. A placeholder
  is never a label: it disappears on typing and fails 1.3.1 and 3.3.2.
- 16 px font (`text-base`) on touch — see §2.4.
- `inputMode="decimal"` for money (already correct), `type="date"` for dates,
  `type="search"` for search (already correct).
- Errors: `aria-invalid="true"` + `aria-describedby` pointing at the message, and the
  message carries a **text** prefix, not just red. Hint and error share one slot.
- Required is conveyed by the word, not only an asterisk: keep the `*` but
  `aria-hidden` it and give the input the real `required` attribute or a
  "(required)" in the label text.

```tsx
// components/form.tsx — the shared control
const CONTROL =
  "w-full min-h-11 rounded-control border border-border-strong bg-surface-inset
   px-3 text-base text-fg placeholder:text-fg-muted
   focus-visible:border-accent disabled:opacity-50";

<p id={`${id}-error`} role="alert" className="text-sm text-negative">
  <span aria-hidden="true">⚠ </span>{error}
</p>
```

Note `role="alert"` on the error: 4.1.3 Status Messages wants the error announced
without moving focus, and an inline error that only changes colour is silent to a
screen reader.

Checkboxes are covered separately in §4.14.

### 4.4 Selects and pickers

- **Native `<select>` under ~15 options.** It gets the platform's own accessible
  picker on mobile for free and costs nothing to maintain. Most of this app's lists
  (owners, account types, currencies) qualify.
- **A searchable `<select>`-like control only above ~15 options.** The category list
  is the one that will grow past that.
- A custom picker must implement the APG combobox/listbox pattern in full:
  `role="combobox"` + `aria-expanded` + `aria-controls` on the input,
  `role="listbox"`/`role="option"` + `aria-selected` on the list, arrow keys to move,
  Enter to commit, Escape to dismiss, `aria-activedescendant` kept on the highlighted
  option. Home/End jump to the ends. Type-ahead filters.
- **The category picker specifically** (see §4.5 for the "New category" affordance):
  on phones, a bottom sheet containing a search field, a "Recently used" group of up
  to 5, then the grouped list. Never a modal `<select>` clone with no search.

### 4.5 Chips and tags

- **Filters are `<button aria-pressed>`.** `OwnerFilterChips.tsx` is the reference
  implementation — copy it, do not reinvent it.
- **A tag or badge always carries a text label.** A bare colour dot is forbidden; the
  `categories.color` and `tags.color` columns are for charts and icons, not for the
  only rendering of a category.
- Chip geometry: `min-h-11 rounded-full px-4 text-sm`. The current `px-3 py-1
  text-xs` computes to **24 px**, which is below both our floor and WCAG 2.5.5.
- Selected vs unselected must differ by more than colour: selected chips get
  `bg-accent/20 text-accent border-accent` **and** the `aria-pressed` state.

### 4.6 List rows

- The whole row is one target, `min-h-11`, comfortably **56–72 px** when it carries
  two lines of text.
- One primary action, one row. If a row also needs Edit/Delete, they are either 44 px
  each with 8 px separation, or they move into the detail sheet.
- Never nest an interactive element inside another interactive element. The current
  transaction row is a `<button>` containing only text — correct. The accounts row is
  an `<li>` with an "Edit" `<Button className="px-2 py-1">` inside, which computes to
  28 px and must become `min-h-11`.
- Row state (selected, needs-review) is expressed with **text or an icon**, plus
  colour.

### 4.7 Tables of transactions

- **Below 640 px, a table is not a table.** Render the row list from §4.6. The
  transactions page already does this; keep it that way.
- Where a genuine table must survive on a phone (Settings → Members, FX rates): wrap
  it in a keyboard-reachable scroll container, because a 2-D region that cannot be
  scrolled by keyboard fails 2.1.1.

```tsx
<div className="overflow-x-auto" role="region" aria-label="Exchange rates" tabIndex={0}>
  <table className="w-full text-sm">
    <caption className="sr-only">Exchange rates by date</caption>
    <thead>
      <tr className="text-left text-xs text-fg-muted">
        <th scope="col" className="py-2">Date</th>
        ...
```

- Numeric columns get `text-right tabular-nums`; the first column is the row's
  identity and should be frozen if the table scrolls horizontally.
- The page body must never scroll horizontally. An `overflow-x-hidden` on `<body>` is
  a smell — fix the child instead (§5).

### 4.8 Modals vs bottom sheets

| Screen | Pattern |
|---|---|
| `<640px` | **Bottom sheet**: `rounded-t-overlay`, `max-h-[85dvh]`, drag handle, safe-area padding on the footer (below) |
| `≥640px` | Centred modal, `max-w-lg`, `rounded-overlay` |
| `≥1024px` | Optional right slide-over for detail views |

Use a full-screen takeover only for a multi-step flow (the CSV import).

Both are the **same component**: `Dialog.tsx`, which handles focus move-in, focus
restore, Escape, backdrop click, `role="dialog"`, `aria-modal`, and a Tab cycle. It
also implements everything below — read it before changing any of it:

- **`dvh`, not `vh`, for the sheet height.** On iOS Safari `vh` is measured against
  the largest possible viewport, so an `85vh` sheet overflows the screen while the URL
  bar is showing — exactly when it matters. `85dvh` tracks the *visible* viewport.
- **Safe-area padding goes on the footer, not the sheet**:
  `pb-[calc(0.75rem+env(safe-area-inset-bottom))]` on the footer element. Padding the
  sheet instead pads the bottom of its *scroll area*, which pushes the last row of
  content up away from the footer rather than lifting the buttons clear of the home
  indicator.
- **Drag is a sheet gesture only** — it makes a centred modal wobble — and it is
  disabled entirely under `prefers-reduced-motion`, not merely unanimated.
- **`onClose` is held in a ref.** It is almost always an inline arrow, so keying the
  focus effect on it re-ran the effect on every parent render, and the cleanup
  restored focus to whatever was focused at open: focus jumped out of the field
  mid-keystroke. The effect depends on `open` alone.

The rest of the contract:

- `title` renders as a real heading and the panel is labelled by it via
  `aria-labelledby`, never `aria-label` (a label attribute and a visible heading
  should not be able to drift apart).
- On `open`, `<body>` gets `overflow: hidden` — a background that scrolls under a
  sheet is how a user taps the wrong row.
- A sheet is dismissed by dragging down **or** the close button, which is `size-11`.
- `shadow-lg`, never `shadow-2xl`: §2.6 allows exactly one overlay shadow.

### 4.9 Charts

See §2.9. Additionally: every report section keeps its "No cash flow in range."
empty state; a chart with no data renders the message, never an empty axis frame.

### 4.10 Empty states

Four distinct states, never conflated. Distinguishing them is the whole point.

| State | Trigger | Content |
|---|---|---|
| **First-run** | Query succeeded, zero rows ever | Icon, heading naming the gap ("No accounts yet"), one sentence, **one primary action that creates the thing** |
| **No results** | Query succeeded, a filter is active | "No transactions match these filters." + a **Clear filters** button |
| **Error** | `isError` | "Couldn't load your transactions." + **Retry**. Styled `text-negative`, not muted |
| **Loading** | `isLoading` | Skeleton rows reserving the real row height |

- The current `No transactions match.` with no escape is a dead end; add the clear
  action.
- **Never render the first-run empty state on error.** That is how a user concludes
  their data is gone. Gate it on `isSuccess && rows.length === 0 && !hasFilters`.
- The heading says what is missing ("No accounts yet"), not that something is empty.
- A first-run empty state's button creates the item inline — it does not link
  somewhere generic.

### 4.11 Loading and error states

- **Skeletons, not spinners, for lists and charts** — a skeleton reserves the layout
  so nothing shifts when data lands. A spinner collapses to nothing and then shoves
  the page.
- **A spinner only for in-place actions** (a button that is saving), and only
  alongside the existing label plus `aria-busy`.
- **After first load, never a full-page spinner.** TanStack Query keeps previous data;
  use `isFetching` for a subtle top-of-list indicator and keep showing the old rows.
- Every `isError` branch renders the API's message in `text-negative` with a retry —
  the current pattern in `AddTxnForm` is right; make it universal.
- **When nothing answered there is no message, and saying so is not an exception to
  the rule above — it is the rule applied honestly.** `fetch` rejects with a
  `TypeError` whose message is `"Failed to fetch"`, which is a browser string, not a
  diagnosis; rendering it verbatim satisfies the letter of "show the API's message"
  while telling a self-hoster nothing. Replace it *only* in the case where there was
  no API to speak: an `ApiError` still renders the server's `detail` unchanged.
  `AuthContext`'s session probe is the worked instance — and the reason the rule now
  names the distinction, because that probe is the one read whose failure chooses a
  *screen*, and it was treating a restarting api container as an expired session.
- **A supplementary read's failure is said out loud too.** When a view's main read
  succeeded and a second one that only *annotates* it did not, the content stays —
  it is still true — but the missing annotation is named and the retry offered.
  Nothing appearing is indistinguishable from nothing to appear: a household with no
  override to show and a request that never arrived render identically unless one of
  them says so. `PortfolioHoldings` is the worked instance (the ADR-0034 override line
  comes from `/investments/holdings`; the values do not). Styled `text-warning`, not
  `text-negative` — the figure on screen is not wrong, it is incomplete — and with a
  `ghost` `Button` rather than the `QueryError` card, which would claim the whole
  section failed.

### 4.12 Destructive-action confirmation

Three tiers, chosen by reversibility:

| Tier | Pattern |
|---|---|
| **Reversible** (re-categorise, ignore a review item, unlink a transfer) | Do it immediately + an undo toast for 5 s. No dialog. |
| **Irreversible, small blast radius** (delete an account and its transactions) | **Inline two-step confirm** inside the open dialog, with the count and the word "cannot be undone". This is what `EditAccountDialog` already does — the pattern is correct, keep it. |
| **Irreversible, household-wide** (delete household, re-import over existing data) | Modal + **type the name to confirm**. |

Rules for all three:

- The destructive button is never adjacent to (or the same shape as) the primary.
  In `EditAccountDialog` the Delete button sits at the far left with a `flex-1`
  spacer — correct, keep it.
- The confirm text states **what is lost and how much**: "Delete "Checking" and its
  214 transactions? This cannot be undone." A count, not a shrug.
- The confirming button reads the verb ("Yes, delete") and the cancelling button
  reads the safe noun ("Keep"), never "Cancel" vs "OK".
- Never a red button as the default focus. Focus lands on the safe option.

### 4.13 Navigation and the app shell

**The shell header is sticky at every width** (`sticky top-0 z-40`) and **must be
opaque** (`bg-surface`). Above 640 px the header *is* the primary navigation, so a
non-sticky header took navigation away entirely as soon as a long list scrolled. The
opacity is load-bearing: without it rows scroll straight through the bar.

Sticky chrome at both ends creates the hazard §7 item 3 names — a focused element
scrolled flush to the viewport edge ends up underneath a bar. `index.css` sets
`scroll-padding-top` and `scroll-padding-bottom` of `4rem` on `html` to clear either
bar. Anything that changes the header or tab-bar height must change those too.

- **The bottom tab bar carries 5 items maximum.** The cap is the *bar's*, and the
  reason is thumb reach on a phone: a sixth target is no longer comfortably
  reachable with one hand. It is not a cap on the product's navigation — the
  desktop nav is a row of text in a header with room to spare, so it renders
  every item and the bar takes the first five (`AppShell.tsx`'s `TAB_BAR_MAX`).
  This is how `/admin` is a nav destination without becoming a sixth tab.
  Prefer a fifth item over a sixth tab: if a new destination has no room in the
  bar, its phone path is a section inside Settings, not a squeezed tab.
  **Investments is the first worked instance**, and it is not inside Settings:
  `Accounts` already owns the household's balances, and a portfolio is the same
  subject at a different depth, so the two are a `SegmentedControl` (§4.14) at the
  top of one page rather than two destinations. The rule is "a section inside an
  existing one", and which existing one is a judgement about subject matter, not
  a default to Settings.
- Each item: `min-h-11` plus `pb-[env(safe-area-inset-bottom)]`. The current
  `py-3 text-xs` computes to 40 px and sits under the home indicator without the
  inset.
- **The active item is not colour alone.** `text-accent` on `text-fg-muted` is a hue
  difference only. Add `font-semibold` **and** `aria-current="page"`. `NavLink`
  supplies the latter itself (`ariaCurrent` defaults to `"page"` and is emitted only
  when active), so it is not passed explicitly:

```tsx
<NavLink
  className={({ isActive }) =>
    `flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 text-xs ` +
    (isActive ? "font-semibold text-accent" : "text-fg-muted")
  }
>
  <Icon aria-hidden="true" className="size-5" />
  {n.label}
</NavLink>
```

- The desktop nav stays `hidden sm:flex`; do not show both.
- Page titles are `<h1>` (§7.5) and the document title changes per route (§7.11).

---

### 4.14 Selection controls

Placeholder, in the sense that this app has exactly one selection control so far — a
checkbox, in `form.tsx`. Radio and switch are not used anywhere yet; the rules below
are written so that adding one does not mean inventing a new pattern.

**The label is the target, not the box.** A 20 px box is below the 44 px floor (§5,
SC 2.5.8), so the control is a `<label>` row wrapping the input and its text, at
`min-h-11`, with the box inside it. There is no such thing as a bare checkbox in this
app: if a checkbox has no label, it is a bug, not a layout choice.

**Checked is not colour.** The box changes fill *and* draws a tick glyph
(`peer-checked:opacity-100`). A fill-only change fails SC 1.4.1 the same way an
accent-hued nav item does (§4.13).

```tsx
// components/form.tsx — the whole control
<label className="flex min-h-11 cursor-pointer items-start gap-3 text-sm text-fg">
  <span className="relative mt-2 grid size-5 shrink-0 place-items-center">
    <input type="checkbox" className="peer size-5 appearance-none rounded
      border border-border-strong bg-surface-inset
      checked:border-accent checked:bg-accent disabled:opacity-50" {...props} />
    <CheckIcon className="pointer-events-none absolute size-3.5
      text-accent-fg opacity-0 peer-checked:opacity-100" />
  </span>
  <span className="min-w-0 py-2">{label}{hint}</span>
</label>
```

Details that are load-bearing:

- **`peer` requires a *previous sibling*.** The tick must stay *after* the input in
  the wrapper `span`, or `peer-checked:` silently stops matching and the tick never
  appears. Reordering those two elements is a visual no-op in review and a
  functional break at runtime.
- **`appearance-none`** removes the native control, so the focus ring is no longer
  free — it comes from the global `:focus-visible` rule in `index.css`, which is why
  nothing here sets `outline-none`.
- **Do not use `has-[:checked]:` on an ancestor** when `peer-checked:` will do; `has`
  forces the browser to re-evaluate the ancestor on every input change.
- **Grouped controls need a group label.** Several checkboxes that answer one question
  belong in a `<fieldset>` with a `<legend>`, not a sequence of loose labels.
- **A hint is not a label.** `hint` renders a second line in `text-fg-muted`; the
  label still has to say what the control does on its own.

---

### 4.15 Account marks

Every account is drawn by `src/components/AccountMark.tsx` — a monogram answering
"which account is this credit or debit from?" without a fetch. It appears wherever a
transaction's account is shown: the transactions list, a review card, the transaction
detail sheet.

- **Initials carry the identity**: up to two letters from the account's own name
  ("Chase Checking" → CC, "Chase Savings" → CS), skipping words that carry no identity
  ("Bank of America" → BA, not BO). Two accounts at one institution must stay apart.
- **Colour reinforces, never informs.** The fill is one of the ten `--chart-N` tokens:
  a curated hue for a handful of institutions people actually have (Chase, Amex, Citi,
  Capital One, Wells Fargo, Discover, Fidelity, Vanguard) and a hash of the name
  otherwise. Colour is never the only signal (§7, item 8) — the initials are always
  there.
- **No network, ever — from the browser.** No favicon service and no per-institution
  request: which banks this household uses is not something to leak to a third party
  (ADR-0002). The mark is computed, so the same account gives the same mark in every
  session and on every machine; nothing in it reads the clock, a random source, or the
  environment.
- **A logo, when the household has one.** Settings → Institutions stores a logo per
  institution — uploaded, or fetched once *by the server* from that institution's own
  site on an admin's request (session 06). The mark then draws it on a white disc with a
  1 px `border` (logos are designed for a light ground; a dark one smudges most of them),
  and falls back to the initials if it fails to load. It is served by this app, never
  by the web, so the rule above still holds. Raster only: an SVG can carry script, and
  these are served from the app's own origin. `lg` (40 px) is for the Accounts list,
  where a logo has to be big enough to recognise.
- **Size**: `sm` 20 px in a list row, `md` 24 px in a card or sheet, `lg` 40 px on the
  Accounts list, `rounded-full`
  (§2.6). Initials are `text-xs` — the floor §2.4 sets, and the largest that fits two
  letters in 20 px. `text-accent-fg` on every fill is at least 4.5:1 in both themes,
  pinned by test rather than asserted here.
- **Accessibly**: `role="img"` and an `aria-label` of the account name (plus the
  institution, when the name does not already say it), and a `title` for the pointer.
  The initials are `aria-hidden`, so a screen reader hears "Chase Checking" and not
  "C C".

---

### 4.16 The app mark

MetalMark is named for the metalmark butterfly (Riodinidae). The mark is a metalmark
on a rounded slate tile: a teal-rimmed field, pale wings, a dark body and antennae,
and vein banding in the field colour. It is generated, not drawn by hand —
`scripts/metalmark-mark.mjs` emits the SVG and `scripts/generate-icons.mjs`
(`npm run icons`) renders the set into `public/`. Regenerate rather than replace a
binary, and commit the output.

- **Three variants, for three masks**: rounded for the favicon and PWA "any" icons;
  square for `apple-touch-icon` (iOS applies its own mask, and rounding twice leaks
  the page background at the corners); scaled to 0.84 inside a full-bleed square for
  the `maskable` icons, so a circular or squircle launcher mask never clips a wing.
- **Reads at 16 px and at 512 px.** Rendered from a 1024 px master and halved down, so
  the 16 px icon has no ringing; the silhouette is the one thing that survives, which
  is why the wings are pale on a dark field rather than the reverse.
- **Works on light and dark chrome.** The tile's own rim is what keeps the edge visible
  against a dark browser; nothing relies on the page background.
- **Its colours are literal hex, deliberately.** The mark is served standalone as
  `favicon.svg`, where no CSS variable from the app is in scope, so it cannot use the
  token block. Those five values are the mark's own palette, defined in
  `scripts/metalmark-mark.mjs` and outside `src/` (design-lint does not scan `scripts/`).

---

### 4.17 The review deck

The review queue is a deck of cards, not a list you scroll and not a form you fill in.
The card is the question ("is this one right?"), the throw is the answer, and the deck
has to say both. `/review` has exactly one job, so the card is the only content on the
page and everything around it is about the card.

- **A deck has depth.** Two cards sit behind the live one — inset 8 px and 16 px per
  side, pushed down by the same amount — so their bottom edges show as two steps of a
  stack. They are empty rounded rectangles on purpose: a stack shows you the *edges* of
  what is under the top card, and an 8 px strip of a card cannot be read, so rendering
  contents there would put every merchant in the queue into the accessibility tree
  three times over to say nothing.
- **A decision goes somewhere.** Right (Reviewed) and left (Ignore) throw the card off
  that side over 240 ms, fading over the last third of the distance. The throw is the
  confirmation: the button you pressed is behind you, and the direction is what tells
  you which of the two answers you gave.
- **Up is not a third verdict.** Pulling the card up 80 px — or pressing `e`, or the Edit
  button sitting between the two verdicts — opens the transaction as a right slide-over
  (§9.6). The card is constrained rather than thrown: it travels 140 px up and no further,
  because it is not going anywhere and the sheet appearing is what says the gesture landed.
  **The deck does not advance.** The card is where you left it when the sheet closes, still
  asking the same question, now carrying the answer you opened it to give — categorizing a
  transaction is the reason to open a card from the deck, and a decision is not something
  to make by accident while editing one.
- **The sheet opens over the deck, never beside it.** At `lg:` a transaction detail is
  normally a pane with the list beside it (§9.3/§9.6), and Review has no second column —
  one card, centred. So Review tells the sheet which presentation to use rather than
  letting the width choose, and at 1280 the difference is visible: `role="dialog"` over the
  deck, not `role="region"` in it.
- **Arrow keys belong to the sheet while it is open.** A ← in the amount box is a caret
  move. Without the gate it would file the card the sheet is showing, behind the sheet,
  where nothing on screen says it happened.
- **The card is 288 px tall** (`h-72`), measured rather than chosen — at 320 px the
  longest realistic merchant wraps four times and the content comes to about 218 px.
  The identity block centres itself in the room the amount leaves, so a one-line
  merchant sits on the card's optical centre instead of at the top of an empty box.
  It is also what keeps the deck out of the whole window: `drag` costs the card
  `touch-action: none`, so a touch that starts on the card can never scroll the page. At
  320×568 the card and both buttons fit above the fold and nothing needs to scroll; at
  667×375 the page does scroll, and the 129 px of shell above the deck is what is left to
  start it from. Both are pinned in `review.spec.ts` — a full-height deck would trap a
  landscape phone with no way to reach the buttons.
- **The two verdicts are overlays** in the top corners, fading in with drag distance
  (40 → 160 px). They are not a row of their own: that row cost 26 px of height on
  every card, always, for two labels that are invisible until the card is dragged.
- **One card is in the air at a time.** A decided card stays in the deck for the length
  of its throw, and a second decision during it is ignored rather than applied to a
  card the user has not been shown yet.
- **The next card does not arrive until the throw lands.** A card appearing behind one
  mid-throw spoils the throw, which is the only thing saying where the last decision
  went.
- **Keyboard**: ← / → are the same two decisions and `e` opens the card, for the desktop
  review pass. All three are window-level, and all three stand down while the sheet is
  open or a field has focus.
  **Reduced motion** (§2.8): no throw, no rotation, no badge fade — the card leaves the
  deck immediately, and the deck is faster for it, never slower.

---

## 5. Mobile rules

**Breakpoints.** Only two matter: `sm` (640 px) and `lg` (1024 px). Phone is the
default and the design target — build at 360 px first, then widen.

**Test viewports.** 360×640 (the design target), 390×844 (iPhone), 768×1024, 1280×800.

**Nothing scrolls sideways on a phone. Not the page, and not a strip inside it.**
A row of chips or tabs that does not fit is not made to scroll: below `sm:` a choice
from a list is a **pill that opens a sheet** (`SheetSelect` — "Range · This year ⌄"),
and chips that stay are allowed to wrap. Two exceptions, each marked `data-scroll-x-ok`
or a labelled region: a **`ScrollTabs`** strip (`components/ScrollTabs.tsx` — the
**Settings section tabs**, eleven sections that read better as tabs than from a picker,
and **Insights' tabs**; one swipeable row, edges faded, the chosen tab kept in view,
roving tabindex and arrow keys per §7.4), and a data table whose columns are the point
(the CSV import preview). `ScrollTabs` is the one sanctioned sideways strip — a new tab
list goes through it rather than reimplementing the fade mask and the roving-tabindex
model. `e2e/mobile.spec.ts` walks every route at 360 and 390 px and fails on any
element past the edge or any container that scrolls sideways. Date inputs carry
`min-width: 0` in `index.css`, because iOS gives them an intrinsic width wider than a
phone column.

**Dropdowns.** The native `<select>` keeps its platform picker (it is the right one
on a phone) but not its platform arrow: `Select` draws the app's chevron. A filter or
view switch on a phone is a `SheetSelect`, not a `<select>`.

**The bottom bar is 64 px tall** and each destination is the whole fifth of it; the
active one's icon sits in a pill, so it is a shape change and never colour alone.

**Charts answer a tap** (`triggerOn: "mousemove|click"`) and let a vertical drag
scroll the page (`touch-action: pan-y` on the wrapper) — a chart the width of the
phone must never be a dead zone.

**Touch targets are ≥44×44 CSS px. Always.** WCAG 2.2 AA actually requires only
24×24 (SC 2.5.8) and permits a spacing exception; **we do not use that exception.**
44 px is the AAA figure (SC 2.5.5, which has no spacing exception) and matches what
both mobile platforms recommend, and it is the right floor for a one-handed app used
while standing. If a control must *look* smaller, grow the hit area with padding —
never the other way round.

**Thumb reach.** On a phone held one-handed, the comfortable band is the bottom
third of the screen. Consequences:

- The bottom tab bar is the primary navigation; nothing important goes in the top
  right.
- Primary actions on long forms sit in a **sticky footer bar**, not at the end of the
  scroll:

```tsx
<div className="sticky bottom-0 -mx-4 mt-4 border-t border-border
                bg-surface-raised/95 px-4 py-3 backdrop-blur
                pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
  <Button className="w-full" type="submit">Save</Button>
</div>
```

**Safe-area insets.** `viewport-fit=cover` is already in `index.html`, so the insets
are live; anything pinned to an edge must consume them:

- Bottom bars/sheets: `padding-bottom: env(safe-area-inset-bottom)`.
- Full-screen views: `padding-top: env(safe-area-inset-top)`.
- Do not pad the page body globally — only edge-pinned elements.

**No horizontal scroll, ever, on the page body.** Anything that genuinely cannot fit
goes into its own `overflow-x-auto` region with `role="region"` and `tabIndex={0}`
(§4.7). Fix overflow by letting the flexible child shrink, not by hiding it:

```tsx
<div className="flex min-w-0 items-center gap-3">
  <p className="truncate font-medium">{merchant}</p>   {/* shrinks */}
  <span className="shrink-0 tabular-nums">{amount}</span> {/* never shrinks */}
</div>
```

**A transaction list at 360 px** behaves exactly like this:

- Row: full-width `<button>`, `min-h-16` (64 px), `px-4 py-3`.
- Left block, `min-w-0 flex-1`: merchant `text-base font-medium truncate`, then one
  meta line `text-xs text-fg-muted truncate` reading `date · category · owner`.
- Right block, `shrink-0 text-right`: the amount, `text-base font-semibold
  tabular-nums`. **The amount never truncates and never ellipsises** — the merchant
  gives way instead.
- Keep a minimum 12 px gutter between the two blocks (`gap-3`) so a long amount and a
  long merchant never read as one string.
- Tags: show at most 2, then `+N` in `text-fg-muted`. The full list is in the sheet.
- Splits, transfers and needs-review markers are text or icon badges in the meta
  line, each with an `aria-label` — not coloured dots.
- Below the list, **Load more** stays a real button (`min-h-11`), not infinite scroll
  with no end in sight; users checking a month want to know when they have reached the
  end of it.

**Forms on phones.** One column, labels above, `text-base` inputs, `inputMode` set,
and `<input autocomplete>` on anything a password manager or the OS can fill
(`email`, `current-password`, `new-password` — the login page already does this).

The desktop counterpart to this section is **§9**. A rule that applies at both sizes
belongs in §4, not in either one.

---

## 6. Money and numbers

### 6.1 Alignment

- **`font-variant-numeric: tabular-nums` globally**, set on `body` in §2.3 so no call
  site can forget it. System UI fonts support it; that is why this is a one-liner
  rather than a webfont.
- Amounts are **right-aligned** in every list, table, and summary. In flex rows that
  is `ml-auto text-right shrink-0`; in tables `text-right`.
- Because every amount is 2 dp (0 dp for JPY), right-alignment plus tabular figures
  gives decimal alignment for free. Do not build a separate decimal-align mechanism.

### 6.2 Sign and colour

The ledger stores expenses as negative amounts. Therefore:

| Case | Colour | Glyph |
|---|---|---|
| **Expense** (the normal case) | `text-fg` — **neutral, not red** | leading `−` |
| **Income / credit** | `text-positive` | leading `+` |
| **Negative balance** (a loan, a card you owe on) | `text-negative` | leading `−` |
| **Negative period total** (net worth down YTD) | `text-negative` | leading `−` |
| **Positive period total** | `text-positive` | leading `+` or bare |

**Do not colour every expense red.** Most rows in a ledger are expenses; painting them
all red depletes red of meaning and makes the genuinely alarming rows (a negative
balance) invisible. The current code already gets this right in `Transactions.tsx`
(negative → `text-slate-100`, positive → `text-emerald-400`) and in `Accounts.tsx`
(a liability balance in red). Formalise it; do not "fix" it.

**The sign is mandatory and is the load-bearing signal.** I checked whether colour
alone could legally carry the distinction and it cannot: under WCAG 1.4.1's
luminance-difference route a colour pair counts as a second visual distinction only
at ≥3:1, and our pairs fall short — positive `#34d399` vs primary text `#f1f5f9` is
1.75:1, negative `#f87171` vs positive `#34d399` is 1.44:1, and in light mode
negative `#b91c1c` vs positive `#047857` is 1.18:1. So the `+`/`−` glyph is required
by conformance, not just by taste.

Use **U+2212 MINUS SIGN** for display, not the hyphen-minus, and do not let the sign
come from `Intl` — it varies by ICU version and locale. Render the magnitude and add
the sign yourself, in one place, so the whole app agrees:

```ts
// lib/format.ts
const MINOR_UNITS: Record<string, number> = { JPY: 0, KRW: 0, VND: 0 };

export function formatMoney(amount: string | number, currency = "USD"): string {
  const n = typeof amount === "string" ? Number(amount) : amount; // display only; ADR-0005
  const dp = MINOR_UNITS[currency] ?? 2;
  // Format the magnitude, then supply the sign ourselves, so the glyph is
  // always U+2212 and never a platform hyphen.
  const body = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  }).format(Math.abs(n));
  return n < 0 ? `−${body}` : body;
}
```

Note `minimumFractionDigits` as well as `maximum`: a value of `12` must render
`$12.00`, not `$12`. Mixed precision inside one column breaks the alignment that
tabular figures bought.

### 6.3 What "positive" means for an expense

An expense is a **negative number** and a **normal event**. The two must not be
confused:

- In a transaction row, a `−$9.99` is neutral-coloured.
- In a **net** figure (net worth, cash flow net, period delta), negative *is* the
  exception worth colouring, because the baseline expectation is that money grows or
  balances.
- Rule of thumb: **colour the aggregate, not the line item.**

### 6.4 Currency display

The household is multi-currency (ADR-0006) and the same glyph recurs across
currencies.

- Use `Intl.NumberFormat` with `narrowSymbol` for the symbol, as above.
- **When a transaction's currency differs from the household base currency, the
  currency code appears in the row's meta line.** `€1,234.00` alone is ambiguous;
  `€1,234.00 · EUR` is not, and a cross-currency transfer leg is exactly where this
  gets misread.
- A converted total always names its base currency at least once per screen
  (`Net worth · USD`).
- The missing-FX-rate warning keeps `text-warning` and its text — currently amber
  text only, which is fine because the words are there, but it needs
  `role="status"` so it is announced when it appears.

### 6.5 Never truncate a number into a different number

- **No abbreviations.** `$1.2K`, `$1.2M`, `1,2 mil` are all forbidden in this app. If
  a figure does not fit, shrink the type, wrap it, or give it its own row — never
  round it in the display.
- **One exception, and it is not an abbreviation: a value axis's tick may be
  shortened only where the short form is the same figure.** `formatMoneyTick()` in
  `src/lib/format.ts` is the implementation and the whole of the licence —
  `2,000` → `$2k` and `2,500` → `$2.5k` (both exact), `1,234` → `$1,234.00`
  (nothing shorter states it). The rule it answers is the one above: a tick is a
  *scale mark*, so it has no room for six characters of zeros on a
  phone-width chart, and a shortened tick that rounded would be the display
  inventing a number the ledger never held. It is never used for a figure a
  reader acts on: amounts, totals and headline figures use `formatMoney`. The
  exact value stays one tap away in the tooltip, and the chart's `aria-label`
  states the finding in full.
- **No CSS truncation on an amount.** `truncate`, `overflow-hidden`, and `text-ellipsis`
  must never be applied to an element rendering a number. Grep in §8.
- A displayed amount equals the stored decimal rounded to the currency's minor unit
  (2 dp; 0 dp for JPY) — never to a coarser precision.
- **A total is always visually distinct** from a row: semibold, with a `border-t`
  above it. It is never just the last row.
- Totals are computed from Decimals server-side (ADR-0005), so the displayed total can
  differ from the sum of the displayed rows by a cent. When it does, say so in one
  muted line beneath the total rather than hiding it — an unexplained one-cent gap is
  exactly the kind of thing this app exists to make trustworthy.

---

### 6.6 Dates

A date is rendered by `src/components/datetime.tsx`. Nothing else turns a date into
text — no `toLocaleDateString`, no `toLocaleTimeString`, no hand-rolled slicing.

**The ISO form is never the visible text.** `YYYY-MM-DD` (and the full instant, for a
moment) belongs in the `title` attribute and in `<time datetime>`; what a person reads
is one of five fixed styles from `src/lib/dates.ts`:

| style | renders | where it is used |
| --- | --- | --- |
| `long` | `Jan 02 2026` | a detail line with room: admin run history, an interest rate's effective date |
| `medium` | `Jan 02` | the default. A date that needs no year: a review card, a chart axis |
| `weekday` | `Mon` | where the weekday is the point |
| `compact` | `Today` / `Yesterday` / `Jan 02` | a list row, where recency is what the reader scans for |
| `relative` | `2 h ago` / `in 2 h` | a machine-driven timestamp only: last synced, next sync, a job run |

The day is zero-padded (`Jan 02`, never `Jan 2`), so a column of dates stays a column
under tabular figures (§6.3).

**Two frames, and picking the wrong one is the bug.**

- A **calendar day** (`transacted_at`, a report point's `date`, an interest
  `rate_date`) is a day, not a moment. It is read from the value's own date part and
  **never converted**, so a transaction dated 2026-09-20 is the 20th wherever the
  reader is standing. Render it with `<Day>`.
- An **instant** (`last_synced_at`, `started_at`, a sync event's `ts`) is a moment, and
  means the viewer's own clock. Render it with `<Instant>`.

The type is the enforcement: `relative` is not a valid style for a calendar day, so
`formatDay(..., "relative")` does not compile.

Two helpers sit outside that table because they are not dates:

- `formatMonth` renders the reports API's `YYYY-MM` key as `Jan` (or `Jan 2026`), for
  the cash-flow axis.
- `formatTime` renders `HH:MM(:SS)`, fixed 24-hour and zero-padded, for a run's event
  log, where two events can share a minute.

**No "Tomorrow".** "Today" and "Yesterday" are named; a future date renders as a date.
Nothing in the ledger is legitimately dated ahead, and naming it would be a kindness to
a typo.

---

## 7. Accessibility floor

Run this list before shipping. Every item is checkable.

1. **Contrast.** Every text pair ≥4.5:1; ≥3:1 only for ≥24 px, or ≥18.5 px bold
   (14 pt bold; 1 pt = 1.333 px). UI boundaries and graphical objects ≥3:1. Ratios are
   thresholds and are **not rounded** — 4.499:1 fails. Use §2.1's table and do not
   invent colours.
2. **Labels.** Every input has a visible `<label>` bound by `htmlFor`/`id`. No
   placeholder-only labels. Every icon-only control has an `aria-label`.
3. **Focus.** `:focus-visible` ring visible on every interactive element; `outline-none`
   never appears without a replacement ring. Tab order matches visual order, and
   nothing focused is entirely hidden behind a sticky bar. (Numbering trap: "2.4.11
   Focus Appearance" was the *draft* label. In final WCAG 2.2, **2.4.11 is Focus Not
   Obscured (Minimum, AA)** — it only forbids *entire* hiding — and Focus Appearance
   moved to **2.4.13 (AAA)**.)
4. **Keyboard.** Every action reachable without a mouse, including horizontal scroll
   containers (`tabIndex={0}`). Escape closes every overlay. Overlays trap focus and
   restore it to the trigger.
5. **Headings.** Exactly one `<h1>` per page and no skipped levels. **Every page now
   has exactly one**, including the five app pages, which used to start at `<h2>`
   and so skipped a level under the shell. Sections below it are `<h2>`, not `<h3>`
   chosen for its size.
6. **Live regions for async results.** A status message must be announced without
   receiving focus. What counts is subtle: a results *list* is not a status message,
   but the message about it is. So:
   - Filter result count → `role="status"` on a wrapper around the **whole**
     "12 transactions" string with `aria-atomic="true"`, not on the bare numeral.
   - Save confirmations, "Copied", "Rate added" → `role="status"`.
   - Errors → `role="alert"`.
   - A dialog is **not** a status message (it takes focus) — do not give dialogs
     `role="status"`.
   **These are now throughout the app** — the net-worth figure, the transactions
   count and its empty state, the review queue's count and its error, the rule
   builder's save, the CSV preview and import result, and every Settings
   mutation. The regression to watch for is a *new* async result added without
   one, which looks identical to a working feature until it is announced to
   nobody.
7. **Reduced motion.** The global CSS rule in §2.3 plus `useReducedMotion()` for
   framer-motion. Known case: `Review.tsx`'s card rotate and the drag-threshold
   flash.
8. **Colour alone** (SC 1.4.1) is never the signal — see §6.2 for why the sign glyph
   is mandatory. Same rule for status: "needs review" is a word, not an amber dot.
9. **Zoom and reflow.** Usable at 200% text zoom (SC 1.4.4) and at 320 CSS px wide
   with no two-dimensional scrolling (SC 1.4.10) — except a table, which may scroll in
   2-D **only inside its own container**; the exception does not extend to the rest of
   the page.
10. **Touch targets** ≥44×44 px (§5).
11. **Per-route `document.title`.** Set in `AppShell.tsx` from the same route table
    that builds the nav, so a new route cannot be added without one. It is the
    primary orientation cue for a screen reader user and for a phone's tab
    switcher.
12. **`lang`** stays `en` on `<html>`.

---

## 8. How to check

**Greps that must return nothing.** These are implemented as
`npm run lint:design` (`frontend/scripts/design-lint.mjs`) and run in CI, so they are
a ratchet rather than a thing to remember. The shell forms are kept below because the
script is a direct transcription of them. Two are not literal greps:
rule 6 has a documented exception (a badge carrying its own padding **and** radius),
so the script implements the exception instead of dropping the rule, rule 8 needs
a lookahead to tell a hand-written `tooltip:` from one that calls the shared builder,
and rule 9 keys on the two shapes of an ISO date that reach the screen rather than on
`.slice(0, 10)`, which a native date input legitimately uses.
All rules ignore comments, because a comment explaining why `outline-none` is absent
would otherwise trip rule 4 — and a check that cries wolf on correct code gets
switched off.

`npm run lint` is **not** this check: it is `eslint src` with no ESLint config in the
repo, so it fails immediately. Design conformance is `lint:design`.

```bash
# 1. Any raw palette class left in the app. Tokens only.
grep -rnE '(bg|text|border|ring|divide|from|to|via|placeholder|outline)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}' src/

# 2. A number being truncated or abbreviated.
grep -rn 'truncate' src/ | grep -iE 'amount|balance|total|money|price'

# 3. Arbitrary colour or px spacing values. env()/calc()/vh are allowed
#    (safe-area insets, sheet heights), hex and px are not.
grep -rnE '(text|bg|border|ring|divide)-\[#|(p|m|gap|space-[xy])-\[[0-9.]+(px|rem)' src/

# 4. outline-none without a ring on the same line.
grep -rn 'outline-none' src/ | grep -v 'ring-'

# 5. A hardcoded hex in a chart option.
#    Scoped to the files that build chart options rather than all of src/pages/:
#    Settings.tsx holds hex legitimately, as the *default colour of a new
#    category* — user data, not a theme value.
grep -rn '#[0-9a-fA-F]\{6\}' src/pages/insights/Overview.tsx src/pages/DesignSystem.tsx src/components/Chart.tsx

# 6. A background token on an inline text element. A `bg-*` here is a text
#    colour that got machine-substituted: it either paints a box where a colour
#    belonged or does nothing at all. Backgrounds go on containers, or on a
#    badge that also carries its own padding and radius.
grep -rnE '<(p|span|h[1-6]|legend)[^>]*className="[^"]*\bbg-(surface|accent|positive|negative|warning|danger)' src/

# 7. "The same token twice in one class string" is deliberately **not** a grep.
#    Two attempts at mechanising it fired on correct code: a ternary puts two
#    alternative class strings on one line (`text-fg` vs `text-fg-muted`), and
#    `bg-surface-inset/60 … hover:bg-surface-inset` is a legitimate alpha change.
#    A lint rule that cries wolf on correct code gets switched off, so this stays
#    a review question instead: does the `hover:`/`focus:` name a *different*
#    value from the base? If it names the same one, it does nothing.

# 8. Hand-written chart interaction. Every tooltip, axis pointer and emphasis
#    comes from theme/chartInteraction.ts (§2.10), which is the only file
#    allowed to name those keys — that is what makes hover behave identically
#    on all seven charts instead of being remembered per chart.
#
#    Not a bare grep, for the same reason rule 6 is not: `tooltip:` appears on
#    correct code in the form `tooltip: chartTooltip(t)`. The script keys on the
#    key *not* being followed by the matching builder, which needs a lookahead
#    (`grep -P`, so not portable to the BSD grep this list is otherwise written
#    for). The shell form is therefore the intent, and the script is the check:
#
#      tooltip:      NOT followed by chartTooltip(
#      axisPointer:  anywhere outside the module
#      emphasis:     NOT followed by emphasisLine( | emphasisBar( | emphasisPie(

# 9. An ISO date as visible text. `2026-01-01` is a wire format, and the date
#    vocabulary in lib/dates.ts is what a person reads; the ISO form belongs in
#    the `title` and the `<time datetime>` (§9.5). Not a bare grep either: a
#    native date input's `value` has to be ISO, so the rule keys on the two
#    shapes that actually reach the screen — an ISO date *interpolated into a
#    string* (`` `${x.slice(0, 10)} to …` ``) and one sitting alone as a JSX
#    child. Both were live in ImportDialog.tsx, where the OFX "Covers" line
#    printed `2026-01-01 to 2026-01-31` at the reader and its own test asserted
#    that it did.
grep -rnE '\$\{[^}]*\.slice\(0, ?10\)|>\{[^}]*\.slice\(0, ?10\)\}<' src/

# 10. A role's text colour on its own tint (`bg-warning/20 … text-warning`). Under
#     4.5:1 in light mode for accent, warning and negative (§2.1); the -ink token
#     is the text colour for a tint. Checked in the same class string.
```

**Viewports to look at, in this order:** 360×640 → 390×844 → 768×1024 → 1280×800.
At 360, check the transaction list, the review deck, the bottom nav, and every
Settings card.

**Tools:**

- DevTools → Rendering → *Emulate CSS `prefers-color-scheme`* — flip both ways with no
  reload; the page must not flash on load (hard-reload with cache disabled to test the
  blocking script).
- DevTools colour picker on any text: it prints the contrast ratio inline. Spot-check
  `text-fg-muted` on `surface-inset`, which is the tightest pair we allow.
- Lighthouse → Accessibility ≥95 on `/login`, `/accounts`, `/transactions`,
  `/insights/overview`, `/settings`. Note the score is a floor, not the goal: it does not test
  target size or live regions.
- axe DevTools for a structural pass.
- DevTools → Accessibility pane → check the **computed accessible name** of every
  icon-only button; "✕" or an empty string is a fail.
- Keyboard pass: from the top of each of the 5 pages, Tab through everything. The ring
  must be visible at every stop and no stop may be off-screen or behind the bottom nav.
- Zoom to 200% at 1280×800: no horizontal page scroll, no clipped text.
- `npx playwright test` — extend `e2e/visual.spec.ts` to snapshot the login page in
  **both** themes (set `localStorage["metalmark-theme"]` before `goto`), and add one spec that
  asserts `page.evaluate(() => document.documentElement.classList.contains("dark"))`
  matches the stored preference.
- A target-size test is cheap and worth having: assert every element matching
  `button, a, input, select` has a bounding box of at least 44×44.

---

## 9. Desktop rules

Written because of a specific complaint, which is worth quoting because the diagnosis is
in it: *"this just looks like a vaguely mobile UI adapted to a larger screen."* That was
accurate, and the cause was one line — `AppShell.tsx` renders every page into
`mx-auto w-full max-w-4xl p-4`, a **896 px** column. At 1440 px that is 272 px of dead
margin per side; at 1920 px it is 512 px. §2.5 has documented a 24 px page gutter at `lg:`
since the token layer landed, and the shell never adopted it.

Widening that number is necessary and **not sufficient**. A phone list at 1280 px is still
a phone list: it shows merchant and one meta line and hides category, account and owner
behind a sheet, because at 360 px it had no choice. At 1280 px it has a choice, and *that*
is what "better use of space" means — **stop hiding information behind a tap.** Space is
spent on content, not on larger type and longer empty rows.

**Breakpoint.** `lg` (1024 px) begins the desktop layout, and it is the only new one — §5's
"only two matter, `sm` and `lg`" stands. Everything below is a `lg:` variant of a layout
that already works at 360 px, never a second layout. **A page must not have two JSX trees
branching on viewport**; that is how the two drift and the untested one rots. If a rule
cannot be expressed as a class variant, it is not a desktop rule.

### 9.1 Content width

One global width is the bug. Width follows what the content *is*:

| Content | Max width | Why |
|---|---|---|
| Ledger lists, tables, Admin | `max-w-7xl` (1280) | Tabular content earns every pixel it can get |
| Insights, Accounts | `max-w-6xl` (1152) | Two-up grids need room to become two-up |
| Settings, forms, dialogs, prose | `max-w-2xl` (672) | A 1280 px-wide paragraph is unreadable |

The shell sets the **widest** case and pages narrow themselves; a page that needs less
wraps its content rather than the shell asking it what it wants. `mx-auto` stays, so a
narrow page is centred rather than pinned left.

**Gutter.** `p-4` becomes `lg:p-6`, per §2.5. That is the whole of the shell change at
this level: `max-w-4xl` → `max-w-7xl`, `p-4` → `lg:p-6`.

### 9.2 The shell at `lg:`

The phone tab bar is already `sm:hidden` and the nav already becomes a horizontal row in
the header. The desktop shape keeps that — **a header with the nav in it** — rather than
growing a second navigation in a left sidebar.

The reason is the destination count. A sidebar pays for itself at fifteen destinations
where a list down the side beats a wrapped row; at six it costs 240 px of every page's
width to save one row of header, and it puts Settings and Admin — the two least-used
items — in the most visually prominent column on the screen. §4.13's rule stands: replace
a nav, or do not add one.

**What the header gains at `lg:`** — nothing. It is already correct. The work is below it.

### 9.3 What each page does with the room

Five shapes, and they are the whole of it:

- **Transactions — list and detail, side by side.** The list takes `lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]`
  with the detail panel in the second column, shown when a row is selected and a quiet
  empty state otherwise. This is the single biggest win: the phone's row → sheet → back
  loop becomes one click with no navigation.
- **Insights (Overview) — two-up.** KPI row spans both columns; the charts pair two per
  row above `lg:`. The Sankey (§2.9) is full-width, because flow diagrams lose their
  meaning when squeezed. The tab strip above it is `ScrollTabs` (§5), not part of this
  shape.
- **Accounts and Admin — card grids.** `lg:grid-cols-2` and `lg:grid-cols-3`. Cards keep
  their internal layout and simply stop being full-width.
- **Settings — rail and content.** The tab list becomes a vertical rail in the first
  column (`lg:w-56`), the active panel in the second. §4.14's roving tabindex and arrow
  keys are unchanged; only the axis changes, and `aria-orientation` goes with it.
- **Review — a deck, and it stays small.** One card at a time, capped at `max-w-2xl` and
  centred. This is the one page whose desktop shape is *not* wider: a decision card is
  read at a glance, and the room would only push the merchant and the amount further
  apart — which is exactly what the 864 px card this replaced did (§4.17). What the
  desktop gets instead of width is the keyboard, ← / →. The transaction detail opens
  **over** the deck as a slide-over (§9.6), never as a pane: a pane is the second column
  of the Transactions shape, and there is no second column here to put it in.

### 9.4 The list row at `lg:`

The phone row (§5) is merchant + one meta line + amount. At `lg:` the row gains **columns,
not height**:

- Category, account and owner stop being sheet-only and get their own columns, with the
  account carrying its mark (§4.6).
- The meta line becomes the columns; it does not stay *and* gain columns beside it.
- `min-h-16` is a phone figure. At `lg:` a row may be `min-h-12` — but **44 px is still the
  floor**, because §5's target rule is not a touch-only rule and one floor is easier to
  keep than two.
- A real header row appears above the list, naming the columns.

**Density is information, never smaller type.** The type scale (§2.4) does not change at
`lg:`. A desktop layout that buys space by shrinking 14 px text to 12 px has not used the
space, it has spent the reader's eyesight on it.

### 9.5 Pointer affordances

A pointer has a hover state and a phone does not, which changes two things and only two:

- **Rows and cards get a hover state** — `hover:bg-surface-inset`, the same token the
  active nav item uses. Purely additive: it says "this is one target", and nothing is
  reachable *only* by hovering.
- **Destructive and rare actions may be hover-revealed** — a delete on a rule row, for
  instance. They must still be focusable and reachable by keyboard (`focus-visible` shows
  them, not only `hover:`), and they must not be the only route to an action someone needs.

**Full timestamps and other detail belong here.** A pretty date (`Jan 02`) reveals its
`YYYY-MM-DD` on hover via `title`. `title` is not an accessible name and is not available
to a touch user, so the ISO value is *also* in the element's accessible text — hover is a
convenience for the sighted mouse user, never the only way to reach the value.

### 9.6 Overlays at `lg:`

§4.8 already sanctions a right **slide-over** at ≥1024 px. §9 makes it the rule: a bottom
sheet is a thumb affordance and there is no thumb; at `lg:` a detail or a form opens as a
right slide-over, or in place per §9.3, and **never as a bottom sheet**. Dialogs (§4.8)
are unchanged at every width.

### 9.7 Test viewports

§5's list gains its two desktop entries: **1280×800** and **1440×900**. The existing
1280×800 was already in the list and was never asserted against; these two are what the
`lg:` variants are checked at.

---

## Appendix — state of the tree

The token layer landed and the pages have been swept. §8's greps #1–#3 return **zero**
hits: no raw palette class, no arbitrary hex or px value, no abbreviated number. The
fourteen hex values in `index.css` are exactly the table in §2.1, and `text-fg-muted`
is used 106 times, so the text hierarchy survived.

That last point is worth one sentence, because an earlier attempt at the same sweep
got it wrong: it substituted muted *text* roles with `bg-surface-raised`, which took
`text-fg-muted` to 0 uses and left every `hover:` a no-op against the card behind it.
Grep #6 is what catches that, so keep it in CI rather than running it once.

### The audit below is closed

**Every row of the table that used to follow this line has been fixed**, verified
against the tree on 2026-09-20. It is kept, struck through, because a list of what was
wrong and how each item was caught is worth more than a clean page — but **it is not a
list of open problems, and reading it as one wastes an afternoon.** The counts below are
the ones that were true when the audit was written; the parenthetical is what a re-check
found.

| Problem (as audited) | Verified 2026-09-20 |
|---|---|
| Destructive button's label failed contrast in both themes | Fixed — `danger` is now `bg-danger text-white` (6.47:1), the exact figure §2.1 gives |
| Focus ring suppressed on every control (`outline-none` beat the global rule) | Fixed — **zero** real occurrences; the two remaining hits are comments explaining why it is *not* used, and §8 grep #4 is clean |
| Controls had no `min-h-11` (`px-3 py-2 text-sm` = 36 px) | Fixed — `min-h-11` is on the base control class, with the 36 px arithmetic in the docstring |
| 19 controls at 24–28 px | Fixed — the lint is clean; `min-h-11` appears 20 times |
| No safe-area inset anywhere (`env()` used 0 times) | Fixed — 2 uses, including the bottom tab bar |
| `useChartTokens` read names that do not exist, so hardcoded dark fallbacks painted in light mode | Fixed |
| `Reports.tsx` hardcoded 16 hex values and kept the dark donut palette in both themes | Fixed — **0** hex values in that file |
| Zero live regions (0 × `aria-live`, 0 × `role="status"`) | Fixed — 12 |
| Zero `aria-current`; active nav item and tab were colour-only | Fixed — 3; `NavLink` sets it itself |
| No `<h1>` on any authenticated page | Fixed — every page has one |
| Members table had no `<caption>` and no `scope=` | Fixed — both present |

**How to re-run it** rather than trusting a snapshot: `npm run lint:design` in the `web`
container covers §8's rules 1–6, 8 and 9; the rest are greps —

```sh
grep -rn 'aria-live\|role="status"' src | wc -l     # live regions
grep -rn 'aria-current' src | wc -l                 # active-state signal
grep -rn 'safe-area-inset' src | wc -l              # edge-pinned elements
grep -l '<h1' src/pages/*.tsx                       # page headings
grep -rn 'outline-none' src                         # each hit needs a ring (§8 #4)
```

An audit is a claim about a commit, so **date it and say what it did not check.** This
one checked the eleven items above; it did not measure contrast across the app, and it is
not a substitute for a real screen-reader pass.

What follows is the audit as written, with its original counts:

| Problem | Where | Breaks |
|---|---|---|
| **The destructive button's label fails contrast in both themes.** `danger` is `bg-negative/90 text-fg`, and `text-fg` flips with the theme — so it is light-on-light-red in dark (**3.07:1**) and dark-on-dark-red in light (**3.16:1**). That fill needs a fixed `#ffffff` label (**6.47:1**), per §2.1 | `form.tsx` `VARIANTS.danger` | 1.4.3 |
| **The focus ring is suppressed on every control.** `outline-none` is a class (specificity 0-1-0) and beats the global `:where(…):focus-visible` rule in `index.css` (0-0-0), so the ring never paints. Six sites, and both Dialog branches have **no** compensating indicator at all | `form.tsx:15`, `Dialog.tsx:87–88`, `Login.tsx:45,56`, `Signup.tsx:39` | 2.4.7; grep #4 fires on all 6 |
| Controls have no `min-h-11` — `px-3 py-2 text-sm` is **36 px**. `min-h-11` appears exactly once, in `ThemeToggle.tsx` | `form.tsx` | below 44 px; §5 |
| **19** controls at **24–28 px** (`px-2 py-1`, `px-3 py-1`) — chips, dialog close, Accounts "Edit" | `Dialog.tsx:98`, `Accounts.tsx:118`, `OwnerFilterChips.tsx:21` | below 44 px |
| No safe-area inset anywhere — `env(safe-area-inset-*)` used **0** times | `AppShell.tsx` bottom nav | sits under the home indicator |
| `useChartTokens` reads `--success`, `--content`, `--content-muted`; `index.css` defines `--positive`, `--fg`, `--fg-muted`. `getPropertyValue` returns `""` for a name that does not exist, so the **hardcoded fallback** is used — `#34d399` and `#f1f5f9`, both dark-theme values, in light mode | `src/theme/chartTokens.ts:61–65` | §2.9; white-on-white chart text |
| `Reports.tsx` still hardcodes **16** hex values, keeps the dark `DONUT_COLORS` in both themes (`#fbbf24` = **1.67:1** on white), and does not import `useChartTokens` | `Reports.tsx` | 1.4.11; grep #5 fires |
| **Zero** live regions — 0 × `aria-live`, 0 × `role="status"` | whole app | 4.1.3 |
| **Zero** `aria-current`; the active nav item and active tab are colour-only | `AppShell.tsx`, `Settings.tsx` | 1.4.1 |
| No `<h1>` on any authenticated page (only Login and Signup have one) | Accounts, Transactions, Review, Reports, Settings | 1.3.1 |
| Members table has no `<caption>` and no `scope=` | `Settings.tsx` | 1.3.1 |
| `disabled:opacity-50` is back on controls; the primary button composites to **3.30:1** when disabled | `form.tsx` | §4.3 — use explicit disabled colours |
| `ThemeToggle` and `ThemeButton` are mounted nowhere | — | §3 has no entry point |

Everything else §7 asks for is caught by the other five checks in §8 — the keyboard
pass, the computed accessible names, 200% zoom, Lighthouse and the target-size
assertion. None of those is greppable, which is why they are in the list.

