import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ColorPicker from "@/components/ColorPicker";
import { tokenHex } from "@/theme/chartTokens";

// The palette is read from the real stylesheet: src/test/setup.ts copies the
// `:root` block of index.css onto the document, so these are the light-theme
// values the picker would resolve in a browser, not a fixture standing in for
// them. A test that hardcoded its own palette would pass while the picker
// offered nothing.
const TEAL = tokenHex("chart-1")!;
const SKY = tokenHex("chart-2")!;

function Harness({ initial = TEAL }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  // No provider: `useTheme` is a module-level external store (theme.ts), so a
  // component that reads it renders standalone.
  return <ColorPicker label="Color" value={value} onChange={setValue} testid="color" />;
}

describe("ColorPicker", () => {
  it("offers the ten series colours as a labelled radio group", () => {
    render(<Harness />);
    const group = screen.getByRole("group", { name: "Color" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Teal" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Sky" })).not.toBeChecked();
    // Named, not just coloured: ten anonymous swatches would be a 1.4.1 failure
    // and unusable with a screen reader.
    expect(screen.getAllByRole("radio")).toHaveLength(10);
  });

  it("reports the colour it is on, in words", () => {
    render(<Harness />);
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      `Selected: Teal · ${TEAL}`,
    );
  });

  it("hands the caller the token's value when a swatch is picked", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "Sky" }));
    expect(screen.getByRole("radio", { name: "Sky" })).toBeChecked();
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      `Selected: Sky · ${SKY}`,
    );
  });

  it("keeps a colour that is not in the palette reachable", async () => {
    const user = userEvent.setup();
    // A household's existing categories hold whatever they were created with,
    // and the stored value can be anything the column accepts.
    render(<Harness initial="#a1b2c3" />);
    expect(screen.getByRole("radio", { name: "Teal" })).not.toBeChecked();
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      "Selected: Custom · #a1b2c3",
    );
    await user.click(screen.getByRole("radio", { name: "Teal" }));
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      `Selected: Teal · ${TEAL}`,
    );
  });

  it("takes a value the palette does not hold", () => {
    // `fireEvent` rather than a click: the platform's own picker is the thing
    // this control hands off to, and it does not exist in jsdom. What is under
    // test is that the value it reports arrives unchanged.
    render(<Harness />);
    fireEvent.change(screen.getByTestId("color-custom"), { target: { value: "#123456" } });
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      "Selected: Custom · #123456",
    );
    expect(screen.getByRole("radio", { name: "Teal" })).not.toBeChecked();
  });

  it("says nothing is chosen rather than claiming a colour", () => {
    render(<Harness initial="" />);
    expect(screen.getByTestId("color-current")).toHaveTextContent(
      "No colour chosen yet.",
    );
  });
});
