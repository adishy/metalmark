// The design system, rendered from the real components.
//
// This page exists to answer one question: "what does this app's UI actually
// look like right now?" Everything here is imported, not re-implemented — a
// gallery of copies would drift from the app within a week and start lying,
// which is worse than having no gallery.
//
// Add a component to the app, add it here in the same commit.
import { useCallback, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import Chart from "@/components/Chart";
import Dialog from "@/components/Dialog";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button, Checkbox, Field, Input, Select, Spinner, Textarea, useFieldId } from "@/components/form";
import { CloseIcon } from "@/components/icons";
import { useTheme } from "@/theme/theme";
import { token, useChartTokens } from "@/theme/chartTokens";
import {
  barEndRadius,
  chartArea,
  chartAxis,
  chartLegend,
  chartTooltip,
  emphasisBar,
  emphasisLine,
  emphasisPie,
  valueTicks,
  zeroRule,
  type ChartBox,
} from "@/theme/chartInteraction";
import { sliceLabels } from "@/lib/donutChart";
import { formatDuration, formatMoney, formatMoneyTick } from "@/lib/format";
import { formatDay, formatMonth, relativeTime, todayIso, type DayStyle } from "@/lib/dates";
import AccountMark from "@/components/AccountMark";
import { MetalMark } from "@/components/MetalMark";
import { Day, Instant, Time } from "@/components/datetime";
import ConnectionBadge from "@/components/ConnectionBadge";
import type { Connection, Owner } from "@/api/types";

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

/** The reference clock the elapsed-time gallery is pinned to. */
const DEMO_NOW = new Date("2026-09-20T12:00:00Z");

/** The day styles, each with the surface that wants it. Written out rather than
 *  derived, because "which style does this surface want" is the decision this
 *  table exists to record. */
const DAY_STYLES: { style: DayStyle; label: string; value: string; use: string }[] = [
  {
    style: "long",
    label: "long",
    value: "2026-01-02T12:00:00Z",
    use: "A standalone date — a page heading, a run's start — where nothing nearby says the year.",
  },
  {
    style: "medium",
    label: "medium",
    value: "2026-01-02T12:00:00Z",
    use: "Card meta and chart axes: the year is already known, and the day is what varies.",
  },
  {
    style: "weekday",
    label: "weekday",
    value: "2026-01-02T12:00:00Z",
    use: "Beside a clock time, so a weekday reads without arithmetic.",
  },
  {
    style: "compact",
    label: "compact",
    value: "2026-01-02T12:00:00Z",
    use: "The transaction row — the densest surface in the app, so it gets the shortest vocabulary.",
  },
];

/** A realistic set of accounts, chosen to show the two things the mark has to
 *  get right: two accounts at one institution (Chase Checking / Chase Savings —
 *  same hue, different letters) and an institution that is not in the curated
 *  map at all (Zzyzx), which falls back to the name's own hash. */
const DEMO_ACCOUNTS: { name: string; institution: string | null }[] = [
  { name: "Chase Checking", institution: "Chase" },
  { name: "Chase Savings", institution: "Chase" },
  { name: "Amex", institution: "American Express" },
  { name: "Capital One Venture", institution: "Capital One" },
  { name: "Fidelity 401k", institution: "Fidelity" },
  { name: "Rainy Day Fund", institution: "Zzyzx Credit Union" },
];

/** One row per state the badge can be in — including the last one, which is the
 *  combination a single status field could not express. */
function demoConnection(
  id: string,
  org_name: string,
  status: Connection["status"],
  is_enabled = true,
): Connection {
  return {
    id,
    provider: "simplefin",
    org_name,
    status,
    last_synced_at: null,
    last_error: null,
    is_enabled,
    sync_interval_minutes: 360,
    next_sync_at: null,
    created_at: "2026-09-01T00:00:00Z",
  };
}

const DEMO_CONNECTIONS: Connection[] = [
  demoConnection("c1", "Everyday Bank", "ok"),
  demoConnection("c2", "Credit Union", "error"),
  demoConnection("c3", "Brokerage", "auth_error"),
  demoConnection("c4", "Brokerage (switched off)", "auth_error", false),
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

  const lineOption = useCallback(
    (box: ChartBox): EChartsOption => ({
      grid: { top: 16, right: 8, bottom: 8, left: 8, containLabel: true },
      tooltip: chartTooltip(t),
      xAxis: {
        type: "category",
        data: ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
        ...chartAxis(t),
      },
      yAxis: {
        type: "value",
        ...chartAxis(t, {
          grid: true,
          tick: (v) => formatMoneyTick(v, "USD"),
          splitNumber: valueTicks(box),
        }),
      },
      series: [
        {
          type: "line",
          smooth: true,
          areaStyle: chartArea(t),
          lineStyle: { color: t.accent },
          itemStyle: { color: t.accent },
          emphasis: emphasisLine(t, { area: true }),
          data: [1200, 1480, 1310, 1720, 1650, 2040],
        },
      ],
    }),
    [t],
  );

  const barOption = useCallback(
    (box: ChartBox): EChartsOption => ({
      grid: { top: 30, right: 8, bottom: 8, left: 8, containLabel: true },
      tooltip: chartTooltip(t),
      legend: chartLegend(t, { top: 0 }),
      xAxis: {
        type: "category",
        data: ["Jan", "Feb", "Mar", "Apr"],
        ...chartAxis(t),
      },
      yAxis: {
        type: "value",
        ...chartAxis(t, {
          grid: true,
          tick: (v) => formatMoneyTick(v, "USD"),
          splitNumber: valueTicks(box),
        }),
      },
      series: [
        {
          name: "Income",
          type: "bar",
          stack: "cf",
          itemStyle: { color: t.positive, borderRadius: barEndRadius("top") },
          emphasis: emphasisBar(t, t.positive),
          markLine: zeroRule(t),
          data: [3200, 3400, 3100, 3600],
        },
        {
          name: "Expense",
          type: "bar",
          stack: "cf",
          itemStyle: { color: t.negative, borderRadius: barEndRadius("bottom") },
          emphasis: emphasisBar(t, t.negative),
          data: [-2100, -2450, -1980, -2300],
        },
      ],
    }),
    [t],
  );

  const donutOption = useCallback(
    (box: ChartBox): EChartsOption => ({
      tooltip: chartTooltip(t, { trigger: "item" }),
      legend: chartLegend(t, { bottom: 0, type: "scroll" }),
      color: t.series,
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          itemStyle: { borderColor: t.surface, borderWidth: 2 },
          // The same rule the real donut follows, from the same module: the
          // design system shows the app's charts, not a second set of them.
          ...sliceLabels(t, box),
          emphasis: emphasisPie(t),
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
        <h1 className="text-xl font-semibold text-fg">Design system</h1>
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
          <span className="rounded-full bg-accent/15 px-3 py-1 text-sm text-accent-ink">Badge</span>
          <span className="rounded-full bg-negative/20 px-3 py-1 text-sm text-negative-ink">Overdue</span>
          <span className="rounded-full bg-surface-inset px-3 py-1 text-sm text-fg">Neutral</span>
        </div>
        <p className="text-xs text-fg-muted">
          A tag or badge always carries its text. The <code>categories.color</code> column is for
          charts and icons, never the only rendering of a category.
        </p>
      </Section>

      <Section
        title="Connection status"
        note="Two chips, because health and pause are two different facts — the fourth row is a bank that is broken AND switched off, and one label cannot say both."
      >
        <div className="space-y-2">
          {DEMO_CONNECTIONS.map((c) => (
            <div key={c.status + String(c.is_enabled)} className="flex flex-wrap items-center gap-2">
              <span className="w-32 text-sm text-fg-muted">{c.org_name}</span>
              <ConnectionBadge connection={c} />
            </div>
          ))}
        </div>
        <p className="text-xs text-fg-muted">
          The wording is the component's, not each page's: <code>auth_error</code> reads
          “Reconnect needed”, because the credential is a bank access URL and the action a person
          can take is to authorise again at the bridge — not to retype a password.
        </p>
      </Section>

      <Section
        title="Elapsed time"
        note="Coarse on purpose, and floored rather than rounded so the label never overstates how long something has taken."
      >
        {/* A fixed reference clock, so the gallery shows the same six phrases
            tomorrow as today. The app itself reads the viewer's own clock —
            `relativeTime`'s `now` argument exists for tests and for this. */}
        <ul className="divide-y divide-border">
          {[
            { label: "A few seconds", iso: "2026-09-20T11:59:32Z" },
            { label: "Minutes", iso: "2026-09-20T11:22:00Z" },
            { label: "Hours", iso: "2026-09-20T03:00:00Z" },
            { label: "Days", iso: "2026-09-15T12:00:00Z" },
            { label: "Older than a week", iso: "2026-07-03T12:00:00Z" },
            { label: "In the future", iso: "2026-09-20T14:30:00Z" },
          ].map((r) => (
            <li key={r.label} className="flex items-center gap-3 py-2">
              <span className="min-w-0 flex-1 text-sm text-fg-muted">{r.label}</span>
              <span className="shrink-0 text-sm text-fg tabular-nums">
                {relativeTime(r.iso, DEMO_NOW)}
              </span>
            </li>
          ))}
          <li className="flex items-center gap-3 border-t border-border py-2">
            <span className="min-w-0 flex-1 text-sm text-fg-muted">Durations</span>
            <span className="shrink-0 text-sm text-fg tabular-nums">
              {formatDuration(412)} · {formatDuration(1840)} · {formatDuration(64_000)} ·{" "}
              {formatDuration(3_930_000)}
            </span>
          </li>
        </ul>
        <p className="text-xs text-fg-muted">
          Past a week the phrase hands off to a date: “37 d ago” is a worse answer than the day it
          happened.
        </p>
      </Section>

      <Section
        title="Dates"
        note="One vocabulary, five styles, fixed rather than locale-aware — so the same ledger reads the same on two machines. The ISO form is never the visible text: it is the title (hover, and on focus) and the <time datetime> value, which is why every date below carries one."
      >
        <ul className="divide-y divide-border">
          {DAY_STYLES.map((r) => (
            <li key={r.label} className="flex items-center gap-3 py-2">
              <span className="w-20 shrink-0 font-mono text-xs text-fg-muted">{r.label}</span>
              <span className="shrink-0 text-sm text-fg tabular-nums">
                <Day value={r.value} style={r.style} />
              </span>
              <span className="min-w-0 flex-1 text-xs text-fg-muted">{r.use}</span>
            </li>
          ))}
          {/* The two relative words, which are by definition relative to when
              this page is read — the only rows here that move. */}
          <li className="flex items-center gap-3 py-2">
            <span className="w-20 shrink-0 font-mono text-xs text-fg-muted">compact</span>
            <span className="shrink-0 text-sm text-fg tabular-nums">
              <Day value={`${todayIso()}T12:00:00Z`} style="compact" />
            </span>
            <span className="min-w-0 flex-1 text-xs text-fg-muted">
              Today — and Yesterday for the day before it. There is no “Tomorrow”: nothing in a
              ledger is legitimately dated ahead.
            </span>
          </li>
          <li className="flex items-center gap-3 py-2">
            <span className="w-20 shrink-0 font-mono text-xs text-fg-muted">instant</span>
            <span className="shrink-0 text-sm text-fg tabular-nums">
              <Instant value="2026-01-02T14:03:07Z" style="relative" />
            </span>
            <span className="min-w-0 flex-1 text-xs text-fg-muted">
              An instant, not a day: converted to the viewer's local clock, because someone asking
              when a sync ran means their own.
            </span>
          </li>
          <li className="flex items-center gap-3 py-2">
            <span className="w-20 shrink-0 font-mono text-xs text-fg-muted">time</span>
            <span className="shrink-0 text-sm text-fg tabular-nums">
              <Time value="2026-01-02T14:03:07Z" />
            </span>
            <span className="min-w-0 flex-1 text-xs text-fg-muted">
              Fixed 24-hour and fixed width, for the one column that needs a clock time without a
              date: a run's event log, where two events can share a second.
            </span>
          </li>
          <li className="flex items-center gap-3 py-2">
            <span className="w-20 shrink-0 font-mono text-xs text-fg-muted">month</span>
            <span className="shrink-0 text-sm text-fg tabular-nums">
              {formatMonth("2026-01")} · {formatMonth("2026-01", "long")}
            </span>
            <span className="min-w-0 flex-1 text-xs text-fg-muted">
              The one thing the five styles do not cover — a month axis is not a day. Twelve short
              labels are what fit a phone (§5).
            </span>
          </li>
        </ul>
        <p className="text-xs text-fg-muted">
          A <strong>calendar day</strong> is read from the value's own date part and never
          converted: a transaction dated {formatDay("2026-01-02", "medium")} is the 2nd in the
          ledger wherever the reader is standing. An <strong>instant</strong> is converted to local
          time. Getting those two the wrong way round is the entire failure mode, so the frame is
          in the component's name — <code>&lt;Day&gt;</code> or <code>&lt;Instant&gt;</code> — and
          not a prop.
        </p>
      </Section>

      <Section
        title="The mark"
        note="The app's own mark — the metalmark, a Riodinidae butterfly — at the sizes it is used, down to the 16 px a tab strip gives it. It is one file: scripts/metalmark-mark.mjs draws it, `npm run icons` writes it to public/, and this component is an <img> of the SVG the tab is already using. So the picture in the tab, in the launcher, on a desktop notification and beside the wordmark are the same bytes and cannot drift apart."
      >
        <div className="flex flex-wrap items-end gap-6">
          <div className="flex flex-col items-center gap-2">
            <span className="flex items-center gap-2">
              <MetalMark size={28} />
              <span className="text-lg font-semibold text-accent">MetalMark Money</span>
            </span>
            <span className="font-mono text-xs text-fg-muted">28 · header</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <MetalMark size={48} />
            <span className="font-mono text-xs text-fg-muted">48 · sign-in</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <MetalMark size={16} />
            <span className="font-mono text-xs text-fg-muted">16 · favicon floor</span>
          </div>
        </div>
        <p className="text-xs text-fg-muted">
          It <strong>draws its own field</strong> and is never recoloured. That is not laziness — the
          mark has to stay legible at 16 px in a tab strip, which is why it is a filled tile with a
          teal rim rather than a bare butterfly, and why it looks the same here as in the favicon. On
          the dark surfaces that field sits one step up from the background and the rim carries the
          shape; tinting it to the accent would trade the match for the mark.
        </p>
        <p className="text-xs text-fg-muted">
          Always <strong>decorative</strong>: <code>alt=&quot;&quot;</code> is baked into the
          component, because every call site already puts the word &quot;MetalMark&quot; beside it
          and an alt text would say the name twice (§4.2).
        </p>
      </Section>

      <Section
        title="Account marks"
        note="Computed, never fetched. Which banks a household uses is not something to hand to a favicon service, and asking each institution for its logo is a request that leaks the same thing (ADR-0002, decision G). So the mark is initials from the account's own name plus a hue from the chart tokens: no network, and the same account always produces the same mark, in every session."
      >
        <ul className="divide-y divide-border">
          {DEMO_ACCOUNTS.map((a) => (
            <li key={a.name} className="flex items-center gap-3 py-2">
              <AccountMark name={a.name} institution={a.institution} size="md" />
              <span className="min-w-0 flex-1 truncate text-sm text-fg">{a.name}</span>
              <span className="shrink-0 text-xs text-fg-muted">{a.institution ?? "—"}</span>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-3">
          <AccountMark name="Chase Checking" />
          <AccountMark name="Chase Savings" />
          <AccountMark name="Zzyzx Credit Union" />
          <span className="text-xs text-fg-muted">
            20px in a list row, 24px on a card or a sheet
          </span>
        </div>
        <p className="text-xs text-fg-muted">
          The <strong>colour identifies the institution</strong> for the handful of names people
          actually have (Chase is blue, Capital One is red), and falls back to a hash of the name
          for everything else; the <strong>initials identify the account</strong>, which is what
          separates two accounts at one bank. It is never colour alone (§7.8) — the letters carry
          the mark, and the account name is the accessible name.
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
