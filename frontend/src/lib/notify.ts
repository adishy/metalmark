// Desktop notifications: the cursor, the permission, and the one display path
// (ADR-0037).
//
// The app does not decide when to notify — the worker does, and it records the
// decision as a `run.notified` row. This module's whole job is to *read* that
// decision back and show it: poll, display what is new, remember how far it got.
// A page that re-derived `should_notify` in TypeScript would drift from the
// Python, and it would drift towards notifying too often, which is exactly the
// failure the rule exists to prevent.
//
// **`registration.showNotification`, never `new Notification(...)`.** The
// page-context constructor throws outright on Android Chrome, so the one path
// that works everywhere is the only one taken. The click behaviour lives in
// `public/sw-notify.js`, which the browser appends to the generated worker.
//
// **No financial detail.** The title and body come from the server already
// sanitized and already composed (see `SyncNotice`); nothing here reads a
// transaction, and nothing here builds a sentence out of a field.

import type { SyncNotice } from "@/api/types";

/** Where the last-shown notice's id lives, per browser. */
const CURSOR_KEY = "metalmark.notify.since";

/** Ids shown in this tab already.
 *
 *  The durable half of "shown once per browser" is the cursor; this is the
 *  in-memory half, and it is what makes a double-invoked effect — React's
 *  StrictMode in development, or two polls in flight at once — not show the same
 *  notification twice. A `Set` in a module, rather than state, because nothing
 *  renders from it. */
const shownInThisTab = new Set<string>();

export type NotifySupport = NotificationPermission | "unsupported";

/** What the browser will let us do, as one value rather than three checks.
 *
 *  `"unsupported"` is a real answer and not a fallback: jsdom has no
 *  `Notification` at all, Firefox on http:// (which is how a self-hosted LAN app
 *  is reached) has the API behind a secure-context gate, and both must render as
 *  a page that says so rather than as a button that does nothing. */
export function support(): NotifySupport {
  if (typeof window === "undefined") return "unsupported";
  if (!("Notification" in window) || !("serviceWorker" in navigator)) return "unsupported";
  return window.Notification.permission;
}

/** The cursor, or `null` for "show me the window".
 *
 *  `localStorage` can throw rather than return — a private window, blocked site
 *  data, a storage quota of zero — and a notification system that crashes the
 *  page it lives on would be worse than one that forgets where it was. A lost
 *  cursor re-shows notices the user has already seen, which is the benign
 *  direction (ADR-0037, Consequences). */
export function readCursor(): string | null {
  try {
    return window.localStorage.getItem(CURSOR_KEY);
  } catch {
    return null;
  }
}

function writeCursor(id: string): void {
  try {
    window.localStorage.setItem(CURSOR_KEY, id);
  } catch {
    /* see readCursor */
  }
}

/** Ask. Only ever from a click — see `Admin`'s button, and ADR-0037 §4: a
 *  permission prompt that appears unbidden is the one everybody denies, and a
 *  denial is permanent for the origin. */
export async function requestPermission(): Promise<NotifySupport> {
  if (support() === "unsupported") return "unsupported";
  return await window.Notification.requestPermission();
}

/** The registration whose scope covers this page, if there is one.
 *
 *  `null` in development, where `vite-plugin-pwa` builds the worker only for a
 *  production build — so this is also the check that keeps `deliver` from
 *  throwing on a dev server. */
async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  return (await navigator.serviceWorker.getRegistration()) ?? null;
}

/** Show every notice not yet shown, and return how many were.
 *
 *  The cursor advances **per notice, after it is shown** — not once at the end
 *  and not before the call. If permission is missing or the worker is not
 *  registered, nothing advances at all: the next poll asks for the same window
 *  again, which is what makes granting permission in Admin deliver the notices
 *  that arrived while it was denied instead of silently swallowing them. */
export async function deliver(notices: SyncNotice[]): Promise<number> {
  const fresh = notices.filter((n) => !shownInThisTab.has(n.id));
  if (fresh.length === 0 || support() !== "granted") return 0;

  const worker = await registration();
  if (worker === null) return 0;

  for (const notice of fresh) {
    await worker.showNotification(notice.title, {
      body: notice.body,
      // One per connection, so a bank that keeps failing replaces its own
      // standing notification instead of stacking a column of them (§6).
      tag: notice.connection_id ?? notice.id,
      icon: "/notification-icon.png",
      badge: "/notification-icon.png",
      // Read by `public/sw-notify.js` on click. The panel, because that is where
      // a broken connection is operated — and because every notice so far is
      // about one.
      data: { url: "/admin" },
    });
    shownInThisTab.add(notice.id);
    writeCursor(notice.id);
  }
  return fresh.length;
}

/** Forget the tab's dedupe set. For tests, and for nothing else. */
export function resetForTests(): void {
  shownInThisTab.clear();
}
