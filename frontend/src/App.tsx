import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import AppShell from "@/components/AppShell";
import Accounts from "@/pages/Accounts";
import Transactions from "@/pages/Transactions";
import Review from "@/pages/Review";
import Reports from "@/pages/Reports";
import Settings from "@/pages/Settings";
import Admin from "@/pages/Admin";
import DesignSystem from "@/pages/DesignSystem";

export default function App() {
  const { me, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-fg-muted" data-testid="loading">
        Loading…
      </div>
    );
  }

  if (!me) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/accounts" replace />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/transactions" element={<Transactions />} />
        <Route path="/review" element={<Review />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/settings" element={<Settings />} />
        {/* The sync control panel. Absent from the nav for the same reason the
            design system below is — DESIGN.md §4.13 caps the tab bar at five —
            but it is a real destination, reached from Settings → Connections.
            The client-side admin check is a courtesy that keeps a member from
            landing on a page of 403s; every route it calls enforces owner
            server-side, and that is the check that matters. */}
        <Route
          path="/admin"
          element={me.user.is_admin ? <Admin /> : <Navigate to="/accounts" replace />}
        />
        {/* Reference page for the design system (docs/DESIGN.md). Deliberately
            absent from the nav — it is a development tool, not a destination. */}
        <Route path="/debug/design-system" element={<DesignSystem />} />
        <Route path="*" element={<Navigate to="/accounts" replace />} />
      </Routes>
    </AppShell>
  );
}
