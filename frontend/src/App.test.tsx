import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { AuthProvider } from "@/auth/AuthContext";
import { setCsrfToken } from "@/api/client";

// The session probe is the app's first request and the only one whose failure
// decides which *screen* you get, so it is the one failure a user cannot route
// around. These tests exist because that made it the one `isError` branch that
// skipped §4.11: every failure of `GET /auth/me` landed on `me: null`, and
// `me: null` is the sign-in page — so an api container restarting, a 502 from
// the proxy in front of it, or a dropped connection all presented as "your
// session expired", inviting a password that was never the problem.
//
// Composition mirrors `main.tsx`, with `MemoryRouter` in place of
// `BrowserRouter` — the difference under test is upstream of routing, and a
// memory router keeps the assertions off `window.history`.

const fetchMock = vi.fn();

function response(init: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  body?: string;
}) {
  const body = init.body ?? "";
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? "",
    text: async () => body,
  } as unknown as Response;
}

const json = (value: unknown) => response({ body: JSON.stringify(value) });

/**
 * Answer the whole authenticated boot, not just the probe.
 *
 * A successful probe does not end the render — it hands off to `AppShell`, which
 * routes to `/accounts`, which has its own reads. A mock that answers *every*
 * path with the `Me` body gives those reads an object where they expect an
 * array, and the failure surfaces as an unhandled `owners.data?.forEach is not a
 * function` that has nothing to do with the probe. So the routes the shell
 * needs are named here, and the default is the empty list they all accept.
 */
function bootFetch() {
  return async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/auth/me")) return json(ME);
    // Shaped to `NetWorth`, not merely truthy: `Accounts` reads
    // `unconverted_currencies.length` off it, so a stub that is only
    // approximately right throws inside the page and reads as a probe failure.
    if (url.includes("/accounts/net-worth")) {
      return json({
        base_currency: "USD",
        assets: "0",
        liabilities: "0",
        net_worth: "0",
        unconverted_currencies: [],
      });
    }
    return json([]);
  };
}

const ME = {
  user: { id: "u1", email: "a@b.c", display_name: "A", is_admin: true },
  household_id: "h1",
  household_name: "Home",
  base_currency: "USD",
  role: "owner",
  csrf_token: "csrf",
};

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  // The provider logs the non-401 failures before turning them into state —
  // correct, and noisy here. Silenced rather than removed: the assertion that
  // it *was* logged is below.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setCsrfToken(null);
});

describe("the session probe", () => {
  it("keeps a 401 meaning signed out, and shows the sign-in page", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 401, body: '{"detail":"Not authenticated"}' }),
    );
    renderApp();

    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(screen.queryByTestId("session-unreachable")).not.toBeInTheDocument();
  });

  it("does not call a server that never answered 'signed out'", async () => {
    // What a restarting api container looks like from the browser: `fetch`
    // rejects, and there is no HTTP status anywhere in the story.
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderApp();

    expect(await screen.findByTestId("session-unreachable")).toBeInTheDocument();
    expect(screen.queryByTestId("login-form")).not.toBeInTheDocument();
  });

  it("says the server did not answer rather than repeating fetch's TypeErrors", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderApp();

    const card = await screen.findByTestId("session-unreachable");
    expect(card).toHaveTextContent("The server did not answer.");
    // §4.11 renders the API's message rather than a generic one because the API
    // says what went wrong. This is the case with no API and therefore no
    // message, so it is the one case that replaces one — and "Failed to fetch"
    // is a browser string, not a diagnosis.
    expect(card).not.toHaveTextContent("Failed to fetch");
  });

  it("shows the API's own message when the API is what failed", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 502, body: '{"detail":"Bad gateway"}' }),
    );
    renderApp();

    const card = await screen.findByTestId("session-unreachable");
    expect(card).toHaveTextContent("Bad gateway");
    expect(screen.queryByTestId("login-form")).not.toBeInTheDocument();
  });

  it("still reports the failure to the console, so it is not swallowed", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderApp();

    await screen.findByTestId("session-unreachable");
    expect(console.error).toHaveBeenCalled();
  });

  it("lets the retry succeed once the server is back", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    fetchMock.mockImplementation(bootFetch());
    renderApp();

    await screen.findByTestId("session-unreachable");
    await userEvent.click(screen.getByTestId("session-unreachable-retry"));

    // Signed in for real: the shell is up and the error card is gone. The
    // negative assertion is the half that matters — "the card went away" would
    // also be true if the retry had landed on the sign-in page.
    expect(await screen.findByTestId("nav-accounts")).toBeInTheDocument();
    expect(screen.queryByTestId("session-unreachable")).not.toBeInTheDocument();
    expect(screen.queryByTestId("login-form")).not.toBeInTheDocument();
  });
});
