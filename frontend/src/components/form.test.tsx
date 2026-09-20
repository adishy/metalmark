import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Field, Input, requiredText, validAmount, validCurrency, validRate } from "@/components/form";

describe("form validation helpers", () => {
  it("requiredText flags empty/whitespace", () => {
    expect(requiredText("")).toBe("Required");
    expect(requiredText("   ")).toBe("Required");
    expect(requiredText("x")).toBeNull();
  });

  it("validAmount accepts decimals and signs, rejects junk", () => {
    expect(validAmount("-54.32")).toBeNull();
    expect(validAmount("12")).toBeNull();
    expect(validAmount("+0.5")).toBeNull();
    expect(validAmount("")).toBe("Required");
    expect(validAmount("abc")).toBe("Enter a valid number");
    expect(validAmount("1.2.3")).toBe("Enter a valid number");
  });

  it("validCurrency requires exactly 3 letters", () => {
    expect(validCurrency("USD")).toBeNull();
    expect(validCurrency("eur")).toBeNull();
    expect(validCurrency("US")).toBe("Use a 3-letter code");
    expect(validCurrency("US1")).toBe("Use a 3-letter code");
  });

  it("validRate requires a positive number", () => {
    expect(validRate("0.92")).toBeNull();
    expect(validRate("0")).toBe("Must be greater than 0");
    expect(validRate("-1")).toBe("Enter a valid number");
  });
});

describe("<Field />", () => {
  it("ties the label to the control and renders the error region", () => {
    render(
      <Field label="Amount" htmlFor="amt" error="Enter a valid number" required>
        <Input id="amt" defaultValue="x" />
      </Field>,
    );
    // Label association: querying by the accessible label finds the input.
    const input = screen.getByLabelText(/Amount/);
    expect(input).toHaveAttribute("id", "amt");
    expect(screen.getByTestId("amt-error")).toHaveTextContent("Enter a valid number");
  });
});
