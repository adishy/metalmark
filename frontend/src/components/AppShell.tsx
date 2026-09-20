import { NavLink, useLocation } from "react-router-dom";
import { useEffect, type ComponentType, type ReactNode, type SVGProps } from "react";
import { useAuth } from "@/auth/AuthContext";
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

  const isAdmin = me?.user.is_admin === true;
  const items = NAV.filter((n) => !n.adminOnly || isAdmin);

  // The primary orientation cue for a screen reader and for a phone's tab
  // switcher, and it was the same string on every route (§7.11).
  useEffect(() => {
    const here = items.find((n) => n.to === pathname)?.label ?? EXTRA_TITLES[pathname];
    document.title = here ? `${here} · MetalMark` : "MetalMark";
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
          <span className="text-lg font-semibold text-accent">MetalMark</span>
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

      <main className="mx-auto w-full max-w-4xl flex-1 p-4">{children}</main>

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
