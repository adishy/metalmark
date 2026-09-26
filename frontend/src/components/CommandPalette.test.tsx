import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import CommandPalette from "@/components/CommandPalette";
import type { NavItem } from "@/components/AppShell";
import { ChartIcon, ListIcon, ReviewIcon, SettingsIcon, WalletIcon } from "@/components/icons";

// `nav` arrives already filtered by `AppShell` — that is the contract, and the
// admin filtering that matters for the nav entries is asserted there, against
// the same expression that decides the header. What is *this* component's own
// job is Settings, which declares an admin-only section of its own, and the
// combobox behaviour: filtering, the highlight, Enter, and the count.

const NAV: NavItem[] = [
  { to: "/accounts", label: "Accounts", Icon: WalletIcon, chord: "a" },
  { to: "/transactions", label: "Transactions", Icon: ListIcon, chord: "t" },
  { to: "/review", label: "Review", Icon: ReviewIcon, chord: "r" },
  { to: "/insights", label: "Insights", Icon: ChartIcon, chord: "i" },
  { to: "/settings", label: "Settings", Icon: SettingsIcon, chord: "s" },
];

/** Where the router actually went: the assertion the palette is *for*. */
function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{`${pathname}${search}`}</p>;
}

function renderPalette({ isAdmin = false }: { isAdmin?: boolean } = {}) {
  const onClose = vi.fn();
  render(
    <MemoryRouter initialEntries={["/transactions"]}>
      <Where />
      <CommandPalette nav={NAV} isAdmin={isAdmin} onClose={onClose} />
    </MemoryRouter>,
  );
  return { onClose };
}

const input = () => screen.getByTestId("palette-input");
const active = () => input().getAttribute("aria-activedescendant");

describe("<CommandPalette />", () => {
  it("filters as you type, and says how many are left", async () => {
    renderPalette();
    // The whole list, before a query: five nav destinations, Insights' two
    // tabs, and Settings' eleven sections less the admin-only one this member
    // cannot reach — 5 + 2 + 10. The count is the palette's own arithmetic, so
    // it is asserted rather than assumed: a destination that goes missing
    // silently is the failure mode this number exists to catch.
    expect(screen.getByTestId("palette-status")).toHaveTextContent("17 destinations");

    await userEvent.type(input(), "trans");

    // One row, and it is the one the query names: "Transactions".
    expect(screen.getByTestId("palette-option-nav-transactions")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-option-nav-accounts")).not.toBeInTheDocument();
    // The count is a live region, and it carries the whole sentence — a bare
    // numeral announced on its own says nothing (§7.6).
    expect(screen.getByTestId("palette-status")).toHaveTextContent("1 destination");
    expect(screen.getByTestId("palette-status")).toHaveAttribute("role", "status");
    expect(screen.getByTestId("palette-status")).toHaveAttribute("aria-atomic", "true");
  });

  it("matches a tab by the section it is in, not only by its own name", async () => {
    // "settings owner" is two tokens against two different fields of the row.
    renderPalette();
    await userEvent.type(input(), "settings owner");

    expect(screen.getByTestId("palette-option-settings-owners")).toBeInTheDocument();
    expect(screen.queryByTestId("palette-option-settings-tags")).not.toBeInTheDocument();
  });

  it("says so when nothing matches, rather than going quiet", async () => {
    renderPalette();
    await userEvent.type(input(), "zzz");

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByTestId("palette-status")).toHaveTextContent("No destination matches “zzz”.");
  });

  it("moves the highlight with the arrows, Home and End", async () => {
    renderPalette();

    // Focus starts in the field, and the highlight is expressed as
    // `aria-activedescendant` rather than by moving focus — so the arrows are
    // read off the input, which is where focus stays for the whole gesture.
    expect(input()).toHaveFocus();
    expect(active()).toBe("palette-option-nav-accounts");

    await userEvent.keyboard("{ArrowDown}");
    expect(active()).toBe("palette-option-nav-transactions");
    await userEvent.keyboard("{ArrowUp}");
    expect(active()).toBe("palette-option-nav-accounts");
    // Up at the top is a no-op, not a jump to the end.
    await userEvent.keyboard("{ArrowUp}");
    expect(active()).toBe("palette-option-nav-accounts");

    await userEvent.keyboard("{End}");
    expect(active()).toBe("palette-option-settings-profile");
    await userEvent.keyboard("{Home}");
    expect(active()).toBe("palette-option-nav-accounts");
  });

  it("activates the highlighted destination with Enter, and closes", async () => {
    const { onClose } = renderPalette();
    await userEvent.type(input(), "review");
    await userEvent.keyboard("{Enter}");

    expect(screen.getByTestId("where")).toHaveTextContent("/review");
    expect(onClose).toHaveBeenCalled();
  });

  it("is labelled, and describes itself as the listbox it is", () => {
    renderPalette();
    const combobox = input();
    expect(combobox).toHaveAttribute("role", "combobox");
    expect(combobox).toHaveAttribute("aria-autocomplete", "list");
    expect(combobox).toHaveAttribute("aria-expanded", "true");
    // The visible label, bound by id — a palette search box is still an input
    // (§4.3: no placeholder-only labels).
    expect(screen.getByLabelText("Search destinations")).toBe(combobox);
    expect(screen.getByRole("listbox", { name: "Destinations" })).toBeInTheDocument();
    // The surface is a dialog, named by the visible heading rather than by an
    // `aria-label` that could drift from it (§4.8).
    expect(screen.getByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("keeps focus inside itself", async () => {
    renderPalette();
    const dialog = screen.getByRole("dialog", { name: "Command palette" });

    // Tab from the field wraps to the panel's own close button, and Tab from
    // there comes back — the trap Dialog owns, exercised through the one thing
    // that can escape it (a field that is not the first focusable in the panel).
    await userEvent.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await userEvent.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("hides the admin-only Settings section from a member", async () => {
    // A member's routes 403 server-side, so the row would open onto a screen of
    // errors. The palette applies the same flag that filtered the nav it was
    // handed — there is no second rule, only Settings' own `adminOnly`.
    //
    // The nav's Admin destination is deliberately not asserted here: `nav`
    // arrives already filtered (the fixture has no Admin in it either), so
    // "absent" would be true whatever this component did. That filter is
    // asserted in `AppShell.test.tsx`, against the same expression the header
    // renders.
    renderPalette({ isAdmin: false });
    await userEvent.type(input(), "admin");
    expect(screen.queryByTestId("palette-option-settings-admin")).not.toBeInTheDocument();
    expect(screen.getByTestId("palette-status")).toHaveTextContent("No destination matches");
  });

  it("shows it to an administrator", async () => {
    renderPalette({ isAdmin: true });
    await userEvent.type(input(), "admin");
    expect(screen.getByTestId("palette-option-settings-admin")).toBeInTheDocument();
  });
});
