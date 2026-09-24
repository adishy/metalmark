import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { Button, Field, Input, Spinner } from "@/components/form";
import { MetalMark } from "@/components/MetalMark";

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
      {/* Marked up the way password managers look for a sign-in form (web.dev
          "Sign-in form best practices"; Chromium's password form guidance): a
          real <form> with method="post" and an action, a username field that
          says `autocomplete="username"` and a password field that says
          `current-password`, each with a stable `id` and `name` and a <label>,
          and a real submit button. The submit handler still does the work — the
          action is only what the form claims to be, which is what a manager
          reads when it decides to offer to fill or save. */}
      <form
        onSubmit={submit}
        method="post"
        action="/api/auth/login"
        className="w-full max-w-sm space-y-4 rounded-card bg-surface-raised p-8"
        data-testid="login-form"
      >
        {/* The one screen with room for the mark at a size that shows what it
            is — the header runs it at 28 px, where it is a dot with a butterfly
            in it. 48 px is §2.6's next step up from the avatar sizes, and it is
            the first thing on the page, which is what a sign-in screen wants:
            the brand before the form. */}
        <div className="flex flex-col items-center gap-2 text-center">
          <MetalMark size={48} />
          <h1 className="text-2xl font-semibold text-accent">MetalMark Money</h1>
          <p className="text-sm text-fg-muted">Sign in to your household</p>
        </div>
        {/* The shared primitives, not hand-rolled inputs. The inline version
            these replaced was `px-3 py-2` (40 px, under the 44 px floor),
            inherited `text-sm` from its wrapper (iOS Safari zooms the viewport
            on a control under 16 px and never zooms back), and used
            `focus:border-accent` where the spec says `focus-visible`. */}
        <Field label="Email" htmlFor="login-email">
          <Input
            id="login-email"
            name="username"
            type="email"
            inputMode="email"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            data-testid="email"
          />
        </Field>
        <Field label="Password" htmlFor="login-password">
          <Input
            id="login-password"
            name="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            data-testid="password"
          />
        </Field>
        {/* Form-level, not field-level: the credentials are wrong, not the
            email. role="alert" so it is announced without moving focus. */}
        {error && (
          <p className="text-sm text-negative" role="alert" data-testid="login-error">
            <span aria-hidden="true">⚠ </span>
            {error}
          </p>
        )}
        {/* Keeps its label while working (§4.1). "Signing in…" replaced the
            label, which made the button's own name change underneath the user
            and put the status where nothing announces it. */}
        <Button
          type="submit"
          disabled={busy}
          aria-busy={busy}
          className="w-full"
          data-testid="login-submit"
        >
          {busy && <Spinner />}
          Sign in
        </Button>
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
