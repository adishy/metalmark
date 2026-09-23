import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Account, Allocation, Owner, Portfolio } from "@/api/types";
import Accounts from "@/pages/Accounts";
import { formatMoney } from "@/lib/format";
import { todayIso } from "@/lib/dates";

// The page's own reads, stubbed. `useCreateAccount`/`useUpdateAccount`/`useDeleteAccount`
// are never driven here, but the page calls them on every render and they reach the
// network through TanStack Query otherwise.
const h = vi.hoisted(() => ({
  allocation: vi.fn(),
  portfolio: vi.fn(),
  holdings: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));

vi.mock("@/api/investments", async () => {
  const actual = await vi.importActual<typeof import("@/api/investments")>("@/api/investments");
  return {
    ...actual,
    useAllocation: () => h.allocation(),
    usePortfolio: () => h.portfolio(),
    useHoldings: () => h.holdings(),
  };
});

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  const settled = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    mutate: vi.fn(),
    isSuccess: true,
  });
  return {
    ...actual,
    useOwners: () => settled(OWNERS),
    useAccounts: () => settled(ACCOUNTS),
    useNetWorth: () => settled(NET_WORTH),
    useCreateAccount: () => ({ ...settled(undefined), mutate: h.create }),
    useUpdateAccount: () => ({ ...settled(undefined), mutate: h.update }),
    useDeleteAccount: () => settled(undefined),
  };
});

const OWNERS = [{ id: "owner-1", name: "Alice", kind: "person", sort: 0 }] as unknown as Owner[];

const ACCOUNTS = [
  {
    id: "acct-1",
    name: "Checking",
    type: "depository",
    currency: "USD",
    owner_id: "owner-1",
    is_asset: true,
    is_hidden: false,
    institution: null,
    current_balance: "100.00",
    balance_date: "2026-01-01",
  },
] as unknown as Account[];

const NET_WORTH = {
  net_worth: "100.00",
  assets: "100.00",
  liabilities: "0.00",
  base_currency: "USD",
  unconverted_currencies: [],
} as unknown as ReturnType<typeof import("@/api/hooks").useNetWorth>["data"];

const ALLOCATION = {
  as_of: "2026-09-20",
  base_currency: "USD",
  group_by: "security",
  total_base: "3000.00",
  rows: [{ key: "sec-vti", label: "VTI", value_base: "3000.00", percent: "100.0000", holdings: 1 }],
  unpriced_positions: 0,
  no_rate_positions: 0,
  max_stale_days: null,
} as unknown as Allocation;

// One account, not none: the holdings view shows no total card when there is
// nothing to total (§4.10 — `$0.00` over "No investment accounts yet." states a
// figure about a household that has none), so an empty portfolio here would mean
// the assertions below could not tell the view from an empty one.
const PORTFOLIO = {
  as_of: "2026-09-20",
  base_currency: "USD",
  total_base: "3000.00",
  accounts: [
    {
      account_id: "acct-broker",
      name: "Brokerage",
      currency: "USD",
      balance_source: "derived",
      balance_account: "3000.00",
      market_value_account: "3000.00",
      market_value_base: "3000.00",
      stated_balance_base: null,
      unaccounted_cash_base: "0.0000",
      holdings: [],
      unpriced: 0,
      no_rate: 0,
      oldest_price_date: null,
      max_stale_days: null,
      is_fully_valued: true,
    },
  ],
} as unknown as Portfolio;

beforeEach(() => {
  h.create.mockReset();
  h.update.mockReset();
  h.allocation.mockReset();
  h.portfolio.mockReset();
  h.holdings.mockReset();
  h.allocation.mockImplementation(() => ({
    data: ALLOCATION,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
  h.portfolio.mockImplementation(() => ({
    data: PORTFOLIO,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
  h.holdings.mockImplementation(() => ({
    data: [],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
});

describe("<Accounts /> views", () => {
  it("opens on the balances view, with the page's one heading above both", () => {
    render(<Accounts />);

    expect(screen.getByTestId("accounts-view-balances")).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByTestId("accounts-view-panel-balances")).getByTestId("account-list"))
      .toHaveTextContent("Checking");
    expect(screen.queryByTestId("investments-view")).not.toBeInTheDocument();

    // One `<h1>` for the page, above the switcher: the panels select what is
    // below it, so a heading inside a panel would leave one view without a
    // top-level heading (§7.5).
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Accounts");
  });

  it("swaps the panel for the investments view without leaving the page", async () => {
    const user = userEvent.setup();
    render(<Accounts />);

    await user.click(screen.getByTestId("accounts-view-investments"));

    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
    // The allocation and the valued holdings list are what the second view is.
    expect(screen.getByTestId("allocation-total")).toHaveTextContent(formatMoney("3000.00", "USD"));
    expect(screen.getByTestId("portfolio-total")).toHaveTextContent(formatMoney("3000.00", "USD"));
    // The balances panel is gone, not merely hidden: the two views do not share
    // a DOM.
    expect(screen.queryByTestId("account-list")).not.toBeInTheDocument();
    expect(screen.queryByTestId("net-worth")).not.toBeInTheDocument();
  });

  it("keeps the panel addressable from the tab that selected it", async () => {
    const user = userEvent.setup();
    render(<Accounts />);

    const tab = screen.getByTestId("accounts-view-investments");
    await user.click(tab);

    // aria-controls resolving to the mounted panel is what makes the tablist
    // more than a decoration (§7.4).
    const panel = screen.getByTestId(tab.getAttribute("aria-controls") ?? "");
    expect(panel).toHaveAttribute("role", "tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", tab.id);
  });

  it("says the portfolio ignores an active owner filter, rather than swapping totals silently", async () => {
    const user = userEvent.setup();
    render(<Accounts />);

    // No filter yet, so there is nothing to warn about and no note.
    expect(screen.queryByTestId("investments-owner-note")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("owner-filter-owner-1"));
    await user.click(screen.getByTestId("accounts-view-investments"));

    // The portfolio endpoints have no owner scope, so a reader who filtered to
    // one owner and switched views would otherwise see household totals appear
    // under an active filter with nothing said — which reads as the filter having
    // stopped working.
    expect(screen.getByTestId("investments-owner-note")).toHaveTextContent("household-wide");
    expect(screen.getByTestId("investments-owner-note")).toHaveTextContent("Alice");
  });

  it("moves between the views with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<Accounts />);

    await user.tab();
    // One tab stop for the group, then the arrow keys move inside it.
    const balances = screen.getByTestId("accounts-view-balances");
    expect(balances).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByTestId("accounts-view-investments")).toHaveFocus();
    expect(screen.getByTestId("accounts-view-investments")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
  });
});

// A balance lands on its own date (session 04, audit #4): the edit dialog used to
// send the account's *old* balance date back with every save, which overwrote that
// day's point in the net-worth history with today's number.
describe("balance writes", () => {
  // The dialogs hold hooks of their own (owner creation, CSV export) that need a
  // client even though nothing here reaches the network.
  const renderPage = () =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Accounts />
      </QueryClientProvider>,
    );
  const sentUpdate = () => h.update.mock.calls[0][0].body as Record<string, unknown>;

  it("sends no balance when only the name changed", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("account-edit-acct-1"));
    const name = screen.getByTestId("edit-account-name");
    await user.clear(name);
    await user.type(name, "Everyday");
    await user.click(screen.getByTestId("edit-account-save"));
    expect(sentUpdate().name).toBe("Everyday");
    expect(sentUpdate()).not.toHaveProperty("current_balance");
    expect(sentUpdate()).not.toHaveProperty("balance_date");
  });

  it("dates a new balance today, not on the account's last balance date", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("account-edit-acct-1"));
    const balance = screen.getByTestId("edit-account-balance");
    await user.clear(balance);
    await user.type(balance, "250.00");
    await user.click(screen.getByTestId("edit-account-save"));
    expect(sentUpdate()).toMatchObject({ current_balance: "250.00", balance_date: todayIso() });
  });

  it("opens an account with no balance when none was typed", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("add-account"));
    const form = screen.getByTestId("add-account-form");
    await user.type(within(form).getByTestId("account-name"), "Imported card");
    await user.click(within(form).getByTestId("account-save"));
    const body = h.create.mock.calls[0][0] as Record<string, unknown>;
    expect(body.name).toBe("Imported card");
    expect(body).not.toHaveProperty("current_balance");
    expect(body).not.toHaveProperty("balance_date");
  });
});
