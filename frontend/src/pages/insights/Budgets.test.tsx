import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { BudgetReport } from "@/api/types";
import Budgets, { addMonths } from "@/pages/insights/Budgets";

// The tab owns its calls through `@/api/hooks`, so they are stubbed. The stub
// records the period it was called with, which is how "stepping a month asks the
// server for that month" is asserted without a fake server, and the mutate
// payloads *are* the contract with the API (the amount and the period are what
// the route takes).
const h = vi.hoisted(() => ({
  report: vi.fn(),
  set: vi.fn(),
  clear: vi.fn(),
}));

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  return {
    ...actual,
    useBudgetReport: (period: string | null) => h.report(period),
    useSetBudget: () => ({ mutate: h.set, isPending: false, isError: false, error: null }),
    useClearBudget: () => ({ mutate: h.clear, isPending: false, isError: false, error: null }),
  };
});

function query(data: BudgetReport | undefined, over: Record<string, unknown> = {}) {
  return {
    data,
    isPending: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  };
}

/** September with one of each state a row can be in, and one total that is the
 *  sum of them: 450 spent against a 400 plan, 50 with no plan at all. */
const REPORT: BudgetReport = {
  base_currency: "USD",
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  rows: [
    {
      category_id: "cat-food",
      category_name: "Groceries",
      category_icon: "🛒",
      budget: "400.0000",
      spent: "450.0000",
    },
    {
      category_id: "cat-rent",
      category_name: "Rent",
      category_icon: null,
      budget: "2000.0000",
      spent: "0.0000",
    },
    {
      category_id: "cat-fun",
      category_name: "Fun",
      category_icon: null,
      budget: null,
      spent: "50.0000",
    },
  ],
  total_budget: "2400.0000",
  total_spent: "500.0000",
  budgeted_spent: "450.0000",
  unbudgeted_spent: "50.0000",
  attribution: "row",
  warnings: [],
};

function renderTab(search = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/insights/budgets${search}`]}>
        <Budgets />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.report.mockReset();
  h.set.mockReset();
  h.clear.mockReset();
  // The stub echoes the period it was asked for, as the server does: the tab
  // draws the period the *response* names, and steps from it, so a stub that
  // always answered "September" would make the stepper look broken when it is
  // only following the answer.
  h.report.mockImplementation((period: string | null) =>
    query({ ...REPORT, period_start: period ?? REPORT.period_start }),
  );
});

describe("the budgets tab", () => {
  it("names the period it is showing, and asks for the current month when the URL does not", () => {
    renderTab();
    // No `?period=`, so the hook is asked for null and the server resolves it.
    expect(h.report).toHaveBeenCalledWith(null);
    expect(screen.getByTestId("budgets-period").textContent).toBe("Sep 2026");
  });

  it("states the window on every figure that could be read as a bare number", () => {
    renderTab();
    // The four totals: the plan and the spend are only meaningful inside a month,
    // so the two that could be mistaken for all-time figures carry it.
    expect(screen.getByText("Spent · Sep 2026")).toBeTruthy();
    expect(screen.getByText("Planned")).toBeTruthy();
    expect(screen.getByTestId("budgets-total-spent").textContent).toBe("$500.00");
    expect(screen.getByTestId("budgets-total-budget").textContent).toBe("$2,400.00");
    // The two halves of the spend, so a reader can see how much of the month had
    // a plan behind it at all.
    expect(screen.getByTestId("budgets-total-budgeted").textContent).toBe("$450.00");
    expect(screen.getByTestId("budgets-total-unbudgeted").textContent).toBe("$50.00");
  });

  it("says by how much a category is over, not just that it is", () => {
    renderTab();
    const row = screen.getByTestId("budget-row-cat-food");
    expect(within(row).getByText(/🛒 Groceries/)).toBeTruthy();
    expect(within(row).getByTestId("budget-spent-cat-food").textContent).toBe("$450.00");
    expect(within(row).getByTestId("budget-planned-cat-food").textContent).toBe("$400.00");
    expect(within(row).getByText(/planned for Sep 2026/)).toBeTruthy();
    // 450 − 400, taken in minor units: a float subtraction is a cent away from
    // lying, and "over by $49.99" is the wrong sentence.
    expect(within(row).getByTestId("budget-remaining-cat-food").textContent).toBe(
      "Over by $50.00 this month.",
    );
  });

  it("does not call an unspent plan an underspend", () => {
    renderTab();
    const row = screen.getByTestId("budget-row-cat-rent");
    expect(within(row).getByTestId("budget-spent-cat-rent").textContent).toBe("$0.00");
    expect(within(row).getByTestId("budget-remaining-cat-rent").textContent).toBe(
      "Nothing spent yet this month.",
    );
  });

  it("says a category has no plan rather than drawing an empty bar for it", () => {
    renderTab();
    const row = screen.getByTestId("budget-row-cat-fun");
    expect(within(row).getByTestId("budget-noplan-cat-fun").textContent).toMatch(
      /No plan for Sep 2026/,
    );
    // Nothing to measure against, so there is nothing to draw: an empty bar
    // would read as "under budget by everything".
    expect(within(row).queryByTestId("budget-bar-cat-fun")).toBeNull();
    expect(within(row).getByTestId("budget-edit-cat-fun").textContent).toBe("Set a budget");
  });

  it("writes the plan for the month on screen, in the month's own period key", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("budget-edit-cat-fun"));
    expect(screen.getByText("Budget — Fun")).toBeTruthy();
    await user.type(screen.getByTestId("budget-amount-cat-fun"), "150");
    await user.click(screen.getByTestId("budget-save-cat-fun"));

    expect(h.set).toHaveBeenCalledTimes(1);
    expect(h.set.mock.calls[0][0]).toEqual({
      categoryId: "cat-fun",
      period: "2026-09-01",
      amount: "150",
    });
  });

  it("opens a plan dialog clean, and refuses an empty amount on Save", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("budget-edit-cat-fun"));
    const dialog = screen.getByTestId("budget-dialog-cat-fun");
    // A category with no plan opens with an empty field — and an error under a
    // field nobody has typed in yet is a reprimand for opening the dialog.
    expect(within(dialog).queryByRole("alert")).toBeNull();
    await user.click(screen.getByTestId("budget-save-cat-fun"));
    expect(within(dialog).getByRole("alert").textContent).toMatch(/Required/);
    expect(h.set).not.toHaveBeenCalled();
  });

  it("opens the dialog seeded with the stored plan, and clears it explicitly", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByTestId("budget-edit-cat-food"));
    const amount = screen.getByTestId("budget-amount-cat-food") as HTMLInputElement;
    expect(amount.value).toBe("400.0000");
    // The spend goes beside the field, because a plan is only readable next to
    // what has been spent against it — and this is where one is written.
    expect(screen.getByText(/Spent against it so far: \$450\.00\./)).toBeTruthy();
    await user.click(screen.getByTestId("budget-clear-cat-food"));
    expect(h.clear).toHaveBeenCalledWith(
      { categoryId: "cat-food", period: "2026-09-01" },
      expect.anything(),
    );
  });

  it("steps a month at a time through the URL, and comes back to this month", async () => {
    const user = userEvent.setup();
    renderTab("?period=2026-10-01");
    expect(h.report).toHaveBeenLastCalledWith("2026-10-01");
    await user.click(screen.getByTestId("budgets-prev"));
    expect(h.report).toHaveBeenLastCalledWith("2026-09-01");
    await user.click(screen.getByTestId("budgets-next"));
    await user.click(screen.getByTestId("budgets-next"));
    expect(h.report).toHaveBeenLastCalledWith("2026-11-01");
    // "This month" removes the param rather than computing a month here: the
    // server is what knows which month it is.
    await user.click(screen.getByTestId("budgets-this-month"));
    expect(h.report).toHaveBeenLastCalledWith(null);
  });

  it("shows a dropped flow beside the numbers it affects", () => {
    h.report.mockImplementation(() =>
      query({ ...REPORT, warnings: ["1 transfer was dropped from this period."] }),
    );
    renderTab();
    expect(screen.getByTestId("budgets-warning").textContent).toMatch(/dropped/);
  });

  it("offers where plans come from when there are none and nothing is spent", () => {
    h.report.mockImplementation(() =>
      query({
        ...REPORT,
        rows: [],
        total_budget: "0.0000",
        total_spent: "0.0000",
        budgeted_spent: "0.0000",
        unbudgeted_spent: "0.0000",
      }),
    );
    renderTab();
    expect(screen.getByTestId("budgets-empty").textContent).toMatch(/Settings → Categories/);
    expect(screen.queryByTestId("budgets-rows")).toBeNull();
  });

  it("says nothing is planned when spending exists but no budget does", () => {
    h.report.mockImplementation(() =>
      query({
        ...REPORT,
        rows: [REPORT.rows[2]],
        total_budget: "0.0000",
        total_spent: "50.0000",
        budgeted_spent: "0.0000",
        unbudgeted_spent: "50.0000",
      }),
    );
    renderTab();
    expect(screen.getByTestId("budgets-no-plans").textContent).toMatch(
      /Nothing is planned for Sep 2026/,
    );
  });
});

describe("addMonths", () => {
  it("crosses years in both directions, and never carries the day", () => {
    expect(addMonths("2026-09-01", 1)).toBe("2026-10-01");
    expect(addMonths("2026-12-01", 1)).toBe("2027-01-01");
    expect(addMonths("2026-01-01", -1)).toBe("2025-12-01");
    expect(addMonths("2026-03-01", -1)).toBe("2026-02-01");
  });
});
