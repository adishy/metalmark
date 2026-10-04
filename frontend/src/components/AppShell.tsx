import { NavLink, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
  type SVGProps,
} from "react";
import { useAuth } from "@/auth/AuthContext";
import { useSyncNotices } from "@/api/sync";
import { InstitutionLogosProvider } from "@/components/InstitutionLogos";
import { MetalMark } from "@/components/MetalMark";
import { ThemeButton } from "@/components/ThemeToggle";
import { useIsDesktop } from "@/lib/media";
import { AdminIcon, ChartIcon, ListIcon, ReviewIcon, SearchIcon, SettingsIcon, WalletIcon } from "@/components/icons";

// Loaded the first time the palette opens, and never on a phone — see the
// `isDesktop` gate where it is rendered. The app has no other code split, so
// this is the one chunk a phone does not have to download to draw a page it
// cannot open a palette from.
const CommandPalette = lazy(() => import("@/components/CommandPalette"));

export type NavItem = {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** Rendered only for an administrator. See the note on `NAV` below. */
  adminOnly?: boolean;
  /**
   * The key of this destination's "g then x" chord, if it has one. Declared here
   * rather than in a second table beside the listener, so a chord cannot name a
   * route that is not a destination.
   */
  chord?: string;
};

// One list drives the desktop nav, the phone tab bar, and the document title, so
// a new route cannot end up in one and missing from another.
//
// The five-item cap is the **bottom tab bar's** (§4.13) — it is a phone control,
// and the cap exists because a sixth target stops being thumb-reachable. The
// desktop nav is a row of text in a header with room to spare, so it renders
// everything and the tab bar takes the first five.
//
// That distinction is what makes the sixth item possible, and it needs to be
// possible: this panel shipped complete — queue, runs, logs, pause, cancel, all
// tested and green in CI — and the person who asked for it could not find it.
// The only link to it in the whole app was a muted aside in the fifth of eight
// Settings tabs, and the word "Admin" appeared nowhere in the UI. A destination
// nothing points at is not a shipped feature.
export const NAV: NavItem[] = [
  { to: "/accounts", label: "Accounts", Icon: WalletIcon, chord: "a" },
  { to: "/transactions", label: "Transactions", Icon: ListIcon, chord: "t" },
  { to: "/review", label: "Review", Icon: ReviewIcon, chord: "r" },
  { to: "/insights", label: "Insights", Icon: ChartIcon, chord: "i" },
  { to: "/settings", label: "Settings", Icon: SettingsIcon, chord: "s" },
  // `d`, not `a`: Accounts took that one, and a chord that is not the label's
  // first letter is still better than a chord that is ambiguous. The palette
  // prints each chord beside its section, so this is read rather than guessed.
  { to: "/admin", label: "Admin", Icon: AdminIcon, adminOnly: true, chord: "d" },
];

/** How long a "g" stays armed, in ms. GitHub's own chords use about this; long
 *  enough to be deliberate, short enough that a stray "g" does not make the
 *  *next* keypress you type navigate somewhere. */
const CHORD_WINDOW_MS = 1500;

//: How many items the phone tab bar can carry (§4.13). The desktop nav has no
//: such limit; see `NAV`.
const TAB_BAR_MAX = 5;

// Routes that render in the shell but are not destinations in the nav. The
// design system is the only one left: it is a development tool, typed by hand.
const EXTRA_TITLES: Record<string, string> = {
  "/debug/design-system": "Design system",
};

/** The modifier printed on the key, not a preference: ⌘ on an Apple keyboard,
 *  Ctrl everywhere else. Read once — a keyboard does not change under a session. */
const MOD = /mac|iphone|ipad|ipod/i.test(
  typeof navigator === "undefined" ? "" : (navigator.platform ?? ""),
)
  ? "⌘"
  : "Ctrl";

export default function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const navigationType = useNavigationType();

  const isAdmin = me?.user.is_admin === true;
  const items = NAV.filter((n) => !n.adminOnly || isAdmin);

  /*
   * The command palette (issue #35), and its "g then x" chords.
   *
   * Both are **desktop-only**, and that is a behaviour gate rather than a
   * `lg:` class, which is the one thing `lib/media.ts` exists for: a chord is
   * meaningless on a phone, and a phone has no Cmd key to press. §9's "one
   * tree, never two branching on viewport" is a rule about *layout* — there is
   * still exactly one palette component here, mounted or not — and the visible
   * half of it is a class (`hidden … lg:inline-flex`), so no viewport ever
   * renders a second copy of anything.
   */
  const isDesktop = useIsDesktop();
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Keyed on `isAdmin` rather than on the `items` array, which is rebuilt every
  // render: a re-render between the "g" and the letter would otherwise tear the
  // listener down mid-chord and swallow it.
  const chords = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of NAV) if (n.chord && (!n.adminOnly || isAdmin)) m.set(n.chord, n.to);
    return m;
  }, [isAdmin]);

  // Cmd/Ctrl+K. Chrome and Safari open their own search on that key, so this one
  // has to claim it rather than merely notice it.
  //
  // `useLayoutEffect`, unlike the chord listener below, and the difference is
  // the few milliseconds after login: a passive effect is *scheduled*, so there
  // is a window between the shell painting its nav and anything listening, and a
  // key pressed inside it is simply lost. Layout effects flush in the commit,
  // before the frame the user could have typed in. (Found by the e2e spec, which
  // pressed Cmd+K the moment the nav appeared and watched the palette not open.)
  useLayoutEffect(() => {
    if (!isDesktop) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat) return;
      if (e.key.toLowerCase() !== "k" || !(e.metaKey || e.ctrlKey)) return;
      if (e.altKey || e.shiftKey) return; // Cmd+Shift+K is somebody else's
      e.preventDefault();
      setPaletteOpen((open) => !open);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isDesktop]);

  // "g then x". The gate is `Review.tsx`'s, for the same reason: a plain letter
  // is a letter first, and nothing here may eat a keystroke aimed at a field.
  // (A screen reader in browse mode keeps its own single-key navigation: these
  // only fire when the page itself has the keyboard.)
  const chordArmed = useRef(false);
  const chordTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!isDesktop) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || e.defaultPrevented) return;
      const el = e.target as HTMLElement | null;
      if (el?.isContentEditable || (el && /^(input|textarea|select)$/i.test(el.tagName))) return;
      if (paletteOpen) return; // the palette owns the keyboard while it is open
      const key = e.key.toLowerCase();
      if (!chordArmed.current) {
        if (key !== "g") return;
        chordArmed.current = true;
        window.clearTimeout(chordTimer.current);
        chordTimer.current = window.setTimeout(() => {
          chordArmed.current = false;
        }, CHORD_WINDOW_MS);
        return;
      }
      chordArmed.current = false;
      window.clearTimeout(chordTimer.current);
      const to = chords.get(key);
      if (!to) return;
      e.preventDefault();
      navigate(to);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.clearTimeout(chordTimer.current);
    };
  }, [isDesktop, chords, navigate, paletteOpen]);

  // The notice feed (ADR-0037). Rendered by nothing — it is a poll and an
  // effect — and here rather than in a page because the shell is the only thing
  // that is mounted on every route. A tab sitting on Reports when a bank
  // connection breaks is exactly the tab this feature is for; putting the poll
  // in Admin would make it work only for someone already looking at the answer.
  useSyncNotices();

  // A client-side route change keeps the scroll position of the page you left,
  // and nothing else here resets it — the browser only restores scroll for a
  // *document* navigation, and a `BrowserRouter` click is not one. So: scroll
  // the ledger to the bottom, tap Review, and you land 400 px into a page you
  // have not seen, with its heading and its queue count above the fold. The
  // sticky header is what hid it — navigation stays where you left it, so the
  // only thing that looks wrong is the content.
  //
  // PUSH and REPLACE only — but the exemption is not what Back rides on, and it
  // would be wrong to say it is. Chromium restores a POP entry's scroll
  // position *after* this layout effect, within the same frame, so removing the
  // check changes nothing there: measured, both ways, and the shell spec passes
  // either way. It is here so Back is correct by construction rather than by
  // engine ordering — the suite runs one engine, and the alternative is a
  // `scrollRestoration = "manual"` or a different browser making Back top every
  // page with no test to notice.
  //
  // React Router has no `<ScrollRestoration>` outside a data router; this is
  // the whole of what that would do here.
  //
  // `useLayoutEffect`, not `useEffect`: this has to land before the browser
  // paints the new route, or the new page is drawn at the old offset for a frame
  // and then jumps. (Client-rendered app; there is no server pass for the SSR
  // warning to be about.)
  useLayoutEffect(() => {
    if (navigationType === "POP") return;
    window.scrollTo(0, 0);
  }, [pathname, navigationType]);

  // The primary orientation cue for a screen reader and for a phone's tab
  // switcher, and it was the same string on every route (§7.11).
  //
  // Prefix match, not exact: Insights is the first destination with sub-routes
  // (`/insights/overview`, `/insights/allocations`, …) and every one of them
  // is still "Insights" — the tab within it isn't a separate document title,
  // the same way Settings' eleven tabs (all under the one route `/settings`)
  // never were.
  useEffect(() => {
    const here =
      items.find((n) => n.to === pathname || pathname.startsWith(`${n.to}/`))?.label ??
      EXTRA_TITLES[pathname];
    document.title = here ? `${here} · MetalMark Money` : "MetalMark Money";
    // `items` is a new array each render; `isAdmin` is what actually varies.
  }, [pathname, isAdmin]);

  return (
    <div className="flex min-h-full flex-col">
      {/* Sticky, and opaque: on desktop the primary nav lives in here, so
          scrolling a long list used to take navigation away entirely. `z-40`
          matches the phone tab bar; overlays are `z-50` and cover both. The
          `bg-surface` is load-bearing — without it, rows scroll through the
          bar. Focus scroll-margin for this bar is set in index.css.

          Stays `sticky` rather than `fixed` (unlike the tab bar below): it is
          the first item in this flex column, so `sticky top-0` pins from the
          very first frame with no catch-up and no flow it can fall out of.
          `overscroll-behavior: none` (index.css) is what stops the home-screen
          app's rubber-band from dragging it, and that applies to a sticky
          header exactly as well as a fixed one — going `fixed` here would
          only add the cost of reserving its height in `main` manually. */}
      <header className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex min-w-0 items-center gap-3 xl:gap-6">
          {/* Mark and wordmark as one unit, so the `gap-6` above stays the space
              between the brand and the nav rather than creeping in between the
              two halves of the brand. At 28 px the mark is the size of an app
              icon on a phone's home row — the header is the one place the mark
              has to survive next to a lot of other things. */}
          <span className="flex min-w-0 items-center gap-2">
            <MetalMark size={28} />
            <span className="text-lg font-semibold text-accent lg:whitespace-nowrap">MetalMark Money</span>
          </span>
          <nav className="hidden gap-1 sm:flex" aria-label="Main">
            {items.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                // NavLink sets `aria-current="page"` itself when active, so it is
                // not passed here. (Passing it would also work — NavLink
                // destructures the prop out and recomputes it — but it is the
                // same fact stated twice.)
                className={({ isActive }) =>
                  `inline-flex min-h-11 items-center rounded-control px-3 text-sm ${
                    isActive
                      ? "bg-surface-inset font-semibold text-fg"
                      : "text-fg-muted hover:bg-surface-inset hover:text-fg"
                  }`
                }
                data-testid={`nav-${n.label.toLowerCase()}`}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex shrink-0 items-center gap-1 text-sm text-fg-muted">
          {/* The palette's visible entry point. Cmd+K is faster once it is known,
              and a shortcut nobody has been told about is not a feature — the
              same lesson the admin panel taught (`NAV` above): the panel shipped
              complete and could not be found because nothing pointed at it. This
              is the only thing in the app that says the shortcut exists, so it
              prints the key it is asking you to press. `hidden … lg:inline-flex`
              is the phone half of "desktop-only": `display: none` takes it out of
              the tab order and the accessibility tree too, so a phone has no way
              to reach it — not just no room for it. */}
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-keyshortcuts="Meta+K Control+K"
            className="hidden min-h-11 min-w-0 items-center gap-2 rounded-control border border-border-strong bg-surface-inset px-3 text-sm text-fg-muted hover:text-fg lg:inline-flex lg:w-40 xl:w-64"
            data-testid="palette-open"
          >
            <SearchIcon aria-hidden="true" className="size-4" />
            <span className="flex-1 text-left">Search</span>
            {/* Decorative: `aria-keyshortcuts` is what tells assistive tech the
                shortcut, and "Search ⌘K" read aloud is not a name. */}
            <kbd aria-hidden="true" className="inline-flex shrink-0 items-center rounded border border-border px-1.5 py-0.5 font-sans text-xs leading-4">
              {MOD}K
            </kbd>
          </button>
          <ThemeButton />
          <button
            type="button"
            onClick={() => logout()}
            className="inline-flex min-h-11 items-center rounded-control px-3 hover:bg-surface-inset hover:text-fg"
            data-testid="logout"
          >
            Sign out
          </button>
        </div>
      </header>

      {/* `max-w-7xl` is the **widest** case, not every case (§9.1): the shell
          gives the room to the page that can use all of it — a ledger list, a
          table, the Admin counters — and a page whose content is a form or a
          single figure narrows itself with its own `mx-auto max-w-*`. The
          alternative, the shell asking each page what width it wants, puts the
          decision in one place and the number in twenty.

          It was `max-w-4xl` (896 px) for every page at every viewport, which is
          272 px of dead margin per side at 1440 and 512 px at 1920. §2.5 has
          promised `lg:p-6` since the token layer landed and the shell never took
          it; this is that gutter arriving, one breakpoint up where the extra
          8 px is not worth the content it costs at 360. */}
      {/* `fixed` bars are out of flow, so `main` has to reserve their space
          itself. The tab bar is phone-only (`sm:hidden` below) and `min-h-16`
          (4rem) plus the home-indicator inset; `sm:pb-4`/`lg:p-6` restore the
          plain padding once the bar is gone. See the tab bar's own comment
          for why it is `fixed` rather than `sticky`. */}
      <main className="mx-auto w-full max-w-7xl flex-1 p-4 pb-[calc(4rem+env(safe-area-inset-bottom)+1rem)] sm:pb-4 lg:p-6">
        <InstitutionLogosProvider>{children}</InstitutionLogosProvider>
      </main>

      {/* Phone navigation. `aria-current` gives the screen reader what
          `font-semibold` gives the eye — the accent hue alone is a colour-only
          signal, which SC 1.4.1 forbids (§4.13). The first five only: this bar
          is where the cap lives, and an admin reaches the panel from
          Settings → Admin on a phone.

          `fixed`, not `sticky`: this sits at the end of a flex column inside
          `min-h-full`, and `position: sticky` on the last flex item is where
          iOS Safari's sticky-bottom support is weakest — it can fail to pin
          at all, and in the standalone home-screen app a rubber-band
          overscroll can drag it off the bottom edge (`overscroll-behavior`
          above is the primary fix for that; `fixed` means the bar does not
          depend on it). `fixed` anchors it to the viewport unconditionally,
          which is also what "only content scrolls" means literally. */}
      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-surface-raised pb-[env(safe-area-inset-bottom)] sm:hidden"
        aria-label="Main"
      >
        {items.slice(0, TAB_BAR_MAX).map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            // NavLink supplies `aria-current="page"`; the icon is decorative and
            // carries its own aria-hidden, so the label is the accessible name.
            // 64 px tall and a fifth of the width each: the whole cell is the
            // target, not the icon. The active tab's icon sits in a pill — a
            // shape change, so the current tab is never colour alone (§7).
            className={({ isActive }) =>
              `group flex min-h-16 flex-1 flex-col items-center justify-center gap-1 pt-2 pb-1.5 text-xs ${
                isActive ? "font-semibold text-accent-ink" : "text-fg-muted"
              }`
            }
            data-testid={`tab-${n.label.toLowerCase()}`}
          >
            {({ isActive }) => (
              <>
                <span
                  className={`inline-flex h-8 w-14 items-center justify-center rounded-full ${
                    isActive ? "bg-accent/15" : ""
                  }`}
                >
                  <n.Icon className="size-6 shrink-0" />
                </span>
                {n.label}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* The palette itself, mounted only while it is open and only on a desktop
          — which is also what keeps its chunk off a phone (the lazy import
          above). `items` rather than `NAV`: the admin filter is applied once,
          where the nav applies it, so the palette cannot show a member a
          destination the header does not. `isAdmin` rides along for Settings,
          which declares its own admin-only section. */}
      {isDesktop && paletteOpen && (
        <Suspense fallback={null}>
          <CommandPalette nav={items} isAdmin={isAdmin} onClose={() => setPaletteOpen(false)} />
        </Suspense>
      )}
    </div>
  );
}
