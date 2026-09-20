import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "@/auth/AuthContext";

const NAV = [
  { to: "/accounts", label: "Accounts" },
  { to: "/transactions", label: "Transactions" },
  { to: "/review", label: "Review" },
  { to: "/reports", label: "Reports" },
  { to: "/settings", label: "Settings" },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-6">
          <span className="text-lg font-semibold text-brand">metalmark</span>
          <nav className="hidden gap-1 sm:flex">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm ${
                    isActive ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"
                  }`
                }
                data-testid={`nav-${n.label.toLowerCase()}`}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <span className="hidden sm:inline">{me?.household_name}</span>
          <button onClick={() => logout()} className="hover:text-white" data-testid="logout">
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 p-4">{children}</main>

      {/* Mobile bottom nav */}
      <nav className="flex border-t border-slate-800 sm:hidden">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              `flex-1 py-3 text-center text-xs ${isActive ? "text-brand" : "text-slate-500"}`
            }
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
