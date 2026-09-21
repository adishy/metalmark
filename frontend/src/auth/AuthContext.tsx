import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken } from "@/api/client";
import { useSignup } from "@/api/hooks";
import type { Me, SignupCreate } from "@/api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  /**
   * The session probe failed for a reason that is **not** "you are signed out".
   *
   * A 401 is an answer, and `me: null` is the whole of it. A 500, a 502 from the
   * reverse proxy, or `fetch`'s own `TypeError` when nothing is listening is no
   * answer at all — and conflating the two renders the sign-in page for a server
   * that is merely restarting, which reads to the user as an expired session and
   * invites them to type a password that was never the problem.
   */
  probeError: unknown | null;
  /** Re-run the session probe. The retry §4.11 asks for beside the message. */
  retryProbe: () => void;
  login: (email: string, password: string) => Promise<void>;
  /** Open signup: creates the household on the first signup, joins it after. */
  signup: (input: SignupCreate) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [probeError, setProbeError] = useState<unknown | null>(null);
  // Bumped by `retryProbe` to re-run the effect below. A nonce rather than a
  // `probe()` function so the fetch stays in the effect, where it is cancelled
  // on unmount — a retry that resolves after the provider is gone is a
  // setState on nothing.
  const [probeAttempt, setProbeAttempt] = useState(0);
  const signupMutation = useSignup();

  const applyMe = (m: Me | null) => {
    setMe(m);
    setCsrfToken(m?.csrf_token ?? null);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<Me>("/auth/me")
      .then((m) => {
        if (cancelled) return;
        applyMe(m);
        setProbeError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          applyMe(null);
          setProbeError(null);
          return;
        }
        console.error(e);
        // §4.11 wants the API's own message shown rather than a generic one, and
        // for a 5xx it is: `ApiError` carries the server's `detail`. But when
        // nothing answered there *is* no message, and `fetch`'s is
        // "Failed to fetch" — a browser string that tells a self-hoster nothing.
        // So the one case that replaces the message is the case with no message
        // to replace.
        setProbeError(e instanceof ApiError ? e : new Error("The server did not answer."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [probeAttempt]);

  const retryProbe = () => setProbeAttempt((n) => n + 1);

  const login = async (email: string, password: string) => {
    const m = await api.post<Me>("/auth/login", { email, password });
    applyMe(m);
  };

  const signup = async (input: SignupCreate) => {
    applyMe(await signupMutation.mutateAsync(input));
  };

  const logout = async () => {
    await api.post("/auth/logout");
    applyMe(null);
  };

  return (
    <AuthCtx.Provider value={{ me, loading, probeError, retryProbe, login, signup, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
