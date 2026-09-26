import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Category, CategoryGroup } from "@/api/types";
import CategoryPicker, { categoryLabel } from "@/components/CategoryPicker";

/** `useIsPhone` reads `window.matchMedia`, which jsdom doesn't implement —
 *  it silently reports "not phone" everywhere unless a test stubs it. */
function stubPhone(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) => ({
      matches,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  );
}

const GROUPS: CategoryGroup[] = [
  { id: "g-inc", name: "Income", type: "income", sort: 0 },
  { id: "g-xfer", name: "Transfers", type: "transfer", sort: 1 },
  { id: "g-food", name: "Food & Dining", type: "expense", sort: 2 },
];
const CATS: Category[] = [
  { id: "c-pay", group_id: "g-inc", name: "Paychecks", icon: "💵", color: null, sort: 0 },
  { id: "c-xfer", group_id: "g-xfer", name: "Transfer", icon: "🔁", color: null, sort: 0 },
  { id: "c-groc", group_id: "g-food", name: "Groceries", icon: "🛒", color: null, sort: 0 },
];

function renderPicker(amount: string, onPick = vi.fn()) {
  render(
    <CategoryPicker
      open
      onClose={() => {}}
      categories={CATS}
      groups={GROUPS}
      value={null}
      amount={amount}
      onPick={onPick}
    />,
  );
  return onPick;
}

describe("<CategoryPicker />", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("writes a category with its emoji", () => {
    expect(categoryLabel(CATS[2])).toBe("🛒 Groceries");
    expect(categoryLabel(null)).toBe("Uncategorized");
  });

  it("lists expense and transfer groups first for money out", () => {
    renderPicker("-12.00");
    const dialog = screen.getByTestId("category-picker");
    const headings = within(dialog).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    // Income is still reachable, under "Other categories", but not first.
    expect(headings.slice(0, 2)).toEqual(["Transfers", "Food & Dining"]);
    expect(within(dialog).getByText("Other categories")).toBeInTheDocument();
  });

  it("lists income first for money in", () => {
    renderPicker("3000");
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings[0]).toBe("Income");
  });

  it("searches, and picks", async () => {
    const user = userEvent.setup();
    const onPick = renderPicker("-12.00");
    await user.type(screen.getByTestId("category-picker-search"), "groc");
    expect(screen.queryByText("Transfer")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("category-picker-option-c-groc"));
    expect(onPick).toHaveBeenCalledWith("c-groc");
  });

  it("can clear the category", async () => {
    const user = userEvent.setup();
    const onPick = renderPicker("-12.00");
    await user.click(screen.getByTestId("category-picker-none"));
    expect(onPick).toHaveBeenCalledWith(null);
  });

  it("says what to do when nothing matches", async () => {
    const user = userEvent.setup();
    renderPicker("-12.00");
    await user.type(screen.getByTestId("category-picker-search"), "zzz");
    expect(screen.getByText(/No categories match/)).toHaveTextContent("Settings → Categories");
  });

  it("keeps the phone sheet's height fixed regardless of the query", async () => {
    stubPhone(true);
    const user = userEvent.setup();
    renderPicker("-12.00");
    const body = screen.getByTestId("category-picker-body");
    const heightClass = "h-[min(70dvh,36rem)]";
    // No query, one match, and no match at all — the same class throughout,
    // so it's the list's content that changes, never the sheet's height.
    expect(body.className).toContain(heightClass);
    await user.type(screen.getByTestId("category-picker-search"), "groc");
    expect(body.className).toContain(heightClass);
    await user.clear(screen.getByTestId("category-picker-search"));
    await user.type(screen.getByTestId("category-picker-search"), "zzz");
    expect(body.className).toContain(heightClass);
  });

  it("does not fix the height on the desktop modal", () => {
    stubPhone(false);
    renderPicker("-12.00");
    expect(screen.getByTestId("category-picker-body").className).not.toContain("h-[min(70dvh,36rem)]");
  });
});
