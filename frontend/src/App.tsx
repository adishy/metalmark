import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { QueryError } from "@/components/QueryStates";
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
  const { me, loading, probeError, retryProbe } = useAuth();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-fg-muted" data-testid="loading">
        Loading…
      </div>
    );
  }

  // The session probe failed, and not with a 401 — so this is not "signed out"
  // and the sign-in page would be a lie about it. §4.11's error-plus-retry card,
  // applied to the one read that used to swallow the distinction. Deliberately
  // *not* inside `AppShell`: the shell reads `me` for the nav and the user menu,
  // and there is no user to render. This is the whole screen, below the browser
  // chrome, which is what it should be — you cannot use the app until the server
  // answers, and "Try again" is the only thing on the page that can change that.
  if (probeError) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <QueryError
          what="your session"
          error={probeError}
          onRetry={retryProbe}
          testid="session-unreachable"
        />
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
        {/* The sync control panel. Now a nav destination for administrators —
            the desktop nav has room for a sixth item even though the phone tab
            bar does not (§4.13), and it was reachable only from a muted aside in
            Settings before, which is to say not reachable. The client-side admin
            check is a courtesy that keeps a member from landing on a page of
            403s; every route it calls enforces owner server-side, and that is
            the check that matters. */}
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
