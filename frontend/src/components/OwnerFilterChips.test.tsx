import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Owner } from "@/api/types";
import OwnerFilterChips from "@/components/OwnerFilterChips";

const SHARED: Owner = { id: "shared-1", name: "Shared", kind: "shared", sort: 0 };
const ALICE: Owner = { id: "person-1", name: "Alice", kind: "person", sort: 1 };

describe("<OwnerFilterChips />", () => {
  it("renders All plus one chip per owner, by name", () => {
    render(<OwnerFilterChips owners={[SHARED, ALICE]} value={null} onChange={vi.fn()} />);
    expect(screen.getByTestId("owner-filter-all")).toHaveTextContent("All");
    expect(screen.getByTestId("owner-filter-shared-1")).toHaveTextContent("Shared");
    expect(screen.getByTestId(`owner-filter-${ALICE.id}`)).toHaveTextContent("Alice");
  });

  it("reports the owner id when a chip is picked", async () => {
    const onChange = vi.fn();
    render(<OwnerFilterChips owners={[SHARED, ALICE]} value={null} onChange={onChange} />);
    await userEvent.setup().click(screen.getByTestId("owner-filter-person-1"));
    expect(onChange).toHaveBeenCalledWith("person-1");
  });

  it("clears the filter back to All owners", async () => {
    const onChange = vi.fn();
    render(<OwnerFilterChips owners={[SHARED, ALICE]} value={ALICE.id} onChange={onChange} />);
    await userEvent.setup().click(screen.getByTestId("owner-filter-all"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("marks the active scope for assistive tech", () => {
    render(<OwnerFilterChips owners={[SHARED, ALICE]} value={ALICE.id} onChange={vi.fn()} />);
    expect(screen.getByTestId("owner-filter-person-1")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("owner-filter-all")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("owner-filter-shared-1")).toHaveAttribute("aria-pressed", "false");
  });
});
