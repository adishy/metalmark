import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SheetMultiSelect from "@/components/SheetMultiSelect";

const OPTIONS = [
  { id: "checking", label: "Everyday Checking" },
  { id: "savings", label: "High-Yield Savings" },
  { id: "card", label: "Rewards Card" },
] as const;

function setup(values: readonly string[] = [], testid = "f") {
  const onToggle = vi.fn();
  const onClear = vi.fn();
  render(
    <SheetMultiSelect
      label="Accounts"
      values={values}
      options={OPTIONS}
      onToggle={onToggle}
      onClear={onClear}
      testid={testid}
    />,
  );
  return { onToggle, onClear };
}

describe("<SheetMultiSelect />", () => {
  it("opens a panel from a pill that names the filter", async () => {
    const user = userEvent.setup();
    setup();
    const pill = screen.getByTestId("f");
    // The summary is the pill's state, in words — the only view of it a screen
    // reader has, and the reason the selection is never colour alone (§4.5).
    expect(pill).toHaveTextContent("AccountsAll");
    expect(pill).toHaveAttribute("aria-expanded", "false");
    await user.click(pill);
    expect(pill).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("f-sheet")).toBeInTheDocument();
  });

  it("names one value and counts several", () => {
    setup(["savings"], "one");
    expect(screen.getByTestId("one")).toHaveTextContent("AccountsHigh-Yield Savings");
    setup(["savings", "card"], "two");
    expect(screen.getByTestId("two")).toHaveTextContent("Accounts2 selected");
  });

  it("uses checkboxes whose state is the selection, and stays open while ticking", async () => {
    const user = userEvent.setup();
    const { onToggle } = setup(["card"]);

    await user.click(screen.getByTestId("f"));
    const panel = screen.getByTestId("f-sheet");
    expect(within(panel).getByTestId("f-option-card")).toBeChecked();
    expect(within(panel).getByTestId("f-option-savings")).not.toBeChecked();

    await user.click(within(panel).getByTestId("f-option-savings"));
    expect(onToggle).toHaveBeenCalledWith("savings");
    // Still open: a multi-select that closed on the first tick would be a
    // single-select with extra steps.
    expect(screen.getByTestId("f-sheet")).toBeInTheDocument();
  });

  it("closes on Done, and offers no Clear while nothing is selected", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByTestId("f"));
    expect(screen.queryByTestId("f-clear")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("f-done"));
    expect(screen.queryByTestId("f-sheet")).not.toBeInTheDocument();
  });

  it("clears a filter that is doing something without closing the panel", async () => {
    const user = userEvent.setup();
    const { onClear } = setup(["savings"]);

    await user.click(screen.getByTestId("f"));
    await user.click(screen.getByTestId("f-clear"));
    expect(onClear).toHaveBeenCalled();
  });

  it("says so when there is nothing to choose from", async () => {
    const user = userEvent.setup();
    render(
      <SheetMultiSelect
        label="Categories"
        values={[]}
        options={[]}
        onToggle={() => {}}
        testid="empty"
      />,
    );
    await user.click(screen.getByTestId("empty"));
    expect(screen.getByTestId("empty-sheet")).toHaveTextContent("Nothing to choose from yet.");
  });
});
