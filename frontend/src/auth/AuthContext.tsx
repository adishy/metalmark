import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken } from "@/api/client";
import type { Me } from "@/api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const applyMe = (m: Me | null) => {
    setMe(m);
    setCsrfToken(m?.csrf_token ?? null);
  };

  useEffect(() => {
    api
      .get<Me>("/auth/me")
      .then(applyMe)
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401)) console.error(e);
        applyMe(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    const m = await api.post<Me>("/auth/login", { email, password });
    applyMe(m);
  };

  const logout = async () => {
    await api.post("/auth/logout");
    applyMe(null);
  };

  return <AuthCtx.Provider value={{ me, loading, login, logout }}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
