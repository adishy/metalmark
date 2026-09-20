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
        {/* Reference page for the design system (docs/DESIGN.md). Deliberately
            absent from the nav — it is a development tool, not a destination. */}
        <Route path="/debug/design-system" element={<DesignSystem />} />
        <Route path="*" element={<Navigate to="/accounts" replace />} />
      </Routes>
    </AppShell>
  );
}
