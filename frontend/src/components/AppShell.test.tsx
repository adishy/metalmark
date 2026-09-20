import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/accounts"]}>
      <AppShell>
        <p>content</p>
      </AppShell>
    </MemoryRouter>,
  );
}

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
      for (const label of ["accounts", "transactions", "review", "reports", "settings"]) {
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
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <AppShell>
          <p>content</p>
        </AppShell>
      </MemoryRouter>,
    );
    expect(document.title).toBe("Admin · MetalMark");
  });
});
