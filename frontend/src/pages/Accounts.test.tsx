import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Account, Owner, Portfolio } from "@/api/types";
import Accounts from "@/pages/Accounts";
import { formatMoney } from "@/lib/format";
import { isoDay, todayIso } from "@/lib/dates";

// The page's own reads, stubbed. `useCreateAccount`/`useUpdateAccount`/`useDeleteAccount`
// are never driven here, but the page calls them on every render and they reach the
// network through TanStack Query otherwise.
const h = vi.hoisted(() => ({
  portfolio: vi.fn(),
  holdings: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  putBalance: vi.fn(),
  connections: [] as unknown[],
  deleteBalance: vi.fn(),
  series: vi.fn(),
}));

vi.mock("@/api/investments", async () => {
  const actual = await vi.importActual<typeof import("@/api/investments")>("@/api/investments");
  return {
    ...actual,
    usePortfolio: () => h.portfolio(),
    useHoldings: () => h.holdings(),
  };
});

vi.mock("@/api/sync", async () => {
  const actual = await vi.importActual<typeof import("@/api/sync")>("@/api/sync");
  return { ...actual, useConnections: () => ({ data: h.connections }) };
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
    // Recorded rather than stubbed away: which window the card asked the server
    // for is the claim its own label makes, and the two are checked together.
    useNetWorthSeries: (...args: unknown[]) => settled(h.series(...args)),
    useBalances: () => settled(BALANCES),
    usePutBalance: () => ({ ...settled(undefined), mutate: h.putBalance }),
    useDeleteBalance: () => ({ ...settled(undefined), mutate: h.deleteBalance }),
  };
});

// A canvas has nothing to show jsdom; the accessible name is what is asserted.
vi.mock("@/components/Chart", () => ({
  default: ({ label, testid }: { label: string; testid?: string }) => (
    <div role="img" aria-label={label} data-testid={testid} />
  ),
}));

const SERIES = {
  base_currency: "USD",
  granularity: "week",
  start: "2026-06-01",
  end: "2026-09-01",
  points: [
    { date: "2026-06-01", net_worth: "40.00", missing: [] },
    { date: "2026-09-01", net_worth: "100.00", missing: [] },
  ],
};

const BALANCES = [
  { balance_date: "2026-01-01", balance: "100.00", currency: "USD" },
  { balance_date: "2025-12-31", balance: "80.00", currency: "USD" },
];

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
  {
    id: "acct-card",
    name: "Visa",
    type: "credit",
    currency: "USD",
    owner_id: "owner-1",
    is_asset: false,
    is_hidden: false,
    institution: null,
    current_balance: "-850.0000",
    balance_date: "2026-01-01",
    stale_since: "2026-01-01",
  },
] as unknown as Account[];

const NET_WORTH = {
  net_worth: "100.00",
  assets: "100.00",
  liabilities: "0.00",
  base_currency: "USD",
  unconverted_currencies: [],
} as unknown as ReturnType<typeof import("@/api/hooks").useNetWorth>["data"];

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
  h.putBalance.mockReset();
  h.deleteBalance.mockReset();
  h.portfolio.mockReset();
  h.holdings.mockReset();
  h.series.mockReset();
  // The window the card opens on is a remembered choice, so a test that renders
  // the page must not inherit the one before it.
  window.localStorage.clear();
  h.series.mockImplementation(() => SERIES);
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
    render(<MemoryRouter><Accounts /></MemoryRouter>);

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
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    await user.click(screen.getByTestId("accounts-view-investments"));

    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
    // The allocation moved to Insights (session 09, task B4); what's left here
    // is a link to it, plus the valued holdings list.
    expect(screen.getByTestId("allocation-moved-link")).toHaveAttribute("href", "/insights/allocations");
    expect(screen.getByTestId("portfolio-total")).toHaveTextContent(formatMoney("3000.00", "USD"));
    // The tab narrows the list to investment accounts (there are none here, so
    // it says so) and keeps the net worth above it: the page's whole is always
    // on screen, the tab only chooses which part is listed.
    expect(screen.getByTestId("account-list")).toHaveTextContent("No investments accounts yet");
    expect(screen.queryByText("Checking")).not.toBeInTheDocument();
    expect(screen.getByTestId("net-worth")).toBeInTheDocument();
  });

  it("offers a tab only for the kinds of account the household has", () => {
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.getByTestId("accounts-view-cash")).toBeInTheDocument();
    expect(screen.getByTestId("accounts-view-credit")).toBeInTheDocument();
    expect(screen.queryByTestId("accounts-view-loans")).not.toBeInTheDocument();
    // Grouped under their kind, with a total for the group.
    expect(screen.getByTestId("account-group-total-credit").textContent).toBe(
      `${formatMoney("850.00", "USD")} owed`,
    );
  });

  it("names the owner with an avatar, not a line of text", () => {
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    const avatar = screen.getByTestId("account-owner-acct-1");
    expect(avatar).toHaveAccessibleName("Owner: Alice");
    expect(avatar).toHaveTextContent("AL");
    // The row's only words are the account's name and its balance.
    const row = screen.getByTestId("account-edit-acct-1");
    expect(row).not.toHaveTextContent("USD");
  });

  it("puts the net worth, its change and its line first", () => {
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.getByTestId("net-worth")).toHaveTextContent(formatMoney("100.00", "USD"));
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent(
      `Up ${formatMoney("60.00", "USD")} over the past 2 weeks`,
    );
    expect(screen.getByTestId("accounts-net-worth-chart")).toHaveAccessibleName(/went up/);
  });

  it("keeps the panel addressable from the tab that selected it", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Accounts /></MemoryRouter>);

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
    render(<MemoryRouter><Accounts /></MemoryRouter>);

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
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    // One tab stop for the group, then the arrow keys move inside it.
    const balances = screen.getByTestId("accounts-view-balances");
    balances.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByTestId("accounts-view-cash")).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(screen.getByTestId("accounts-view-investments")).toHaveFocus();
    expect(screen.getByTestId("accounts-view-investments")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
  });
});

// The window the hero opens on, and the reader's ability to move it: a day or a
// fortnight at a time ("networth chart at top should have more varied filters
// like last week, last day etc."), a fortnight by default, and whichever they
// last chose when they come back.
describe("the net-worth window", () => {
  const KEY = "metalmark-networth-range";

  /** The day a window of `n` days opens on, as the card computes it. */
  const daysBack = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return isoDay(d);
  };

  it("opens on a fortnight, and asks the server for exactly that", () => {
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    expect(screen.getByTestId("net-worth-range-2w")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent(
      `Up ${formatMoney("60.00", "USD")} over the past 2 weeks`,
    );
    // The label and the request are one fact: a card that said "over 2 weeks"
    // over a window the server was asked for differently is the failure this
    // pins.
    expect(h.series).toHaveBeenLastCalledWith(daysBack(14), todayIso(), null);
  });

  it("offers the short windows beside the long ones", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    for (const [id, back] of [
      ["1d", 1],
      ["1w", 7],
    ] as const) {
      await user.click(screen.getByTestId(`net-worth-range-${id}`));
      expect(h.series).toHaveBeenLastCalledWith(daysBack(back), todayIso(), null);
    }
    // The ladder the card already had is still there, from 1M to all time.
    for (const id of ["1m", "3m", "6m", "1y", "all"]) {
      expect(screen.getByTestId(`net-worth-range-${id}`)).toBeInTheDocument();
    }
  });

  it("remembers the window it was left on, and opens on it next visit", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<MemoryRouter><Accounts /></MemoryRouter>);

    await user.click(screen.getByTestId("net-worth-range-1w"));
    expect(window.localStorage.getItem(KEY)).toBe("1w");
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent("over the past week");
    expect(h.series).toHaveBeenLastCalledWith(daysBack(7), todayIso(), null);

    // A new visit: the preference is read at mount, not carried in React state.
    unmount();
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.getByTestId("net-worth-range-1w")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent("over the past week");
  });

  it("ignores a remembered window it cannot draw", () => {
    // A week ladder from an older build, or a hand-typed value: the card opens on
    // its default rather than on a window with no row.
    window.localStorage.setItem(KEY, "42y");
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.getByTestId("net-worth-range-2w")).toHaveAttribute("aria-pressed", "true");
  });

  it("says which accounts it only counted partway through", () => {
    // The figure a window states is the difference between two totals, and when
    // an account joins in between, part of that difference is the account rather
    // than money moving. The chart marks the point and withholds the bar; these
    // are the words (ADR-0045), the same ones the Insights card prints.
    h.series.mockImplementation(() => ({
      ...SERIES,
      points: [
        {
          date: "2026-06-01",
          net_worth: "40.00",
          missing: [{ account_id: "acct-2", name: "Savings", reason: "not_started" }],
        },
        { date: "2026-09-01", net_worth: "100.00", missing: [] },
      ],
    }));
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    expect(screen.getByTestId("accounts-net-worth-coverage")).toHaveTextContent(
      "Counted partway through: Savings from Sep 01.",
    );
  });

  it("stays quiet when every point counts everything", () => {
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.queryByTestId("accounts-net-worth-coverage")).not.toBeInTheDocument();
  });

  // The headline and the strip are one card, so they are one figure and one
  // span. A window that opens before the household's history does used to state
  // the difference between a total that counted nothing and one that counted
  // everything — a "rise" the chart beneath it refuses to draw a bar for.
  it("states the change over the span the strip draws, in that span's words", () => {
    h.series.mockImplementation(() => ({
      ...SERIES,
      points: [
        {
          date: "2025-12-31",
          net_worth: "0.0000",
          missing: [{ account_id: "acct-1", name: "Checking", reason: "not_started" }],
        },
        { date: "2026-01-31", net_worth: "17372.2222", missing: [] },
        { date: "2026-09-26", net_worth: "40329.2412", missing: [] },
      ],
    }));
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    // Nothing is skipped over in the middle: every bar the strip draws between
    // the two named days adds up to the figure in front of them.
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent(
      `Up ${formatMoney("22957.0192", "USD")} from Jan 31 to Sep 26`,
    );
    // And the picture's own accessible name repeats the span, because a screen
    // reader gets the sentence and not the bars.
    expect(screen.getByTestId("accounts-net-worth-chart")).toHaveAccessibleName(
      /from Jan 31 to Sep 26/,
    );
  });

  it("stops the span where the strip's last bar is, when today's point starts counting", () => {
    h.series.mockImplementation(() => ({
      ...SERIES,
      points: [
        {
          date: "2026-01-31",
          net_worth: "100.00",
          missing: [{ account_id: "acct-2", name: "Savings", reason: "not_started" }],
        },
        {
          date: "2026-06-30",
          net_worth: "150.00",
          missing: [{ account_id: "acct-2", name: "Savings", reason: "not_started" }],
        },
        // Savings' first balance lands today: the interval into it is a start,
        // so the strip draws no bar there and the headline cannot span it.
        { date: "2026-09-26", net_worth: "1200.00", missing: [] },
      ],
    }));
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    // $50 — the movement the chart can measure — and not $1,100, which is mostly
    // an account appearing.
    expect(screen.getByTestId("net-worth-change")).toHaveTextContent(
      `Up ${formatMoney("50.00", "USD")} from Jan 31 to Jun 30`,
    );
  });

  it("names the single day it has when no interval in the window is drawable", () => {
    // Both points of the window are the two ends of one start: there is no span
    // to state, and "$0.00 over the past 2 weeks" would be a claim the chart
    // cannot make — the strip draws no bar in this window at all.
    h.series.mockImplementation(() => ({
      ...SERIES,
      points: [
        {
          date: "2026-06-01",
          net_worth: "40.00",
          missing: [{ account_id: "acct-2", name: "Savings", reason: "not_started" }],
        },
        { date: "2026-09-01", net_worth: "100.00", missing: [] },
      ],
    }));
    render(<MemoryRouter><Accounts /></MemoryRouter>);

    expect(screen.getByTestId("net-worth-change")).toHaveTextContent(
      `Up ${formatMoney("0.00", "USD")} on Sep 01`,
    );
    expect(screen.getByTestId("accounts-net-worth-coverage")).toHaveTextContent(
      "Counted partway through: Savings from Sep 01.",
    );
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
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <Accounts />
        </QueryClientProvider>
      </MemoryRouter>,
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

// A card's balance is stored signed (debt negative, ADR-0043) and read and typed as
// the amount owed. Before, the form stored what was typed as-is, so a hand-entered
// card and a synced one meant opposite things by the same number.
describe("liabilities", () => {
  const renderPage = () =>
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <Accounts />
        </QueryClientProvider>
      </MemoryRouter>,
    );

  it("shows a card's debt as the amount owed", () => {
    renderPage();
    expect(screen.getByTestId("account-balance-acct-card").textContent).toBe(
      `${formatMoney("850.0000")} owed`,
    );
  });

  it("stores the amount owed typed for a new card as a negative balance", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("add-account"));
    const form = screen.getByTestId("add-account-form");
    await user.type(within(form).getByTestId("account-name"), "Amex");
    await user.selectOptions(within(form).getByTestId("account-type"), "credit");
    expect(within(form).getByText("Amount owed")).toBeInTheDocument();
    await user.type(within(form).getByTestId("account-balance"), "850");
    await user.click(within(form).getByTestId("account-save"));
    expect(h.create.mock.calls[0][0]).toMatchObject({ type: "credit", current_balance: "-850" });
  });

  it("edits a card in amounts owed", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("account-edit-acct-card"));
    const balance = screen.getByTestId("edit-account-balance") as HTMLInputElement;
    expect(balance.value).toBe("850.0000");
    await user.clear(balance);
    await user.type(balance, "900");
    await user.click(screen.getByTestId("edit-account-save"));
    expect(h.update.mock.calls[0][0].body).toMatchObject({ current_balance: "-900" });
  });

  it("retypes an account without touching its balance", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("account-edit-acct-card"));
    await user.selectOptions(screen.getByTestId("edit-account-type"), "other");
    // Same stored balance, read the asset way now.
    expect((screen.getByTestId("edit-account-balance") as HTMLInputElement).value).toBe(
      "-850.0000",
    );
    await user.click(screen.getByTestId("edit-account-save"));
    const body = h.update.mock.calls[0][0].body as Record<string, unknown>;
    expect(body.type).toBe("other");
    expect(body).not.toHaveProperty("current_balance");
  });
});

describe("stale accounts", () => {
  it("marks an account the bank stopped reporting", () => {
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <Accounts />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("account-stale-acct-card")).toHaveTextContent("Not reported since");
    expect(screen.queryByTestId("account-stale-acct-1")).toBeNull();
  });
});

describe("balance history", () => {
  const wrap = (ui: React.ReactNode) =>
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
      </MemoryRouter>,
    );

  it("lists the recorded balances and saves a past one on its own day", async () => {
    const user = userEvent.setup();
    wrap(<Accounts />);
    await user.click(screen.getByTestId("account-edit-acct-1"));
    const history = screen.getByTestId("balance-history");
    expect(within(history).getByTestId("history-row-2025-12-31")).toHaveTextContent(
      formatMoney("80.00", "USD"),
    );
    await user.type(within(history).getByTestId("history-date"), "2025-06-30");
    await user.type(within(history).getByTestId("history-amount"), "42.50");
    await user.click(within(history).getByTestId("history-add"));
    expect(h.putBalance).toHaveBeenCalledWith(
      { accountId: "acct-1", date: "2025-06-30", balance: "42.50" },
      expect.anything(),
    );
  });

  it("takes a card's past balance as the amount owed", async () => {
    const user = userEvent.setup();
    wrap(<Accounts />);
    await user.click(screen.getByTestId("account-edit-acct-card"));
    const history = screen.getByTestId("balance-history");
    await user.type(within(history).getByTestId("history-date"), "2025-06-30");
    await user.type(within(history).getByTestId("history-amount"), "300");
    await user.click(within(history).getByTestId("history-add"));
    expect(h.putBalance.mock.calls[0][0].balance).toBe("-300");
  });

  it("removes a balance only after a second, explicit tap", async () => {
    const user = userEvent.setup();
    wrap(<Accounts />);
    await user.click(screen.getByTestId("account-edit-acct-1"));
    await user.click(screen.getByTestId("history-remove-2025-12-31"));
    expect(h.deleteBalance).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("history-remove-confirm-2025-12-31"));
    expect(h.deleteBalance).toHaveBeenCalledWith(
      { accountId: "acct-1", date: "2025-12-31" },
      expect.anything(),
    );
  });

  it("asks for a day before saving", async () => {
    const user = userEvent.setup();
    wrap(<Accounts />);
    await user.click(screen.getByTestId("account-edit-acct-1"));
    await user.type(screen.getByTestId("history-amount"), "10");
    await user.click(screen.getByTestId("history-add"));
    expect(screen.getByTestId("history-error")).toHaveTextContent("Pick the day");
    expect(h.putBalance).not.toHaveBeenCalled();
  });
});

describe("a bank that stopped sending", () => {
  it("says so above the net worth when syncs succeed but bring nothing new", () => {
    h.connections = [
      {
        id: "c1", org_name: "Chase", is_enabled: true, quiet_syncs: 6,
        last_new_data_at: new Date(Date.now() - 3 * 864e5).toISOString(),
      },
    ];
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.getByTestId("bank-stalled")).toHaveTextContent("Chase hasn’t sent new transactions");
    h.connections = [];
  });

  it("stays quiet for a quiet weekend", () => {
    h.connections = [
      {
        id: "c1", org_name: "Chase", is_enabled: true, quiet_syncs: 2,
        last_new_data_at: new Date(Date.now() - 20 * 36e5).toISOString(),
      },
    ];
    render(<MemoryRouter><Accounts /></MemoryRouter>);
    expect(screen.queryByTestId("bank-stalled")).not.toBeInTheDocument();
    h.connections = [];
  });
});
