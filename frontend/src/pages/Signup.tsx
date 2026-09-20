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
    "mt-1 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 outline-none focus:border-brand";

  return (
    <div className="flex h-full items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-2xl bg-slate-900 p-8 shadow-xl"
        data-testid="signup-form"
      >
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-brand">MetalMark</h1>
          <p className="text-sm text-slate-400">Create your account</p>
        </div>
        <label className="block text-sm">
          <span className="text-slate-400">Email</span>
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
          <span className="text-slate-400">Display name</span>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className={input}
            autoComplete="name"
            data-testid="signup-name"
          />
        </label>
        <label className="block text-sm">
          <span className="text-slate-400">Password</span>
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
          <span className="text-slate-400">Household name</span>
          <input
            value={household}
            onChange={(e) => setHousehold(e.target.value)}
            className={input}
            data-testid="signup-household"
          />
          <span className="mt-1 block text-xs text-slate-500">
            Optional. Ignored if the household already exists on this instance.
          </span>
        </label>
        {error && (
          <p className="text-sm text-red-400" data-testid="signup-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-brand py-2 font-medium text-slate-950 hover:brightness-110 disabled:opacity-50"
          data-testid="signup-submit"
        >
          {busy ? "Creating account…" : "Create account"}
        </button>
        <p className="text-center text-xs text-slate-400">
          Already have an account?{" "}
          <Link to="/login" className="text-brand underline" data-testid="signup-to-login">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
