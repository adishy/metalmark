import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// The shim the browser appends to the generated worker, read as source. It lives
// in `public/` — outside `src/`, so outside the TypeScript program, outside
// typecheck and outside design-lint — and this import is the only thing in the
// build that would fail if it were deleted or renamed. See ADR-0037.
import shim from "../../public/sw-notify.js?raw";
import type { SyncNotice } from "@/api/types";
import * as notify from "@/lib/notify";

// ---- harnesses -------------------------------------------------------------

type Listener = (event: unknown) => void;

/** Run the shim against a fake `self` and return the listeners it registered.
 *
 *  Run, not grepped. A test asserting the file *contains* "notificationclick"
 *  would pass on a handler whose first line throws, and this file is checked by
 *  nothing else whatsoever — so it is executed here against the smallest `self`
 *  that satisfies it. */
function loadShim(self: Record<string, unknown>): Record<string, Listener> {
  const listeners: Record<string, Listener> = {};
  new Function("self", shim)({
    ...self,
    addEventListener: (type: string, fn: Listener) => {
      listeners[type] = fn;
    },
  });
  return listeners;
}

/** Dispatch a click and wait for the work it asked to be kept alive for. */
async function click(
  listeners: Record<string, Listener>,
  data: Record<string, unknown> = {},
): Promise<{ close: ReturnType<typeof vi.fn> }> {
  const close = vi.fn();
  let settled: Promise<unknown> = Promise.resolve();
  listeners.notificationclick?.({
    notification: { close, data },
    waitUntil: (p: Promise<unknown>) => {
      settled = p;
    },
  });
  await settled;
  return { close };
}

function windowClient(url: string) {
  return { url, focus: vi.fn().mockResolvedValue(undefined), navigate: vi.fn() };
}

const SCOPE = "http://localhost:5173/";

// ---- the shim --------------------------------------------------------------

describe("public/sw-notify.js", () => {
  it("registers a notificationclick listener", () => {
    const listeners = loadShim({});
    expect(typeof listeners.notificationclick).toBe("function");
  });

  it("closes the notification and focuses the tab that is already open", () => {
    // The point of the shim. A click on a notification about a broken bank
    // connection should land on the page the user very likely already has open,
    // not add a second copy of it to the tab strip.
    const existing = windowClient(`${SCOPE}accounts`);
    const openWindow = vi.fn();
    const listeners = loadShim({
      registration: { scope: SCOPE },
      clients: { matchAll: vi.fn().mockResolvedValue([existing]), openWindow },
    });

    return click(listeners).then(({ close }) => {
      expect(close).toHaveBeenCalled();
      expect(existing.focus).toHaveBeenCalled();
      expect(existing.navigate).toHaveBeenCalledWith("/admin");
      expect(openWindow).not.toHaveBeenCalled();
    });
  });

  it("leaves a tab that is already on the target alone", () => {
    // `navigate` on a client that is already there would be a reload of the page
    // the user is looking at, for nothing.
    const existing = windowClient(`${SCOPE}admin`);
    const listeners = loadShim({
      registration: { scope: SCOPE },
      clients: { matchAll: vi.fn().mockResolvedValue([existing]), openWindow: vi.fn() },
    });

    return click(listeners).then(() => {
      expect(existing.focus).toHaveBeenCalled();
      expect(existing.navigate).not.toHaveBeenCalled();
    });
  });

  it("opens a tab when there is none, and ignores windows outside the app", () => {
    const elsewhere = windowClient("https://example.com/");
    const openWindow = vi.fn().mockResolvedValue(undefined);
    const listeners = loadShim({
      registration: { scope: SCOPE },
      clients: { matchAll: vi.fn().mockResolvedValue([elsewhere]), openWindow },
    });

    return click(listeners).then(() => {
      expect(elsewhere.focus).not.toHaveBeenCalled();
      expect(openWindow).toHaveBeenCalledWith("/admin");
    });
  });

  it("goes where the notification said, and to the panel when it said nothing", async () => {
    const existing = windowClient(`${SCOPE}accounts`);
    const openWindow = vi.fn();
    const listeners = loadShim({
      registration: { scope: SCOPE },
      clients: { matchAll: vi.fn().mockResolvedValue([existing]), openWindow },
    });

    await click(listeners, { url: "/settings" });
    expect(existing.navigate).toHaveBeenCalledWith("/settings");
  });
});

// ---- the display path ------------------------------------------------------

const NOTICE: SyncNotice = {
  id: "notice-1",
  run_id: "run-1",
  ts: "2026-09-20T12:00:00Z",
  connection_id: "conn-1",
  title: "Bank connection problem",
  body: "SimpleFIN Bridge: the credential was refused",
};

/** Put a browser under the module. `registration: false` is a browser that has
 *  the API but no worker — which is every `vite dev` session, since the plugin
 *  builds the worker only for a production build. */
function browser({
  permission = "granted" as NotificationPermission,
  registration = true,
}: { permission?: NotificationPermission; registration?: boolean } = {}) {
  const showNotification = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { permission, requestPermission: vi.fn().mockResolvedValue(permission) },
  });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      getRegistration: vi
        .fn()
        .mockResolvedValue(registration ? { showNotification } : undefined),
    },
  });
  return { showNotification };
}

beforeEach(() => {
  notify.resetForTests();
  window.localStorage.clear();
});

afterEach(() => {
  Reflect.deleteProperty(window, "Notification");
  Reflect.deleteProperty(navigator, "serviceWorker");
  vi.restoreAllMocks();
});

describe("deliver", () => {
  it("shows a notice once, then never again", async () => {
    const { showNotification } = browser();

    expect(await notify.deliver([NOTICE])).toBe(1);
    expect(showNotification).toHaveBeenCalledTimes(1);
    expect(showNotification.mock.calls[0][0]).toBe(NOTICE.title);
    expect(showNotification.mock.calls[0][1]).toMatchObject({
      body: NOTICE.body,
      // Per connection, so a bank that keeps failing replaces its own standing
      // notification rather than stacking a column of them (ADR-0037 §6).
      tag: NOTICE.connection_id,
      data: { url: "/admin" },
    });

    // The cursor is the browser's memory of this. A poll re-reading the same
    // window is the normal case, not an edge one — it is what happens every
    // time the app is reloaded — so "again" has to be silent.
    expect(notify.readCursor()).toBe(NOTICE.id);
    expect(await notify.deliver([NOTICE])).toBe(0);
    expect(showNotification).toHaveBeenCalledTimes(1);
  });

  it("tags a notice with no connection by its own id", async () => {
    const { showNotification } = browser();

    await notify.deliver([{ ...NOTICE, connection_id: null }]);

    expect(showNotification.mock.calls[0][1].tag).toBe(NOTICE.id);
  });

  it("shows nothing, and moves nothing, without permission", async () => {
    // The property that makes the permission button in Admin work at all: the
    // poll has been running the whole time, so the notices that arrived while it
    // was denied are still in the window and are shown the moment it is granted
    // — rather than being consumed silently by a cursor that advanced anyway.
    const { showNotification } = browser({ permission: "denied" });

    expect(await notify.deliver([NOTICE])).toBe(0);
    expect(showNotification).not.toHaveBeenCalled();
    expect(notify.readCursor()).toBeNull();

    browser({ permission: "granted" });
    expect(await notify.deliver([NOTICE])).toBe(1);
  });

  it("shows nothing when the page has no worker to show through", async () => {
    // A dev server, where `vite-plugin-pwa` builds no worker at all. Nothing is
    // displayed and nothing is remembered, so a production build still has the
    // notice to show.
    const { showNotification } = browser({ registration: false });

    expect(await notify.deliver([NOTICE])).toBe(0);
    expect(showNotification).not.toHaveBeenCalled();
    expect(notify.readCursor()).toBeNull();
  });

  it("is a no-op in a browser without the API", async () => {
    // No stubs at all: exactly the environment every other test in this suite
    // runs in, and any origin that is not a secure context.
    expect(notify.support()).toBe("unsupported");
    expect(await notify.deliver([NOTICE])).toBe(0);
  });

  it("survives storage that refuses to be read", async () => {
    // Private windows, blocked site data, a zero quota. A notification system
    // that threw here would take the page down with it.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage is blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage is blocked");
    });
    browser();

    expect(notify.readCursor()).toBeNull();
    expect(await notify.deliver([NOTICE])).toBe(1);
    expect(notify.readCursor()).toBeNull();
  });
});
