// Public signup. The first account created on a fresh instance becomes the
// household's owner/admin; later ones join that household as members, so the
// household name is only offered when there is no household to join yet.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { Button, Field, Input } from "@/components/form";

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
        {/* Shared primitives — see the note in Login.tsx: the hand-rolled input
            this replaced was under the 44 px target floor, inherited `text-sm`
            (iOS zooms the viewport on focus and never zooms back), and used
            `focus:` where the spec says `focus-visible:`. */}
        <Field label="Email" htmlFor="signup-email">
          <Input
            id="signup-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            data-testid="signup-email"
          />
        </Field>
        <Field label="Display name" htmlFor="signup-name">
          <Input
            id="signup-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            autoComplete="name"
            data-testid="signup-name"
          />
        </Field>
        <Field label="Password" htmlFor="signup-password">
          <Input
            id="signup-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            data-testid="signup-password"
          />
        </Field>
        <Field
          label="Household name"
          htmlFor="signup-household"
          hint="Optional. Ignored if the household already exists on this instance."
        >
          <Input
            id="signup-household"
            value={household}
            onChange={(e) => setHousehold(e.target.value)}
            data-testid="signup-household"
          />
        </Field>
        {error && (
          <p className="text-sm text-negative" role="alert" data-testid="signup-error">
            <span aria-hidden="true">⚠ </span>
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy} className="w-full" data-testid="signup-submit">
          {busy ? "Creating account…" : "Create account"}
        </Button>
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
