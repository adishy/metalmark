import { NavLink, useLocation } from "react-router-dom";
import { useEffect, type ComponentType, type ReactNode, type SVGProps } from "react";
import { useAuth } from "@/auth/AuthContext";
import { ThemeButton } from "@/components/ThemeToggle";
import { ChartIcon, ListIcon, ReviewIcon, SettingsIcon, WalletIcon } from "@/components/icons";

type NavItem = {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
};

// Five items, the maximum for a bottom tab bar (§4.13). One list drives the
// desktop nav, the phone tab bar, and the document title, so a new route cannot
// end up in one and missing from another.
const NAV: NavItem[] = [
  { to: "/accounts", label: "Accounts", Icon: WalletIcon },
  { to: "/transactions", label: "Transactions", Icon: ListIcon },
  { to: "/review", label: "Review", Icon: ReviewIcon },
  { to: "/reports", label: "Reports", Icon: ChartIcon },
  { to: "/settings", label: "Settings", Icon: SettingsIcon },
];

// Routes that render in the shell but are not destinations in the nav.
const EXTRA_TITLES: Record<string, string> = {
  "/debug/design-system": "Design system",
};

export default function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const { pathname } = useLocation();

  // The primary orientation cue for a screen reader and for a phone's tab
  // switcher, and it was the same string on every route (§7.11).
  useEffect(() => {
    const here = NAV.find((n) => n.to === pathname)?.label ?? EXTRA_TITLES[pathname];
    document.title = here ? `${here} · MetalMark` : "MetalMark";
  }, [pathname]);

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2">
        <div className="flex min-w-0 items-center gap-6">
          <span className="text-lg font-semibold text-accent">MetalMark</span>
          <nav className="hidden gap-1 sm:flex" aria-label="Main">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                // NavLink sets `aria-current="page"` itself when active; do not
                // pass the attribute here, or it overrides that.
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
          signal, which SC 1.4.1 forbids (§4.13). */}
      <nav
        className="sticky bottom-0 z-40 flex border-t border-border bg-surface-raised pb-[env(safe-area-inset-bottom)] sm:hidden"
        aria-label="Main"
      >
        {NAV.map((n) => (
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
