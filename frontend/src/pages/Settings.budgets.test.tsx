import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { BudgetReport, Category, CategoryGroup } from "@/api/types";
import Settings from "@/pages/Settings";

// Settings → Categories is the *complete* list of categories, and therefore the
// only place a plan can be written for a category with nothing spent on it yet
// (ADR-0058). This file proves the plan line is there, that it is expense-only,
// and that it opens the same dialog the Insights → Budgets tab opens — the
// dialog's own behaviour lives in the Budgets tab's test.
const h = vi.hoisted(() => ({
  groups: [] as CategoryGroup[],
  categories: [] as Category[],
  report: { data: undefined } as unknown,
  set: vi.fn(),
  clear: vi.fn(),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ me: { user: { is_admin: false }, role: "owner" } }),
}));

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  const settled = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: true,
  });
  return {
    ...actual,
    useHousehold: () => settled({ role: "owner", base_currency: "USD" }),
    useCategoryGroups: () => settled(h.groups),
    useCategories: () => settled(h.categories),
    useBudgetReport: () => h.report,
    useSetBudget: () => ({ mutate: h.set, isPending: false, isError: false, error: null }),
    useClearBudget: () => ({ mutate: h.clear, isPending: false, isError: false, error: null }),
  };
});

const GROUPS: CategoryGroup[] = [
  { id: "grp-expense", name: "Living", type: "expense", sort: 0 },
  { id: "grp-income", name: "Earned", type: "income", sort: 1 },
];

const CATEGORIES: Category[] = [
  { id: "cat-food", group_id: "grp-expense", name: "Groceries", icon: "🛒", color: null, sort: 0 },
  { id: "cat-fun", group_id: "grp-expense", name: "Fun", icon: null, color: null, sort: 1 },
  { id: "cat-pay", group_id: "grp-income", name: "Paycheck", icon: null, color: null, sort: 0 },
];

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
  ],
  total_budget: "400.0000",
  total_spent: "450.0000",
  budgeted_spent: "450.0000",
  unbudgeted_spent: "0.0000",
  attribution: "row",
  warnings: [],
};

function renderCategories() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings?tab=categories"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.groups = GROUPS;
  h.categories = CATEGORIES;
  h.report = { data: REPORT, isPending: false, isError: false, error: null, refetch: vi.fn() };
  h.set.mockReset();
  h.clear.mockReset();
});

describe("the categories list", () => {
  it("says what each expense category is planned for this month", () => {
    renderCategories();
    expect(screen.getByTestId("category-budget-cat-food").textContent).toBe(
      "$400.00 budgeted for Sep 2026",
    );
    // No plan is a state the list must state too, or the reader cannot tell a
    // category that has none from one whose report has not loaded.
    expect(screen.getByTestId("category-budget-cat-fun").textContent).toBe("No budget for Sep 2026");
  });

  it("offers no plan on an income category", () => {
    renderCategories();
    // `spending_by_category` counts money out, and the server refuses a plan on
    // an income category: a control that could only ever 422 is worse than no
    // control.
    expect(screen.queryByTestId("category-budget-cat-pay")).toBeNull();
  });

  it("writes the plan through the same dialog the Budgets tab uses, for the month on screen", async () => {
    const user = userEvent.setup();
    renderCategories();
    await user.click(screen.getByTestId("category-budget-cat-food"));
    expect(screen.getByText("Budget — Groceries")).toBeTruthy();
    const amount = screen.getByTestId("budget-amount-cat-food") as HTMLInputElement;
    expect(amount.value).toBe("400.0000");
    await user.clear(amount);
    await user.type(amount, "500");
    await user.click(screen.getByTestId("budget-save-cat-food"));
    expect(h.set).toHaveBeenCalledWith(
      { categoryId: "cat-food", period: "2026-09-01", amount: "500" },
      expect.anything(),
    );
  });

  it("clears a plan only from inside the dialog", async () => {
    const user = userEvent.setup();
    renderCategories();
    // Nothing on the row itself writes: the row states the plan, the dialog
    // changes it, and "clear" is one deliberate press inside that dialog.
    await user.click(screen.getByTestId("category-budget-cat-food"));
    const dialog = screen.getByTestId("budget-dialog-cat-food");
    await user.click(within(dialog).getByTestId("budget-clear-cat-food"));
    expect(h.clear).toHaveBeenCalledWith(
      { categoryId: "cat-food", period: "2026-09-01" },
      expect.anything(),
    );
    // Clearing is offered only where there is something to clear: Fun has no
    // plan, so it has no clear button.
    expect(screen.queryByTestId("budget-clear-cat-fun")).toBeNull();
  });

  it("draws no plan line at all until the report has loaded", () => {
    h.report = { data: undefined, isPending: true, isError: false, error: null, refetch: vi.fn() };
    renderCategories();
    expect(screen.queryByTestId("category-budget-cat-food")).toBeNull();
    // The list itself is unaffected — a budget is a line on a row, not a gate
    // on the page.
    expect(screen.getByTestId("category-edit-cat-food")).toBeTruthy();
  });
});
