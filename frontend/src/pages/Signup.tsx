// Public signup. The first account created on a fresh instance becomes the
// household's owner/admin; later ones join that household as members, so the
// household name is only offered when there is no household to join yet.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [household, setHousehold] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signup({
        email,
        display_name: displayName,
        password,
        household_name: household.trim() || undefined,
      });
      navigate("/accounts", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign up failed");
    } finally {
      setBusy(false);
    }
  };

  const input =
    "mt-1 w-full rounded-control border border-border-strong bg-surface-inset px-3 py-2 outline-none focus:border-accent";

  return (
    <div className="flex h-full items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-card bg-surface-raised p-8 shadow-xl"
        data-testid="signup-form"
      >
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-accent">MetalMark</h1>
          <p className="text-sm text-fg-muted">Create your account</p>
        </div>
        <label className="block text-sm">
          <span className="text-fg-muted">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={input}
            autoComplete="username"
            data-testid="signup-email"
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg-muted">Display name</span>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className={input}
            autoComplete="name"
            data-testid="signup-name"
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg-muted">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={input}
            autoComplete="new-password"
            data-testid="signup-password"
          />
        </label>
        <label className="block text-sm">
          <span className="text-fg-muted">Household name</span>
          <input
            value={household}
            onChange={(e) => setHousehold(e.target.value)}
            className={input}
            data-testid="signup-household"
          />
          <span className="mt-1 block text-xs text-fg-muted">
            Optional. Ignored if the household already exists on this instance.
          </span>
        </label>
        {error && (
          <p className="text-sm text-negative" data-testid="signup-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-control bg-accent py-2 font-medium text-accent-fg hover:brightness-110 disabled:opacity-50"
          data-testid="signup-submit"
        >
          {busy ? "Creating account…" : "Create account"}
        </button>
        <p className="text-center text-xs text-fg-muted">
          Already have an account?{" "}
          <Link to="/login" className="text-accent underline" data-testid="signup-to-login">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
