// Theme state: the light/dark choice, and the machinery that applies it.
//
// Deliberately not a React context. The theme is read by the root <html> class,
// not by components, so a context provider would be ceremony around a value only
// two components ever consume. An external store with `useSyncExternalStore`
// keeps it usable from anywhere (including tests) and needs no provider nesting.
//
// The inline script in index.html applies the theme before first paint; this
// module takes over once React is running. Both read the same storage key.
import { useSyncExternalStore } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/** Keep in step with the inline script in index.html. */
export const THEME_KEY = "metalmark-theme";

/** Mobile browser chrome / iOS status bar. Mirrors `--surface` in each theme. */
const THEME_COLOR: Record<ResolvedTheme, string> = {
  light: "#f8fafc",
  dark: "#020617",
};

function systemPrefersDark(): boolean {
  // jsdom (vitest) has no matchMedia. `:root` in index.css is light, so report
  // light here too — otherwise a test would render a `.dark` root against a
  // stylesheet whose default is light, and neither the test nor the app would
  // be describing the same thing.
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function readChoice(): ThemeChoice {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return raw === "light" || raw === "dark" ? raw : "system";
  } catch {
    return "system"; // localStorage throws in a private window
  }
}

function resolveFor(choice: ThemeChoice): ResolvedTheme {
  if (choice === "system") return systemPrefersDark() ? "dark" : "light";
  return choice;
}

/**
 * The snapshot carries the *resolved* theme as well as the choice, and identity
 * is what tells React whether to re-render. With only the choice in it, flipping
 * the OS preference would recompute the same `"system"` string, React would bail
 * out of the update, and the screen would keep the old theme.
 */
let snapshot: { choice: ThemeChoice; resolved: ResolvedTheme } = {
  choice: "system",
  resolved: "light",
};

const listeners = new Set<() => void>();

function apply(): void {
  const resolved = resolveFor(snapshot.choice);
  snapshot = { choice: snapshot.choice, resolved };

  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("dark", resolved === "dark");
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (meta) meta.content = THEME_COLOR[resolved];
  }

  for (const l of listeners) l();
}

let started = false;

/** Called once from main.tsx. Idempotent, so tests may call it freely. */
export function initTheme(): void {
  if (started) return;
  started = true;

  snapshot = { choice: readChoice(), resolved: "light" };
  apply();

  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    // Only meaningful while the choice defers to the OS, but it must stay
    // subscribed: switching to "system" later has to pick up the live value.
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (snapshot.choice === "system") apply();
    });
  }
}

export function setTheme(choice: ThemeChoice): void {
  try {
    // "system" is the absence of an override, not a third stored value.
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    /* Not fatal — the choice just will not survive a reload. */
  }
  snapshot = { choice, resolved: snapshot.resolved };
  apply();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => snapshot;

export function useTheme(): {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setTheme: (choice: ThemeChoice) => void;
} {
  const s = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { choice: s.choice, resolved: s.resolved, setTheme };
}
