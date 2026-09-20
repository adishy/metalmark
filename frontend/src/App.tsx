import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import Login from "@/pages/Login";
import AppShell from "@/components/AppShell";
import Accounts from "@/pages/Accounts";
import Transactions from "@/pages/Transactions";
import Review from "@/pages/Review";
import Reports from "@/pages/Reports";

export default function App() {
  const { me, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-slate-400" data-testid="loading">
        Loading…
      </div>
    );
  }

  if (!me) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
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
        <Route path="*" element={<Navigate to="/accounts" replace />} />
      </Routes>
    </AppShell>
  );
}
