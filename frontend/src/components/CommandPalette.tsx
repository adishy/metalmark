// The command palette (issue #35): every destination in the app, reachable by
// typing. Desktop-only — `AppShell` mounts it (lazily) only at `lg:`, which is
// where §9 puts the desktop, and a phone has no Cmd key and no room for a
// 512 px panel of links it already has five of.
//
// **Why `Dialog` and not a fourth overlay.** §4.8's component already carries
// the four things an overlay must not get wrong — focus moves in and returns to
// where it was, focus is trapped, `<body>` does not scroll behind it, Escape
// closes it — and the task's rule is to reuse it rather than write another. Of
// its four presentations this is the **centred modal** (`side={false}`,
// `inline={false}`, the default): a slide-over is for a detail *beside* its
// list (§9.3), an inline panel is not an overlay at all (no trap, no Escape, no
// scroll lock — see Dialog.tsx), and a bottom sheet is a `<640px` thumb
// affordance which §9.6 forbids at `lg:` outright. The centred modal is the
// only one that is still correct where this is allowed to open, and because it
// is never mounted below `lg:` the sheet branch is unreachable rather than
// merely unused.
//
// **The one thing it does not give us** is where focus lands: `Dialog` focuses
// the first focusable in the panel, which is its own close button, and a palette
// that opens with the keyboard outside its field is not a palette. The refocus
// below runs *after* Dialog's effect (React runs a child's effects first), so it
// wins without Dialog having to know about it.
//
// **The destination lists are imported, never re-typed.** `nav` arrives as a
// prop already filtered by `AppShell` (the same `me.user.is_admin` expression
// that decides the nav item and the tab bar), and the two tab tables come from
// the pages that own them. A palette with its own copy of the six sections is a
// palette that goes stale the first time a destination is renamed.
import {
  Fragment,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent,
  type SVGProps,
} from "react";
import { useNavigate } from "react-router-dom";
import Dialog from "@/components/Dialog";
import type { NavItem } from "@/components/AppShell";
import { TABS as INSIGHTS_TABS } from "@/pages/Insights";
import { TABS as SETTINGS_TABS } from "@/pages/Settings";

/** One row of the palette. `id` is stable and unique — it is the option's DOM id
 *  and the `data-testid` suffix. */
type Item = {
  id: string;
  label: string;
  /** The visual heading, and part of what the filter matches. */
  group: string;
  to: string;
  Icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** The "g then x" chord, for the six nav sections that have one. */
  chord?: string;
};

const optionId = (item: Item) => `palette-option-${item.id}`;

export default function CommandPalette({
  nav,
  isAdmin,
  onClose,
}: {
  /** `AppShell`'s already-admin-filtered nav list. */
  nav: NavItem[];
  /** The same flag that filtered `nav` — Settings declares its own admin-only
   *  section, and the filter has to be the nav's rule rather than a second one. */
  isAdmin: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const baseId = useId();
  const labelId = `${baseId}-label`;
  const listId = `${baseId}-list`;

  const items = useMemo(() => {
    // "Pages", not "sections": in this app a *section* is one of Settings' or
    // Insights' tabs (that is what both tab strips call themselves), and the nav
    // is the pages above them. The placeholder promises the same three words.
    const out: Item[] = nav.map((n) => ({
      id: `nav-${n.to.replace(/\W+/g, "")}`,
      label: n.label,
      group: "Pages",
      to: n.to,
      Icon: n.Icon,
      chord: n.chord,
    }));
    // `/insights/:tab` is a real route, so these are ordinary destinations.
    for (const t of INSIGHTS_TABS) {
      out.push({ id: `insights-${t.id}`, label: t.label, group: "Insights", to: `/insights/${t.id}` });
    }
    // Settings' panel is not a route of its own; it is addressable through
    // `?tab=`, which is what lets eleven sections be destinations instead of
    // eleven entries that all land on Categories.
    for (const t of SETTINGS_TABS) {
      if ("adminOnly" in t && !isAdmin) continue;
      out.push({ id: `settings-${t.id}`, label: t.label, group: "Settings", to: `/settings?tab=${t.id}` });
    }
    // `/debug/design-system` is deliberately absent, and that is not an
    // oversight: it is outside the nav because it is a development tool rather
    // than a destination (`AppShell`'s `EXTRA_TITLES`), and the palette is a
    // list of destinations.
    return out;
  }, [nav, isAdmin]);

  // Every whitespace-separated token has to match, anywhere in the row's label or
  // its heading — so "settings owner" finds Owners, and "owner" alone does too.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    const tokens = q.split(/\s+/);
    return items.filter((it) => {
      const hay = `${it.label} ${it.group}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [items, query]);

  // The field, not Dialog's close button. See the note at the top of the file.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Clamped rather than reset: the list shrinks under the highlight as the query
  // narrows, and an index that outlived its row would leave Enter with nothing
  // to activate while the user can still see a highlighted row.
  const activeIndex = matches.length === 0 ? -1 : Math.min(active, matches.length - 1);
  const activeItem = activeIndex === -1 ? undefined : matches[activeIndex];

  // Arrow keys move a highlight inside a scrolling panel, so the panel has to
  // follow it. `block: "nearest"` scrolls the list and not the page; the page
  // behind is `overflow: hidden` anyway. Optional call: jsdom has no
  // `scrollIntoView`, and a test that throws here would be testing the stub.
  useEffect(() => {
    if (!activeItem) return;
    document.getElementById(optionId(activeItem))?.scrollIntoView?.({ block: "nearest" });
  }, [activeItem]);

  function go(item: Item) {
    onClose();
    navigate(item.to);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    // Escape and Tab belong to `Dialog`, which owns the trap and the close.
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(matches.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeItem) go(activeItem);
    }
  }

  // Rendered as it goes, so a heading is emitted whenever the run of results
  // changes group. Headings are `presentation` and `aria-hidden`: the options
  // are the listbox's content, and a visual grouping is not worth a second
  // announcement of every row.
  let lastGroup: string | undefined;

  return (
    <Dialog open onClose={onClose} title="Command palette" testid="command-palette">
      <div className="flex flex-col">
        <label htmlFor={`${baseId}-input`} id={labelId} className="text-sm font-medium text-fg">
          Search destinations
        </label>
        {/* The combobox/listbox half of the pattern Dialog cannot supply. Focus
            stays in the field the whole time — the highlight is
            `aria-activedescendant`, so arrow keys are not a second tab order. */}
        <input
          ref={inputRef}
          id={`${baseId}-input`}
          type="search"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeItem ? optionId(activeItem) : undefined}
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Type a section, a tab, or a page"
          className="mt-1 w-full min-h-11 rounded-control border border-border-strong bg-surface-inset px-3 text-base text-fg placeholder:text-fg-muted focus-visible:border-accent"
          data-testid="palette-input"
        />

        {/* The result count, in the app's existing live-region shape (§7.6): the
            whole sentence, `aria-atomic`, so a screen-reader user typing into a
            filtered list is told what survived the filter rather than nothing at
            all. One element for both states — the empty state *is* the count
            being zero, and two live regions would say it twice. */}
        <p
          role="status"
          aria-atomic="true"
          className="mt-2 px-1 text-xs text-fg-muted"
          data-testid="palette-status"
        >
          {matches.length === 0
            ? `No destination matches “${query.trim()}”.`
            : `${matches.length} ${matches.length === 1 ? "destination" : "destinations"}`}
        </p>

        <ul id={listId} role="listbox" aria-label="Destinations" className="mt-1">
          {matches.map((it, i) => {
            const heading = it.group === lastGroup ? null : it.group;
            lastGroup = it.group;
            const selected = i === activeIndex;
            return (
              <Fragment key={it.id}>
                {heading && (
                  <li
                    role="presentation"
                    aria-hidden="true"
                    className="px-1 pb-1 pt-3 text-xs font-medium text-fg-muted"
                  >
                    {heading}
                  </li>
                )}
                <li
                  id={optionId(it)}
                  role="option"
                  aria-selected={selected}
                  // Clickable but not focusable: this is the combobox pattern's
                  // own shape, where the field keeps focus and the highlight is
                  // `aria-activedescendant`. A row that took focus would make the
                  // keyboard a second, competing way to move down the list.
                  onMouseMove={() => setActive(i)}
                  onClick={() => go(it)}
                  className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-control px-3 text-sm text-fg ${
                    selected ? "bg-surface-inset font-semibold" : "hover:bg-surface-inset"
                  }`}
                  data-testid={`palette-option-${it.id}`}
                >
                  {it.Icon && <it.Icon aria-hidden="true" className="size-5 shrink-0 text-fg-muted" />}
                  <span className="min-w-0 flex-1">{it.label}</span>
                  {it.chord && (
                    <kbd
                      aria-hidden="true"
                      className="shrink-0 rounded border border-border px-1.5 py-0.5 text-xs text-fg-muted"
                    >
                      g {it.chord}
                    </kbd>
                  )}
                </li>
              </Fragment>
            );
          })}
        </ul>
      </div>
    </Dialog>
  );
}
