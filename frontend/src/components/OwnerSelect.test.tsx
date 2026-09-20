import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Owner } from "@/api/types";
import OwnerSelect from "@/components/OwnerSelect";

// The picker owns its data (owners + inline create), so stub the hooks rather
// than standing up a QueryClient and a fetch mock for a component test.
const h = vi.hoisted(() => ({
  owners: [] as Owner[],
  mutate: vi.fn(),
}));

vi.mock("@/api/hooks", () => ({
  useOwners: () => ({ data: h.owners }),
  useCreateOwner: () => ({ mutate: h.mutate, isPending: false, isError: false, error: null }),
}));

const SHARED: Owner = { id: "shared-1", name: "Shared", kind: "shared", sort: 0 };
const ALICE: Owner = { id: "person-1", name: "Alice", kind: "person", sort: 1 };

beforeEach(() => {
  h.owners = [SHARED, ALICE];
  h.mutate.mockReset();
});

describe("<OwnerSelect />", () => {
  it("is labelled and lists the owners in server order", () => {
    render(<OwnerSelect value={null} onChange={vi.fn()} testid="detail-owner" />);
    const select = screen.getByLabelText("Owner");
    expect(select).toHaveAttribute("data-testid", "detail-owner");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Shared", "Alice"]);
  });

  it("offers an Inherit option when nullable and reports null for it", async () => {
    const onChange = vi.fn();
    render(
      <OwnerSelect value={ALICE.id} onChange={onChange} nullable testid="detail-owner" />,
    );
    expect(screen.getByRole("option", { name: "Inherit (Shared)" })).toBeInTheDocument();

    await userEvent.setup().selectOptions(screen.getByTestId("detail-owner"), "");
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("names what an inheriting row inherits from", () => {
    render(
      <OwnerSelect
        value={null}
        onChange={vi.fn()}
        nullable
        inheritFrom="Checking"
        testid="split-owner-0"
      />,
    );
    expect(screen.getByRole("option", { name: "Inherit (Checking)" })).toBeInTheDocument();
  });

  it("has no empty option when not nullable and falls back to Shared", () => {
    render(<OwnerSelect value={null} onChange={vi.fn()} testid="account-owner" />);
    expect(screen.queryByRole("option", { name: /Inherit/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("account-owner")).toHaveValue(SHARED.id);
  });

  it("creates an owner inline and selects it", async () => {
    const created: Owner = { id: "person-2", name: "Riley", kind: "person", sort: 2 };
    h.mutate.mockImplementation(
      (_body: unknown, opts: { onSuccess: (o: Owner) => void }) => opts.onSuccess(created),
    );
    const onChange = vi.fn();
    render(<OwnerSelect value={null} onChange={onChange} testid="account-owner" />);

    const user = userEvent.setup();
    await user.click(screen.getByTestId("account-owner-new"));
    await user.type(screen.getByTestId("account-owner-new-name"), "Riley");
    await user.click(screen.getByTestId("account-owner-new-save"));

    expect(h.mutate).toHaveBeenCalledWith({ name: "Riley" }, expect.anything());
    expect(onChange).toHaveBeenCalledWith(created.id);
  });

  it("hides the inline create control when asked", () => {
    render(
      <OwnerSelect value={null} onChange={vi.fn()} allowCreate={false} testid="split-owner-0" />,
    );
    expect(screen.queryByTestId("split-owner-0-new")).not.toBeInTheDocument();
  });
});
