import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AppShell from "@/components/AppShell";
import type { Me } from "@/api/types";

// The shell reads the current user to decide whether to show the admin
// destination. `me` is mutable so one file can render both audiences — the
// whole point of these tests is that the two differ.
let me: Me | null = null;

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ me, logout: vi.fn(), login: vi.fn(), signup: vi.fn(), loading: false }),
}));

function who({ isAdmin, role = "owner" }: { isAdmin: boolean; role?: string }): Me {
  return {
    user: { id: "u1", email: "a@b.c", display_name: "A", is_admin: isAdmin },
    household_id: "h1",
    household_name: "Home",
    base_currency: "USD",
    role,
    csrf_token: "csrf",
  };
}

/** The shell now runs the notice poll (`useSyncNotices`, ADR-0037), so it needs
 *  a query client. In the navigation tests below the poll never starts of its
 *  own accord — jsdom has no `Notification`, so `support()` answers
 *  "unsupported" — which is exactly the browser those tests are pretending to
 *  be. The notice-poll block stubs the two APIs, which is what starts it. */
function renderShell({ at = "/accounts" }: { at?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[at]}>
        <AppShell>
          <p>content</p>
          <Where />
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Where the router actually is — the assertion the palette and the chords are for. */
function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{`${pathname}${search}`}</p>;
}

/**
 * Answer `matchMedia` as a window of a given width would.
 *
 * jsdom does not lay anything out, so the two media queries the shell's
 * behaviour is gated on — `lg:` (the palette and its chords) and
 * `prefers-reduced-motion` — can only be answered by a stub. `AppShell` reads
 * the first one through `useIsDesktop()`, which is a *behaviour* gate rather
 * than a class (see `lib/media.ts`), so a test that does not stub this is
 * testing the phone and would pass on a palette that never mounted.
 */
function asDesktop(desktop: boolean) {
  const matches = (query: string) => (query.includes("min-width: 1024px") ? desktop : false);
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: matches(query),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

describe("<AppShell /> notice poll", () => {
  // The one property neither jsdom nor the e2e browser can reach on its own:
  // both are "unsupported" — jsdom has no `Notification`, and the e2e stack is
  // served over `http://web:5173`, which is not a secure context, so the API is
  // absent there too. Without this test the whole feature could be wired to
  // nothing and every other test in the repo would stay green.
  const fetchMock = vi.fn();

  beforeEach(() => {
    me = who({ isAdmin: false });
    fetchMock.mockReset();
    // An empty feed, which is also the best case: nothing to deliver, so this
    // test measures the request rather than the display path.
    fetchMock.mockResolvedValue({ ok: true, status: 200, statusText: "OK", text: async () => "[]" });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { permission: "default", requestPermission: vi.fn() },
    });
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: {} });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(window, "Notification");
    Reflect.deleteProperty(navigator, "serviceWorker");
  });

  it("reads the notice feed from wherever the tab happens to be", async () => {
    renderShell({ at: "/transactions" });

    // The shell also loads institution logos for the account marks; the notice
    // feed is the call this test is about, wherever it falls in the order.
    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => c[0])).toContain("/api/connections/notifications"),
    );
  });
});

describe("<AppShell /> navigation", () => {
  beforeEach(() => {
    me = null;
  });

  it("shows the admin destination to an administrator", () => {
    me = who({ isAdmin: true });
    renderShell();
    expect(screen.getByTestId("nav-admin")).toBeInTheDocument();
    expect(screen.getByTestId("nav-admin")).toHaveAttribute("href", "/admin");
  });

  it("hides the admin destination from a member", () => {
    // A member's routes 403 server-side, so the item would open onto a screen of
    // errors. Hiding it is the honest version of "not yours".
    me = who({ isAdmin: false, role: "member" });
    renderShell();
    expect(screen.queryByTestId("nav-admin")).not.toBeInTheDocument();
  });

  it("still shows the five ordinary destinations to everyone", () => {
    for (const isAdmin of [true, false]) {
      me = who({ isAdmin });
      const { unmount } = renderShell();
      for (const label of ["accounts", "transactions", "review", "insights", "settings"]) {
        expect(screen.getByTestId(`nav-${label}`)).toBeInTheDocument();
      }
      unmount();
    }
  });

  it("caps the phone tab bar at five even for an administrator", () => {
    // The five-item cap is the *tab bar's* (DESIGN.md §4.13) — it is a phone
    // control and a sixth target stops being thumb-reachable. The desktop nav
    // has no such limit, which is what lets `/admin` be a destination at all.
    // If this ever renders six, the cap has been lost rather than moved.
    me = who({ isAdmin: true });
    renderShell();

    const tabs = screen.getAllByTestId(/^tab-/);
    expect(tabs).toHaveLength(5);
    expect(screen.queryByTestId("tab-admin")).not.toBeInTheDocument();
  });

  it("puts the mark beside the wordmark, and not in the reading order", () => {
    // `alt=""` is the assertion, not a detail of it. A screen reader that meets
    // an image with no alt reads the filename, and one that meets `alt="
    // MetalMark"` says the name twice — the wordmark is right there. So the mark
    // is decorative and the test says so, because the difference is invisible on
    // screen and no other test would ever catch it.
    me = who({ isAdmin: false });
    renderShell();

    const mark = screen.getByTestId("metalmark-mark");
    expect(mark).toHaveAttribute("src", "/favicon.svg");
    expect(mark).toHaveAttribute("alt", "");
  });

  it("names the admin route in the document title", () => {
    // The string a person scans for. The panel shipped without the word "Admin"
    // anywhere in the interface, which is a large part of why it went unfound.
    me = who({ isAdmin: true });
    renderShell({ at: "/admin" });
    expect(document.title).toBe("Admin · MetalMark Money");
  });

  it("titles every Insights sub-route as Insights, not just the bare route", () => {
    // Insights is the first destination with sub-routes (/insights/:tab); the
    // title match has to be a prefix, or every tab but the first renders no
    // title at all.
    me = who({ isAdmin: false });
    renderShell({ at: "/insights/allocations" });
    expect(document.title).toBe("Insights · MetalMark Money");
  });
});

describe("<AppShell /> command palette (issue #35)", () => {
  const palette = () => screen.queryByTestId("command-palette");

  /** The palette's search field, once its chunk has resolved.
   *
   *  `lazy()` means every open waits on a dynamic import, so this is the one
   *  wait in these tests that is about the bundler rather than about the
   *  shortcut — and the default one-second `findBy` window is short enough for a
   *  loaded machine to miss it, which reports as "the shortcut does nothing". */
  const paletteInput = () => screen.findByTestId("palette-input", {}, { timeout: 5000 });

  // The palette is a `lazy()` chunk, and the first dynamic import of it in a
  // file is where vitest transforms its whole graph — `CommandPalette` reads its
  // destination lists from the Settings and Insights pages, which are large.
  // That transform is a jsdom-only cost (a browser has the module already built,
  // and the two pages are in the entry chunk either way), and it is longer than
  // the one-second default `findBy` window — so without this warm-up the *first*
  // test to open the palette fails on a Suspense boundary that has not resolved
  // yet, which reads exactly like a shortcut that does not work.
  beforeAll(() => import("@/components/CommandPalette"));

  beforeEach(() => {
    me = who({ isAdmin: true });
    asDesktop(true);
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "matchMedia");
  });

  it("opens on ⌘K, with focus in the field", async () => {
    renderShell();
    expect(palette()).not.toBeInTheDocument();

    await userEvent.keyboard("{Meta>}k{/Meta}");

    // The lazy chunk resolves a tick later, which is why this waits.
    expect(await paletteInput()).toHaveFocus();
  });

  it("opens on Ctrl+K", async () => {
    renderShell();
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(await paletteInput()).toBeInTheDocument();
  });

  it("toggles closed on the shortcut that opened it", async () => {
    renderShell();
    await userEvent.keyboard("{Control>}k{/Control}");
    await paletteInput();

    await userEvent.keyboard("{Control>}k{/Control}");
    await waitFor(() => expect(palette()).not.toBeInTheDocument());
  });

  it("returns focus to the trigger when Escape closes it", async () => {
    // The requirement is about *where focus was*, not about the palette: it
    // goes back to the control the user was on, or the palette is a keyboard
    // trap with a door that only opens inward.
    renderShell();
    const trigger = screen.getByTestId("palette-open");
    await userEvent.click(trigger);
    expect(await paletteInput()).toHaveFocus();

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(palette()).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("does not offer an admin destination to a member", async () => {
    // The nav's own filter, reached through the palette: a member gets neither
    // the Admin section nor Settings' admin-only panel, because the palette is
    // handed the list the header renders rather than building its own.
    me = who({ isAdmin: false, role: "member" });
    renderShell();
    await userEvent.keyboard("{Control>}k{/Control}");
    await paletteInput();

    expect(screen.queryByTestId("palette-option-nav-admin")).not.toBeInTheDocument();
    expect(screen.queryByTestId("palette-option-settings-admin")).not.toBeInTheDocument();
    expect(screen.getByTestId("palette-option-nav-accounts")).toBeInTheDocument();
  });

  it("lands on Admin for an administrator", async () => {
    renderShell();
    await userEvent.keyboard("{Control>}k{/Control}");
    await paletteInput();

    await userEvent.type(screen.getByTestId("palette-input"), "admin");
    // Two destinations answer to "admin" — the section itself and Settings'
    // admin-only panel — and the nav's comes first, so the highlight is already
    // on Admin by the time Enter is pressed.
    expect(screen.getByTestId("palette-option-settings-admin")).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");

    expect(screen.getByTestId("where")).toHaveTextContent("/admin");
  });

  it("is not there at all on a phone-width window", async () => {
    asDesktop(false);
    renderShell();

    // Two halves, and jsdom can only prove one. The trigger's gate is a class
    // (`hidden … lg:inline-flex`), and jsdom applies no CSS, so asking whether
    // the button is *in the document* would fail on a phone that cannot see it.
    // What is asserted here is that it carries the class that hides it; that
    // `display: none` really takes it out of the tab order and the
    // accessibility tree — "no way to open it", not "no room for it" — is a
    // browser's job and is asserted in `e2e/command-palette.spec.ts`.
    expect(screen.getByTestId("palette-open")).toHaveClass("hidden");
    expect(screen.getByTestId("palette-open")).toHaveClass("lg:inline-flex");

    // The behaviour gate, which jsdom *can* prove, and which is the half a class
    // cannot do: the handler is never registered, so the shortcut does nothing.
    // There is no bottom-sheet version of this on a phone (§9.6) — the palette
    // is not mounted at all.
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(palette()).not.toBeInTheDocument();
  });
});

describe("<AppShell /> “g then x” chords (issue #35)", () => {
  beforeEach(() => {
    me = who({ isAdmin: true });
    asDesktop(true);
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "matchMedia");
  });

  it("navigates on g then the section's letter", async () => {
    renderShell({ at: "/accounts" });
    await userEvent.keyboard("gt");
    expect(screen.getByTestId("where")).toHaveTextContent("/transactions");

    await userEvent.keyboard("gr");
    expect(screen.getByTestId("where")).toHaveTextContent("/review");
  });

  it("does not fire on the letter alone, or on a letter that is not a chord", async () => {
    renderShell({ at: "/accounts" });
    await userEvent.keyboard("t");
    expect(screen.getByTestId("where")).toHaveTextContent("/accounts");

    // "g" arms the chord; "z" is not one, so nothing happens and the chord is
    // spent rather than left waiting for the next keystroke.
    await userEvent.keyboard("gz");
    expect(screen.getByTestId("where")).toHaveTextContent("/accounts");
  });

  it("stands down while a field has focus, so a letter stays a letter", async () => {
    // The same rule Review's ← / → follow (§4.17): a plain key is what someone
    // is typing first, and a shortcut that eats it files data they did not mean.
    renderShell({ at: "/settings" });
    const field = document.createElement("input");
    document.body.appendChild(field);
    field.focus();

    await userEvent.keyboard("gt");
    expect(screen.getByTestId("where")).toHaveTextContent("/settings");
    field.remove();
  });

  it("does not fire on a phone", async () => {
    asDesktop(false);
    renderShell({ at: "/accounts" });
    await userEvent.keyboard("gt");
    expect(screen.getByTestId("where")).toHaveTextContent("/accounts");
  });
});
