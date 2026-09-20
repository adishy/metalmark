import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/accounts", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-card bg-surface-raised p-8 shadow-xl"
        data-testid="login-form"
      >
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-accent">MetalMark</h1>
          <p className="text-sm text-fg-muted">Sign in to your household</p>
        </div>
        <label className="block text-sm">
          <span className="text-fg-muted">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-control border border-border-strong bg-surface-inset px-3 py-2 outline-none focus:border-accent"
            autoComplete="username"
            data-testid="email"
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg-muted">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-control border border-border-strong bg-surface-inset px-3 py-2 outline-none focus:border-accent"
            autoComplete="current-password"
            data-testid="password"
          />
        </label>
        {error && (
          <p className="text-sm text-negative" data-testid="login-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-control bg-accent py-2 font-medium text-accent-fg hover:brightness-110 disabled:opacity-50"
          data-testid="login-submit"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-center text-xs text-fg-muted">
          No account yet?{" "}
          <Link to="/signup" className="text-accent underline" data-testid="login-to-signup">
            Create an account
          </Link>
        </p>
      </form>
    </div>
  );
}
