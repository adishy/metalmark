import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Settings from "@/pages/Settings";

// Settings is a big page — most of its tabs never mount here. Only the
// Connections tab's own hooks (`@/api/sync`, plus `useHousehold` for the
// owner-only gate) need stubs; the point of this file is task A1's rename
// form, not the rest of the page.
const h = vi.hoisted(() => ({
  connections: [] as unknown[],
  update: vi.fn(),
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
    // The owner-only gate in `ConnectionsTab` reads this.
    useHousehold: () => settled({ role: "owner" }),
  };
});

vi.mock("@/api/sync", async () => {
  const actual = await vi.importActual<typeof import("@/api/sync")>("@/api/sync");
  const settled = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    isSuccess: true,
    mutate: vi.fn(),
  });
  return {
    ...actual,
    useConnections: () => settled(h.connections),
    useClaimConnection: () => settled(undefined),
    useDeleteConnection: () => settled(undefined),
    useTriggerSync: () => settled(undefined),
    useUpdateConnection: () => ({ ...settled(undefined), mutate: h.update }),
  };
});

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

async function openConnectionsTab() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Connections" }));
  return user;
}

beforeEach(() => {
  h.connections = [
    {
      id: "c1",
      provider: "simplefin",
      org_name: "First Federal",
      display_name: null,
      status: "ok",
      last_synced_at: null,
      last_error: null,
      is_enabled: true,
      sync_interval_minutes: 360,
      next_sync_at: null,
      created_at: "2026-09-01T00:00:00Z",
    },
  ];
  h.update.mockReset();
});

describe("renaming a connection", () => {
  it("shows the bank's name as the row title until a local name is set", async () => {
    renderSettings();
    await openConnectionsTab();
    expect(screen.getByTestId("conn-row-c1")).toHaveTextContent("First Federal");
    expect(screen.queryByTestId("conn-bank-name-c1")).not.toBeInTheDocument();
  });

  it("opens an inline form prefilled with the current name, and saves a trimmed value", async () => {
    renderSettings();
    const user = await openConnectionsTab();

    await user.click(screen.getByTestId("rename-c1"));
    const input = screen.getByTestId("rename-input-c1");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "First Federal");

    await user.type(input, "  Chase — joint  ");
    await user.click(screen.getByTestId("rename-save-c1"));

    expect(h.update).toHaveBeenCalledWith(
      { id: "c1", body: { display_name: "  Chase — joint  " } },
      expect.anything(),
    );
  });

  it("submits on Enter and cancels on Escape", async () => {
    renderSettings();
    const user = await openConnectionsTab();

    await user.click(screen.getByTestId("rename-c1"));
    await user.type(screen.getByTestId("rename-input-c1"), "Mine{Enter}");
    expect(h.update).toHaveBeenCalledTimes(1);

    // The mock mutation never calls back, so the form is still open (a real
    // save would have closed it on success) — exactly the state Escape should
    // still be able to back out of, without saving again.
    await user.type(screen.getByTestId("rename-input-c1"), "{Escape}");
    expect(screen.queryByTestId("rename-form-c1")).not.toBeInTheDocument();
    expect(h.update).toHaveBeenCalledTimes(1); // Escape did not save
  });

  it("offers Reset to bank name only once a local name is set, and clears it", async () => {
    h.connections = [{ ...h.connections[0], display_name: "Chase — joint" }];
    renderSettings();
    const user = await openConnectionsTab();

    // The bank's own name stays visible, small, underneath.
    expect(screen.getByTestId("conn-bank-name-c1")).toHaveTextContent("First Federal");

    await user.click(screen.getByTestId("rename-c1"));
    expect(screen.getByTestId("rename-input-c1")).toHaveValue("Chase — joint");

    await user.click(screen.getByTestId("rename-reset-c1"));
    expect(h.update).toHaveBeenCalledWith(
      { id: "c1", body: { display_name: "" } },
      expect.anything(),
    );
  });
});
