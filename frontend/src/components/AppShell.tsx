import { NavLink, useLocation, useNavigationType } from "react-router-dom";
import {
  useEffect,
  useLayoutEffect,
  type ComponentType,
  type ReactNode,
  type SVGProps,
} from "react";
import { useAuth } from "@/auth/AuthContext";
import { useSyncNotices } from "@/api/sync";
import { InstitutionLogosProvider } from "@/components/InstitutionLogos";
import { MetalMark } from "@/components/MetalMark";
import { ThemeButton } from "@/components/ThemeToggle";
import { AdminIcon, ChartIcon, ListIcon, ReviewIcon, SettingsIcon, WalletIcon } from "@/components/icons";

type NavItem = {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** Rendered only for an administrator. See the note on `NAV` below. */
  adminOnly?: boolean;
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
const NAV: NavItem[] = [
  { to: "/accounts", label: "Accounts", Icon: WalletIcon },
  { to: "/transactions", label: "Transactions", Icon: ListIcon },
  { to: "/review", label: "Review", Icon: ReviewIcon },
  { to: "/reports", label: "Reports", Icon: ChartIcon },
  { to: "/settings", label: "Settings", Icon: SettingsIcon },
  { to: "/admin", label: "Admin", Icon: AdminIcon, adminOnly: true },
];

//: How many items the phone tab bar can carry (§4.13). The desktop nav has no
//: such limit; see `NAV`.
const TAB_BAR_MAX = 5;

// Routes that render in the shell but are not destinations in the nav. The
// design system is the only one left: it is a development tool, typed by hand.
const EXTRA_TITLES: Record<string, string> = {
  "/debug/design-system": "Design system",
};

export default function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();
  const navigationType = useNavigationType();

  const isAdmin = me?.user.is_admin === true;
  const items = NAV.filter((n) => !n.adminOnly || isAdmin);

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
  useEffect(() => {
    const here = items.find((n) => n.to === pathname)?.label ?? EXTRA_TITLES[pathname];
    document.title = here ? `${here} · MetalMark Money` : "MetalMark Money";
    // `items` is a new array each render; `isAdmin` is what actually varies.
  }, [pathname, isAdmin]);

  return (
    <div className="flex min-h-full flex-col">
      {/* Sticky, and opaque: on desktop the primary nav lives in here, so
          scrolling a long list used to take navigation away entirely. `z-40`
          matches the phone tab bar; overlays are `z-50` and cover both. The
          `bg-surface` is load-bearing — without it, rows scroll through the
          bar. Focus scroll-margin for this bar is set in index.css. */}
      <header className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex min-w-0 items-center gap-6">
          {/* Mark and wordmark as one unit, so the `gap-6` above stays the space
              between the brand and the nav rather than creeping in between the
              two halves of the brand. At 28 px the mark is the size of an app
              icon on a phone's home row — the header is the one place the mark
              has to survive next to a lot of other things. */}
          <span className="flex min-w-0 items-center gap-2">
            <MetalMark size={28} />
            <span className="text-lg font-semibold text-accent">MetalMark Money</span>
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
          <span className="hidden sm:inline">{me?.household_name}</span>
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
      <main className="mx-auto w-full max-w-7xl flex-1 p-4 lg:p-6">
        <InstitutionLogosProvider>{children}</InstitutionLogosProvider>
      </main>

      {/* Phone navigation. `aria-current` gives the screen reader what
          `font-semibold` gives the eye — the accent hue alone is a colour-only
          signal, which SC 1.4.1 forbids (§4.13). The first five only: this bar
          is where the cap lives, and an admin reaches the panel from
          Settings → Admin on a phone. */}
      <nav
        className="sticky bottom-0 z-40 flex border-t border-border bg-surface-raised pb-[env(safe-area-inset-bottom)] sm:hidden"
        aria-label="Main"
      >
        {items.slice(0, TAB_BAR_MAX).map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            // NavLink supplies `aria-current="page"`; the icon is decorative and
            // carries its own aria-hidden, so the label is the accessible name.
            className={({ isActive }) =>
              `flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-xs ${
                isActive ? "font-semibold text-accent" : "text-fg-muted"
              }`
            }
            data-testid={`tab-${n.label.toLowerCase()}`}
          >
            <n.Icon />
            {n.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
