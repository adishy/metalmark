import { useEffect, useState } from "react";

/**
 * Subscribe to a media query.
 *
 * Deliberately narrow: CSS handles almost all responsive behaviour here, and a
 * matchMedia subscription is the slower, hydration-sensitive way to do it. It
 * earns its place only where *behaviour*, not appearance, differs — the dialog
 * drag-to-dismiss exists in the phone sheet and must not exist in the desktop
 * modal, and a CSS class cannot tell a gesture handler which one is on screen.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    // Read once on mount as well as subscribing: the initialiser above ran
    // during render, and the query may have changed since.
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** The phone breakpoint, matching Tailwind's `sm` (640px) — max-width is the
 *  complement of `sm:` so the two agree at the boundary. */
export function useIsPhone(): boolean {
  return useMediaQuery("(max-width: 639px)");
}

/** Tailwind's `lg` (1024px), verbatim.
 *
 *  Same justification as above, one breakpoint up: §9.3 puts the transaction
 *  detail *beside* the list at `lg:` instead of over it, and "beside" is not a
 *  style — it is the difference between a pane and a modal. A pane traps no
 *  focus, locks no scroll, closes on no Escape, and carries no `aria-modal`,
 *  and no CSS class can tell an effect hook any of that. */
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 1024px)");
}
