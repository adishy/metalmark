import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { RecurringList, RecurringSuggestion, Account, Category, CategoryGroup } from "@/api/types";
import Recurring from "@/pages/insights/Recurring";

// The tab owns its calls through `@/api/recurring`, so they are stubbed — the
// mutate payloads are the assertions, because what the forms build *is* the
// contract with the server (ADR-0053). The list hook records the filter it was
// called with, which is how the filter row is tested without a fake server.
const h = vi.hoisted(() => ({
  list: { items: [], totals: [] } as unknown,
  suggestions: [] as unknown[],
  useRecurring: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/api/recurring", () => ({
  useRecurring: (f: unknown) => h.useRecurring(f),
  useRecurringSuggestions: () => ({ data: h.suggestions, isPending: false, isError: false, error: null }),
  useCreateRecurring: () => ({ mutate: h.create, isPending: false, isError: false, error: null }),
  useUpdateRecurring: () => ({ mutate: h.update, isPending: false, isError: false, error: null }),
  useDeleteRecurring: () => ({ mutate: h.delete, isPending: false, isError: false, error: null }),
}));

const ACCOUNTS: Account[] = [
  {
    id: "acct-1",
    name: "Checking",
    type: "depository",
    currency: "USD",
    subtype: null,
    institution: null,
    current_balance: "1000.00",
    balance_date: null,
    is_asset: true,
  } as Account,
];

const CATEGORIES: Category[] = [
  { id: "cat-1", name: "Subscriptions", icon: "📺", group_id: "grp-1", sort: 0 } as Category,
];

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  const settled = (data: unknown) => ({ data, isPending: false, isError: false, error: null });
  return {
    ...actual,
    useAccounts: () => settled(ACCOUNTS),
    useCategories: () => settled(CATEGORIES),
    useCategoryGroups: () => settled([] as CategoryGroup[]),
  };
});

const SERIES = {
  id: "series-1",
  name: "Netflix",
  merchant: "NETFLIX.COM",
  account_id: "acct-1",
  category_id: "cat-1",
  amount: "-12.9900",
  currency: "USD",
  cadence: "monthly" as const,
  next_due_date: "2026-10-05",
  is_active: true,
  transaction_id: "txn-1",
  monthly_amount: "-12.99",
  occurrences: 4,
  last_seen_date: "2026-09-05",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const LIST: RecurringList = {
  items: [SERIES],
  totals: [{ currency: "USD", monthly_in: "0.00", monthly_out: "-12.99", net_monthly: "-12.99" }],
};

const SUGGESTION: RecurringSuggestion = {
  name: "Supermarket",
  merchant: null,
  account_id: "acct-1",
  category_id: null,
  amount: "-420.0000",
  currency: "USD",
  cadence: "monthly",
  monthly_amount: "-420.00",
  occurrences: 3,
  first_date: "2026-01-05",
  last_date: "2026-03-05",
  next_due_date: "2026-04-04",
  transaction_id: "txn-9",
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Recurring />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.list = LIST;
  h.suggestions = [];
  h.useRecurring = vi.fn(() => ({
    data: h.list,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }));
  h.create.mockReset();
  h.update.mockReset();
  h.delete.mockReset();
});

describe("the recurring tab", () => {
  it("lists what is tracked, with the totals it adds up to", () => {
    renderTab();
    const row = screen.getByTestId("recurring-row-series-1");
    expect(within(row).getByText(/Netflix/)).toBeTruthy();
    // The amount a person pays, and the monthly figure the cadence makes it —
    // the second is why a weekly bill and a yearly one can sit in one list.
    expect(within(row).getByText("−$12.99")).toBeTruthy();
    expect(within(row).getByText(/−\$12\.99 a month/)).toBeTruthy();
    expect(within(row).getByText(/4 seen/)).toBeTruthy();
    // Named references render as names, never ids.
    expect(within(row).getByText(/Checking/)).toBeTruthy();
    expect(within(row).getByText(/📺 Subscriptions/)).toBeTruthy();
    expect(screen.getByTestId("totals-out-USD").textContent).toBe("−$12.99");
  });

  it("says so when nothing is tracked yet", () => {
    h.list = { items: [], totals: [] };
    renderTab();
    expect(screen.getByTestId("recurring-empty")).toBeTruthy();
  });

  it("adds a series, sending the amount in the ledger's own sign", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("add-recurring"));
    await user.type(screen.getByTestId("recurring-name"), "Rent");
    await user.type(screen.getByTestId("recurring-amount"), "1500.00");
    await user.selectOptions(screen.getByTestId("recurring-cadence"), "monthly");
    await user.click(screen.getByTestId("recurring-save"));

    expect(h.create).toHaveBeenCalledTimes(1);
    expect(h.create.mock.calls[0][0]).toMatchObject({
      name: "Rent",
      // Typed as a magnitude, stored negative: nobody writes a minus sign to
      // say "this is a bill" (ADR-0043).
      amount: "-1500.00",
      cadence: "monthly",
      transaction_id: null,
      is_active: true,
    });
  });

  it("refuses to save without a name or an amount", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("add-recurring"));
    await user.click(screen.getByTestId("recurring-save"));
    expect(h.create).not.toHaveBeenCalled();
    expect(screen.getByText(/Give it a name/)).toBeTruthy();
    expect(screen.getByText(/Required/)).toBeTruthy();
  });

  it("edits a series in place, sending only what the form says", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("recurring-edit-series-1"));
    const amount = screen.getByTestId("recurring-amount");
    await user.clear(amount);
    await user.type(amount, "15.99");
    await user.click(screen.getByTestId("recurring-save"));

    expect(h.update.mock.calls[0][0]).toMatchObject({
      id: "series-1",
      body: { name: "Netflix", amount: "-15.99", cadence: "monthly", is_active: true },
    });
    expect(h.create).not.toHaveBeenCalled();
  });

  it("pauses and resumes from the row", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("recurring-toggle-series-1"));
    expect(h.update.mock.calls[0][0]).toEqual({ id: "series-1", body: { is_active: false } });

    h.list = { ...LIST, items: [{ ...SERIES, is_active: false }] };
    renderTab();
    expect(screen.getAllByText("Paused").length).toBeGreaterThan(0);
  });

  it("asks before deleting, and says what deleting leaves alone", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("recurring-delete-series-1"));
    expect(h.delete).not.toHaveBeenCalled();
    expect(screen.getByText(/transactions it matched are not touched/)).toBeTruthy();
    await user.click(screen.getByTestId("recurring-delete-confirm-series-1"));
    expect(h.delete.mock.calls[0][0]).toBe("series-1");
  });

  it("offers what the ledger already repeats, and tracks it from the suggestion", async () => {
    const user = userEvent.setup();
    h.suggestions = [SUGGESTION];
    renderTab();
    const card = screen.getByTestId("recurring-suggestions");
    expect(within(card).getByText(/Supermarket/)).toBeTruthy();
    await user.click(screen.getByTestId("suggestion-add-txn-9"));
    // Seeded from the transaction it was picked from, so the server can fill
    // whatever the body leaves out.
    expect(h.create.mock.calls[0][0]).toEqual({ transaction_id: "txn-9", cadence: "monthly" });
  });

  it("opens the editor on a suggestion, seeded, instead of saving blind", async () => {
    const user = userEvent.setup();
    h.suggestions = [SUGGESTION];
    renderTab();
    await user.click(screen.getByTestId("suggestion-edit-txn-9"));
    expect(screen.getByTestId("recurring-name")).toHaveValue("Supermarket");
    expect(screen.getByTestId("recurring-amount")).toHaveValue("420.0000");
    await user.click(screen.getByTestId("recurring-save"));
    expect(h.create.mock.calls[0][0]).toMatchObject({
      transaction_id: "txn-9",
      name: "Supermarket",
      amount: "-420.0000",
      cadence: "monthly",
    });
  });

  it("narrows the request rather than the rendered list", async () => {
    const user = userEvent.setup();
    renderTab();
    // Every render asks for the unfiltered list first: "any" is the absence of
    // a value, not a value the server has to know about.
    expect(h.useRecurring.mock.calls.at(-1)?.[0]).toEqual({
      account_id: null,
      category_id: null,
      is_active: null,
      direction: null,
      q: null,
    });

    await user.click(screen.getByTestId("recurring-account"));
    await user.click(screen.getByTestId("recurring-account-option-acct-1"));
    expect(h.useRecurring.mock.calls.at(-1)?.[0]).toMatchObject({ account_id: "acct-1" });

    await user.click(screen.getByTestId("recurring-status"));
    await user.click(screen.getByTestId("recurring-status-option-paused"));
    expect(h.useRecurring.mock.calls.at(-1)?.[0]).toMatchObject({ is_active: false });

    await user.type(screen.getByTestId("recurring-search"), "net");
    expect(h.useRecurring.mock.calls.at(-1)?.[0]).toMatchObject({ q: "net" });
  });

  it("hides the ledger's suggestions while a filter is on", async () => {
    const user = userEvent.setup();
    h.suggestions = [SUGGESTION];
    renderTab();
    expect(screen.getByTestId("recurring-suggestions")).toBeTruthy();
    await user.click(screen.getByTestId("recurring-kind"));
    await user.click(screen.getByTestId("recurring-kind-option-out"));
    // They are the whole ledger's patterns; beside a narrowed list they would
    // read as if they matched it.
    expect(screen.queryByTestId("recurring-suggestions")).toBeNull();
  });
});
