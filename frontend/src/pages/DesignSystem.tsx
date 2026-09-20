// The design system, rendered from the real components.
//
// This page exists to answer one question: "what does this app's UI actually
// look like right now?" Everything here is imported, not re-implemented — a
// gallery of copies would drift from the app within a week and start lying,
// which is worse than having no gallery.
//
// Add a component to the app, add it here in the same commit.
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import Chart from "@/components/Chart";
import Dialog from "@/components/Dialog";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button, Checkbox, Field, Input, Select, Spinner, Textarea, useFieldId } from "@/components/form";
import { CloseIcon } from "@/components/icons";
import { useTheme } from "@/theme/theme";
import { token, useChartTokens } from "@/theme/chartTokens";
import { formatMoney } from "@/lib/format";
import type { Owner } from "@/api/types";

// ---- scaffolding --------------------------------------------------------

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-card bg-surface-raised p-4 sm:p-6">
      <div>
        <h2 className="text-lg font-medium text-fg">{title}</h2>
        {note && <p className="mt-1 text-sm text-fg-muted">{note}</p>}
      </div>
      {children}
    </section>
  );
}

/** One token, its live value, and the surface it is meant to be read on. */
function Swatch({ name, needs }: { name: string; needs?: string }) {
  const { resolved } = useTheme();
  // Re-read per theme: the whole point of the swatch is that it shows the value
  // the current theme resolves to, not a value copied into this file.
  const value = useMemo(() => {
    try {
      return token(name);
    } catch {
      return "— undefined —";
    }
  }, [name, resolved]);

  return (
    <li className="rounded-control border border-border p-2" data-testid={`swatch-${name}`}>
      <div
        className="h-10 rounded border border-border"
        style={{ backgroundColor: `rgb(var(--${name}))` }}
      />
      <p className="mt-2 break-all text-xs font-medium text-fg">--{name}</p>
      <p className="break-all text-xs text-fg-muted">{value}</p>
      {needs && <p className="text-xs text-fg-muted">{needs}</p>}
    </li>
  );
}

// ---- page ---------------------------------------------------------------

const SURFACES = [
  { name: "surface", needs: "page background" },
  { name: "surface-raised", needs: "cards" },
  { name: "surface-inset", needs: "wells, inputs, chips" },
];
const LINES = [
  { name: "border", needs: "decorative, no minimum" },
  { name: "border-strong", needs: "3:1 — control boundaries" },
];
const TEXT = [
  { name: "fg", needs: "4.5:1" },
  { name: "fg-muted", needs: "4.5:1 — the floor" },
  { name: "accent-fg", needs: "on accent" },
];
const STATUS = [
  { name: "accent", needs: "accent text and fill" },
  { name: "positive", needs: "income, gains" },
  { name: "negative", needs: "liabilities, losses" },
  { name: "warning", needs: "missing FX rate" },
  { name: "danger", needs: "destructive fill only" },
  { name: "focus", needs: "3:1 — focus ring" },
];

const TYPE_SCALE = [
  { cls: "text-xs", label: "12 / 16", use: "Row meta only. Floor — never money, never a label." },
  { cls: "text-sm", label: "14 / 20", use: "Body, form labels (desktop)." },
  { cls: "text-base", label: "16 / 24", use: "Inputs on touch, amounts, list primaries." },
  { cls: "text-lg", label: "18 / 28", use: "Section headings." },
  { cls: "text-xl", label: "20 / 28", use: "Card hero figure." },
  { cls: "text-2xl", label: "24 / 32", use: "Report headline figure." },
  { cls: "text-3xl", label: "30 / 36", use: "Net worth — one per screen." },
];

const DEMO_OWNERS: Owner[] = [
  { id: "o1", name: "Alex", kind: "person", sort: 1 },
  { id: "o2", name: "Beth", kind: "person", sort: 2 },
  { id: "o3", name: "Shared", kind: "shared", sort: 99 },
];

export default function DesignSystem() {
  const t = useChartTokens();
  const [chips, setChips] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const [checks, setChecks] = useState({ a: true, b: false });
  const ids = {
    text: useFieldId("ds-text"),
    money: useFieldId("ds-money"),
    hint: useFieldId("ds-hint"),
    err: useFieldId("ds-err"),
    req: useFieldId("ds-req"),
    sel: useFieldId("ds-sel"),
    area: useFieldId("ds-area"),
  };

  const lineOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 20, right: 16, bottom: 30, left: 60 },
      tooltip: { trigger: "axis", backgroundColor: t.surface, borderColor: t.border, textStyle: { color: t.fg } },
      xAxis: {
        type: "category",
        data: ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
        axisLine: { lineStyle: { color: t.axis } },
        axisLabel: { color: t.label },
      },
      yAxis: {
        type: "value",
        axisLine: { lineStyle: { color: t.axis } },
        axisLabel: { color: t.label },
        splitLine: { lineStyle: { color: t.split } },
      },
      series: [
        {
          type: "line",
          smooth: true,
          areaStyle: { opacity: 0.15 },
          lineStyle: { color: t.accent },
          itemStyle: { color: t.accent },
          data: [1200, 1480, 1310, 1720, 1650, 2040],
        },
      ],
    }),
    [t],
  );

  const barOption: EChartsOption = useMemo(
    () => ({
      grid: { top: 30, right: 16, bottom: 30, left: 60 },
      tooltip: { trigger: "axis", backgroundColor: t.surface, borderColor: t.border, textStyle: { color: t.fg } },
      legend: { top: 0, textStyle: { color: t.label } },
      xAxis: {
        type: "category",
        data: ["Jan", "Feb", "Mar", "Apr"],
        axisLine: { lineStyle: { color: t.axis } },
        axisLabel: { color: t.label },
      },
      yAxis: {
        type: "value",
        axisLine: { lineStyle: { color: t.axis } },
        axisLabel: { color: t.label },
        splitLine: { lineStyle: { color: t.split } },
      },
      series: [
        { name: "Income", type: "bar", stack: "cf", itemStyle: { color: t.positive }, data: [3200, 3400, 3100, 3600] },
        { name: "Expense", type: "bar", stack: "cf", itemStyle: { color: t.negative }, data: [-2100, -2450, -1980, -2300] },
      ],
    }),
    [t],
  );

  const donutOption: EChartsOption = useMemo(
    () => ({
      tooltip: { trigger: "item", backgroundColor: t.surface, borderColor: t.border, textStyle: { color: t.fg } },
      legend: { bottom: 0, textStyle: { color: t.label }, type: "scroll" },
      color: t.series,
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          itemStyle: { borderColor: t.surface, borderWidth: 2 },
          label: { color: t.label },
          data: [
            { name: "Groceries", value: 820 },
            { name: "Transport", value: 410 },
            { name: "Utilities", value: 260 },
            { name: "Dining", value: 190 },
            { name: "Other", value: 140 },
          ],
        },
      ],
    }),
    [t],
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-lg font-medium text-fg">Design system</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Every control below is the component the app itself renders. Flip the theme to check
          both palettes; the specification is <code>docs/DESIGN.md</code>.
        </p>
      </header>

      <Section title="Theme" note="Light / dark / system. The choice is stored and applied before first paint.">
        <ThemeToggle />
      </Section>

      <Section title="Colour: surfaces, lines, text" note="Contrast measured against the worst background each is used on.">
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {SURFACES.map((s) => (
            <Swatch key={s.name} {...s} />
          ))}
          {LINES.map((s) => (
            <Swatch key={s.name} {...s} />
          ))}
          {TEXT.map((s) => (
            <Swatch key={s.name} {...s} />
          ))}
        </ul>
      </Section>

      <Section title="Colour: status and charts">
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {STATUS.map((s) => (
            <Swatch key={s.name} {...s} />
          ))}
          {Array.from({ length: 10 }, (_, i) => (
            <Swatch key={`chart-${i + 1}`} name={`chart-${i + 1}`} needs="categorical series" />
          ))}
        </ul>
      </Section>

      <Section title="Type scale" note="Never below 12px. Inputs are 16px on touch — anything less and iOS zooms the viewport on focus.">
        <ul className="divide-y divide-border">
          {TYPE_SCALE.map((r) => (
            <li key={r.cls} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2">
              <span className={`${r.cls} font-medium text-fg`}>The quick brown fox</span>
              <span className="text-xs text-fg-muted">
                {r.cls} · {r.label}
              </span>
              <span className="w-full text-xs text-fg-muted sm:w-auto sm:flex-1">{r.use}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Buttons" note="One primary per screen. min-h-11 (44px) is on the base class, so a call site cannot shrink the target.">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          {/* Destructive belongs inside a confirmation only — never as the first
              thing a user sees. */}
          <Button variant="danger">Destructive</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled>Disabled</Button>
          <Button aria-busy="true" disabled>
            <Spinner />
            Saving
          </Button>
        </div>
        <p className="text-xs text-fg-muted">
          A disabled button needs a reason next to it, and a loading button keeps its label and
          sets <code>aria-busy</code>.
        </p>
      </Section>

      <Section title="Icon-only buttons" note="Every one has an accessible name. There is no 'the icon is obvious' exception.">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            aria-label="Delete tag"
            className="grid size-11 place-items-center rounded-control text-fg-muted hover:bg-surface-inset hover:text-negative"
          >
            <CloseIcon />
          </button>
          <button
            type="button"
            aria-label="Close"
            className="grid size-11 place-items-center rounded-control text-fg-muted hover:bg-surface-inset hover:text-fg"
          >
            <CloseIcon />
          </button>
        </div>
        <p className="text-xs text-fg-muted">
          The icon is decorative and the name comes from <code>aria-label</code> — a destructive
          icon says "Delete" and never relies on red alone.
        </p>
      </Section>

      <Section title="Text fields" note="Label above, always visible. Errors are announced, not just coloured.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Merchant" htmlFor={ids.text}>
            <Input id={ids.text} placeholder="Whole Foods" />
          </Field>
          <Field label="Amount" htmlFor={ids.money} hint="Negative for an expense.">
            <Input id={ids.money} inputMode="decimal" defaultValue="-54.32" />
          </Field>
          <Field label="With hint" htmlFor={ids.hint} hint="Shown until an error replaces it.">
            <Input id={ids.hint} />
          </Field>
          <Field label="With error" htmlFor={ids.err} error="Enter a valid number">
            <Input id={ids.err} defaultValue="abc" />
          </Field>
          <Field label="Required" htmlFor={ids.req} required hint="The asterisk is aria-hidden; the input carries `required`.">
            <Input id={ids.req} />
          </Field>
          <Field label="Account" htmlFor={ids.sel}>
            <Select id={ids.sel} defaultValue="checking">
              <option value="checking">Checking</option>
              <option value="savings">Savings</option>
              <option value="card">Credit card</option>
            </Select>
          </Field>
          <Field label="Notes" htmlFor={ids.area} className="sm:col-span-2">
            <Textarea id={ids.area} rows={3} placeholder="Optional" />
          </Field>
        </div>
      </Section>

      <Section title="Checkboxes" note="The label is the target — a native box is ~13px, the row is 44px.">
        <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
          <Checkbox
            label="Hidden"
            hint="Exclude from net worth views."
            checked={checks.a}
            onChange={(e) => setChecks((c) => ({ ...c, a: e.target.checked }))}
          />
          <Checkbox
            label="Enabled"
            checked={checks.b}
            onChange={(e) => setChecks((c) => ({ ...c, b: e.target.checked }))}
          />
          <Checkbox label="Disabled" disabled />
          <Checkbox label="Disabled, checked" disabled defaultChecked />
        </div>
      </Section>

      <Section title="Chips and filters" note="A real <button aria-pressed>, so selection is never carried by colour alone.">
        <OwnerFilterChips owners={DEMO_OWNERS} value={chips} onChange={setChips} />
        <div className="flex flex-wrap items-center gap-2 pt-2">
          <span className="rounded-full bg-accent/15 px-3 py-1 text-sm text-accent">Badge</span>
          <span className="rounded-full bg-negative/20 px-3 py-1 text-sm text-negative">Overdue</span>
          <span className="rounded-full bg-surface-inset px-3 py-1 text-sm text-fg">Neutral</span>
        </div>
        <p className="text-xs text-fg-muted">
          A tag or badge always carries its text. The <code>categories.color</code> column is for
          charts and icons, never the only rendering of a category.
        </p>
      </Section>

      <Section title="Money and numbers" note="Tabular figures, right-aligned, fixed 2dp. No abbreviations — $1.2K is forbidden.">
        <ul className="divide-y divide-border">
          {[
            { label: "Expense (neutral)", amount: "-54.32", cls: "text-fg" },
            { label: "Income", amount: "1200.00", cls: "text-positive" },
            { label: "Negative balance", amount: "-4820.11", cls: "text-negative" },
            { label: "EUR, cross-currency", amount: "1234.00", cls: "text-fg", ccy: "EUR" },
            { label: "JPY, zero minor units", amount: "1500", cls: "text-fg", ccy: "JPY" },
          ].map((r) => (
            <li key={r.label} className="flex items-center gap-3 py-2">
              <span className="min-w-0 flex-1 truncate text-sm text-fg-muted">{r.label}</span>
              {/* shrink-0 and text-right: the label gives way, the number never does. */}
              <span className={`shrink-0 text-right text-base font-semibold tabular-nums ${r.cls}`}>
                {formatMoney(r.amount, r.ccy ?? "USD")}
              </span>
            </li>
          ))}
          <li className="flex items-center gap-3 border-t border-border py-2">
            <span className="min-w-0 flex-1 text-sm font-medium text-fg">Total</span>
            <span className="shrink-0 text-right text-base font-semibold tabular-nums text-fg">
              {formatMoney("15234.56")}
            </span>
          </li>
        </ul>
        <p className="text-xs text-fg-muted">
          Why the sign glyph is mandatory: positive and negative are only 1.44–1.75:1 apart, so
          colour cannot legally carry the distinction on its own (WCAG 1.4.1).
        </p>
      </Section>

      <Section title="Charts" note="Read from the CSS variables at runtime and re-mounted on theme change, so they follow the theme.">
        <div className="space-y-6">
          <Chart option={lineOption} label="Net worth rose steadily from $1,200 in January to $2,040 in June." testid="ds-chart-line" />
          <Chart option={barOption} label="Income exceeded expenses in each of the four months shown." testid="ds-chart-bar" />
          <Chart option={donutOption} label="Spending by category, largest is Groceries at $820." testid="ds-chart-donut" />
        </div>
      </Section>

      <Section title="Dialog / bottom sheet" note="Bottom sheet under 640px, centred modal above it. One component.">
        <Button variant="secondary" onClick={() => setSheet(true)} data-testid="ds-open-dialog">
          Open dialog
        </Button>
        <Dialog
          open={sheet}
          onClose={() => setSheet(false)}
          title="Edit account"
          testid="ds-dialog"
          footer={
            <>
              <Button variant="secondary" onClick={() => setSheet(false)}>
                Cancel
              </Button>
              <Button onClick={() => setSheet(false)}>Save</Button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="Name" htmlFor={ids.text}>
              <Input id={ids.text} defaultValue="Everyday Checking" />
            </Field>
            <p className="text-sm text-fg-muted">
              Resize the window below 640px: this becomes a bottom sheet with a drag handle and a
              safe-area inset.
            </p>
          </div>
        </Dialog>
      </Section>

      <Section title="Empty states" note="Four distinct states, never conflated. A first-run state is never shown on error.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-card border border-border p-6 text-center">
            <p className="text-base font-medium text-fg">No accounts yet</p>
            <p className="mt-1 text-sm text-fg-muted">Add one to start tracking your net worth.</p>
            <Button className="mt-3">Add account</Button>
          </div>
          <div className="rounded-card border border-border p-6 text-center">
            <p className="text-base font-medium text-fg">No transactions match these filters.</p>
            <Button variant="secondary" className="mt-3">
              Clear filters
            </Button>
          </div>
          <div className="rounded-card border border-negative p-6 text-center">
            <p className="text-base font-medium text-negative">Couldn't load your transactions.</p>
            <Button variant="secondary" className="mt-3">
              Retry
            </Button>
          </div>
          <div className="space-y-2 rounded-card border border-border p-6" aria-hidden="true">
            <div className="h-4 w-1/3 rounded bg-surface-inset" />
            <div className="h-4 w-2/3 rounded bg-surface-inset" />
            <div className="h-4 w-1/2 rounded bg-surface-inset" />
            <p className="pt-1 text-xs text-fg-muted">Skeleton — reserves the real row height.</p>
          </div>
        </div>
      </Section>

      <Section title="Live regions" note="Async results are announced without moving focus. There are currently none in this gallery — a live region only speaks when its content changes.">
        <p className="text-sm text-fg-muted" role="status" aria-atomic="true">
          12 transactions
        </p>
        <p className="text-sm text-negative" role="alert">
          <span aria-hidden="true">⚠ </span>Couldn't save. Check your connection.
        </p>
      </Section>
    </div>
  );
}
