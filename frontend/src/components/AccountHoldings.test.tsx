import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Holding } from "@/api/types";
import AccountHoldings from "@/components/AccountHoldings";

configure({ asyncUtilTimeout: 10000 });
vi.setConfig({ testTimeout: 20000 });

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }));
vi.mock("@/api/client", () => ({ api: mocks }));
vi.mock("@/api/investments", () => ({ usePortfolio: () => ({ data: { accounts: [] } }) }));
const account = { id: "account", type: "investment", currency: "USD" } as Account;
const holding: Holding = {
  id: "holding", account_id: "account", security_id: "security",
  security: { id: "security", name: "Apple", ticker: "AAPL", security_type: "stock", currency: "USD", is_manual: false },
  quantity: "10", cost_basis: "50", quantity_source: "provider", basis_source: "provider",
  manual_quantity: "10", manual_cost_basis: "50", as_of: "2026-10-01", is_override: false,
};
function show(canEdit = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AccountHoldings account={account} canEdit={canEdit} /></QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); mocks.get.mockResolvedValue([holding]); mocks.patch.mockResolvedValue(holding); mocks.del.mockResolvedValue(undefined); });

describe("account holdings", () => {
  it("edits a bank holding with account-local metadata and decimal strings", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByText(/stays yours, and the bank keeps updating the rest/)).toBeVisible();
    expect(screen.getByText(/Changing the quantity, cost basis or date stops bank updates/)).toBeVisible();
    await user.clear(screen.getByRole("textbox", { name: "Name" })); await user.type(screen.getByRole("textbox", { name: "Name" }), "My Apple");
    await user.type(screen.getByLabelText("Market value · USD"), "123.45");
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    // Only what changed: an untouched quantity, basis or date would stop the bank's.
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/investments/holdings/holding", { name: "My Apple", market_value: "123.45" }));
  });
  it("starts the date from the holding's own and sends quantity, basis and date only when changed", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Quantity as of")).toHaveValue("2026-10-01");
    await user.clear(screen.getByRole("spinbutton", { name: "Quantity" })); await user.type(screen.getByRole("spinbutton", { name: "Quantity" }), "12");
    await user.clear(screen.getByRole("textbox", { name: "Symbol" }));
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/investments/holdings/holding", { quantity: "12", ticker: null }));
    mocks.patch.mockClear();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Quantity as of")); await user.type(screen.getByLabelText("Quantity as of"), "2026-10-03");
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/investments/holdings/holding", { as_of: "2026-10-03" }));
  });
  it("adds a holding without local labels that only repeat the security's own", async () => {
    const security = { id: "sec-vti", name: "Vanguard Total", ticker: "VTI", security_type: "etf", currency: "USD", is_manual: true };
    mocks.get.mockImplementation((url: string) => Promise.resolve(url === "/investments/securities" ? [security] : []));
    mocks.post.mockResolvedValue(holding);
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Add holding" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Vanguard Total");
    await user.type(screen.getByRole("textbox", { name: "Symbol" }), "vti");
    await user.selectOptions(screen.getByLabelText("Type"), "etf");
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenCalledTimes(1);
    const [url, body] = mocks.post.mock.calls[0];
    expect(url).toBe("/investments/holdings");
    expect(body).toMatchObject({ account_id: "account", security_id: "sec-vti", quantity: "1", cost_basis: null });
    expect(body.as_of).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    for (const key of ["name", "ticker", "security_type", "market_value"]) expect(body).not.toHaveProperty(key);
  });
  it("creates a symbol-less security with a null ticker and keeps a differing local name", async () => {
    const created = { id: "sec-new", name: "Family LLC", ticker: null, security_type: "other", currency: "USD", is_manual: true };
    mocks.get.mockImplementation((url: string) => Promise.resolve(url === "/investments/securities" ? [] : []));
    mocks.post.mockImplementation((url: string) => Promise.resolve(url === "/investments/securities" ? created : holding));
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Add holding" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Family LLC");
    await user.selectOptions(screen.getByLabelText("Type"), "other");
    await user.type(screen.getByLabelText("Market value · USD"), "5000");
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[0]).toEqual(["/investments/securities", { name: "Family LLC", ticker: null, security_type: "other", currency: "USD" }]);
    const body = mocks.post.mock.calls[1][1];
    expect(body.market_value).toBe("5000");
    for (const key of ["name", "ticker", "security_type"]) expect(body).not.toHaveProperty(key);
  });
  it("says when a value was set by hand, on bank and manual rows alike", async () => {
    mocks.get.mockResolvedValue([
      { ...holding, market_value_override: "123.4500", market_value_override_as_of: "2026-10-02" },
      { ...holding, id: "h2", security_id: "s2", quantity_source: "manual", market_value_override: "9.0000", market_value_override_as_of: "2026-09-30" },
      { ...holding, id: "h3", security_id: "s3", quantity_source: "manual" },
    ]);
    show();
    expect(await screen.findByTestId("holding-pinned-security")).toHaveTextContent("Value set by you");
    expect(screen.getByTestId("holding-pinned-security").querySelector("time")).toHaveAttribute("datetime", "2026-10-02");
    expect(screen.getByTestId("holding-pinned-s2").querySelector("time")).toHaveAttribute("datetime", "2026-09-30");
    expect(screen.queryByTestId("holding-pinned-s3")).not.toBeInTheDocument();
  });
  it("requires confirmation before deleting", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    expect(mocks.del).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(mocks.del).toHaveBeenCalledWith("/investments/holdings/holding"));
  });
  it("edits history-backed metadata without writing trade-owned quantities or an unknown value", async () => {
    mocks.get.mockResolvedValue([{ ...holding, quantity_source: "history", basis_source: "history" }]);
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByRole("spinbutton", { name: "Quantity" })).toBeDisabled();
    expect(screen.getByLabelText("Total cost basis · USD")).toBeDisabled();
    await user.clear(screen.getByRole("textbox", { name: "Name" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Trust shares");
    await user.click(screen.getByRole("button", { name: "Save holding" }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalled());
    const body = mocks.patch.mock.calls[0][1];
    expect(body.name).toBe("Trust shares");
    expect(body).not.toHaveProperty("quantity");
    expect(body).not.toHaveProperty("cost_basis");
    expect(body).not.toHaveProperty("market_value");
  });
  it("hides editing actions for a viewer", async () => {
    show(false); await screen.findByText("Apple · AAPL");
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add holding" })).not.toBeInTheDocument();
  });
});
