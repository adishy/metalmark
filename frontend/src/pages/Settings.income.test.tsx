import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Owner } from "@/api/types";
import Settings from "@/pages/Settings";

// The door to ADR-0052's data entry: Settings → Owners → "Income & pay". This
// file exists to prove the dialog is actually reachable from the page (it is
// mounted nowhere else) and that the button is on person owners only — the
// dialog's own behaviour lives in components/IncomeDialog.test.tsx.
const h = vi.hoisted(() => ({
  owners: [] as Owner[],
  updateProfile: vi.fn(),
  createPaystub: vi.fn(),
  updatePaystub: vi.fn(),
  deletePaystub: vi.fn(),
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
    useOwners: () => settled(h.owners),
  };
});

vi.mock("@/api/income", () => ({
  // The envelope is always present; an unstated profile is `profile: null`
  // inside it, not a missing response.
  useIncomeProfile: () => ({
    data: {
      owner_id: "person-1",
      profile: null,
      summary: { annualized_gross: null, ytd: [], effective_tax_rate: null },
    },
    isPending: false,
    isError: false,
    error: null,
  }),
  usePaystubs: () => ({ data: [], isPending: false, isError: false, error: null }),
  useUpdateIncomeProfile: () => ({
    mutate: h.updateProfile,
    isPending: false,
    isError: false,
    error: null,
  }),
  useCreatePaystub: () => ({
    mutate: h.createPaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
  useUpdatePaystub: () => ({
    mutate: h.updatePaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
  useDeletePaystub: () => ({
    mutate: h.deletePaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

const SHARED: Owner = { id: "shared-1", name: "Shared", kind: "shared", sort: 0 };
const ALICE: Owner = { id: "person-1", name: "Alice", kind: "person", sort: 1 };

function renderSettings() {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.owners = [SHARED, ALICE];
});

describe("Settings → Owners → Income & pay", () => {
  it("offers the income dialog on person owners only", async () => {
    renderSettings();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Owners" }));

    expect(screen.getByTestId(`owner-income-${ALICE.id}`)).toHaveTextContent("Income & pay");
    expect(screen.queryByTestId(`owner-income-${SHARED.id}`)).not.toBeInTheDocument();
  });

  it("opens the dialog for the owner whose button was pressed, and closes it", async () => {
    renderSettings();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Owners" }));
    expect(screen.queryByTestId("income-dialog")).not.toBeInTheDocument();

    await user.click(screen.getByTestId(`owner-income-${ALICE.id}`));
    const dialog = screen.getByTestId("income-dialog");
    expect(dialog).toHaveTextContent("Income & pay — Alice");

    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByTestId("income-dialog")).not.toBeInTheDocument();
  });
});
