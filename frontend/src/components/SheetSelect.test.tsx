import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SheetSelect from "@/components/SheetSelect";

const OPTIONS = [
  { id: "month", label: "This month" },
  { id: "year", label: "This year" },
] as const;

describe("<SheetSelect />", () => {
  it("says what is chosen, and picks from a sheet", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SheetSelect label="Range" value="year" options={OPTIONS} onChange={onChange} testid="r" />);
    const pill = screen.getByTestId("r");
    expect(pill).toHaveTextContent("RangeThis year");
    expect(pill).toHaveAttribute("aria-expanded", "false");
    await user.click(pill);
    const sheet = screen.getByTestId("r-sheet");
    expect(within(sheet).getByTestId("r-option-year")).toHaveAttribute("aria-selected", "true");
    await user.click(within(sheet).getByTestId("r-option-month"));
    expect(onChange).toHaveBeenCalledWith("month");
    expect(screen.queryByTestId("r-sheet")).not.toBeInTheDocument();
  });
});
