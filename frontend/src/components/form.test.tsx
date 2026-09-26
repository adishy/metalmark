import { describe, it, expect, vi, afterEach } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Combobox,
  Field,
  Input,
  requiredText,
  validAmount,
  validCurrency,
  validRate,
  type ComboboxOption,
} from "@/components/form";

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

// ---- Combobox -----------------------------------------------------------
//
// These are the tests the APG pattern is checked against: the roles and the
// state that make the control operable without a mouse, asserted one key at a
// time. §4.4 says a custom picker must implement the pattern in full, so a
// regression here is a regression against the doc rather than a taste call.

const CATS: ComboboxOption[] = [
  { value: "", label: "Uncategorized" },
  { value: "c-groc", label: "🛒 Groceries" },
  { value: "c-dine", label: "🍽 Dining out" },
  { value: "c-trav", label: "✈️ Travel" },
];

/** A controlled Combobox, the way every call site uses one. */
function Picker({
  initial = "",
  options = CATS,
  onCommit = () => {},
}: {
  initial?: string;
  options?: ComboboxOption[];
  onCommit?: (v: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <Field label="Category" htmlFor="cat">
      <Combobox
        id="cat"
        value={value}
        options={options}
        listLabel="Category"
        onChange={(v) => {
          setValue(v);
          onCommit(v);
        }}
      />
    </Field>
  );
}

const box = () => screen.getByRole("combobox");

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("<Combobox />", () => {
  it("is a combobox with a listbox it names, closed until asked", async () => {
    const user = userEvent.setup();
    render(<Picker />);

    expect(box()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.click(box());
    const list = screen.getByRole("listbox");
    expect(box()).toHaveAttribute("aria-expanded", "true");
    // The input points at the popup, and the popup carries its own name — the
    // visible label names the input, not the list.
    expect(box()).toHaveAttribute("aria-controls", list.id);
    expect(list).toHaveAccessibleName("Category");
    expect(screen.getAllByRole("option")).toHaveLength(4);
    expect(screen.getByRole("option", { name: /Travel/ })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("opens with the arrow keys and keeps aria-activedescendant on the highlight", async () => {
    const user = userEvent.setup();
    render(<Picker />);
    box().focus();

    // Closed, ArrowDown opens (the native <select>'s own behaviour).
    await user.keyboard("{ArrowDown}");
    expect(box()).toHaveAttribute("aria-expanded", "true");
    const [first] = screen.getAllByRole("option");
    expect(box()).toHaveAttribute("aria-activedescendant", first.id);

    await user.keyboard("{ArrowDown}");
    const options = screen.getAllByRole("option");
    expect(box()).toHaveAttribute("aria-activedescendant", options[1].id);

    await user.keyboard("{ArrowUp}{ArrowUp}");
    // Wraps rather than sticking at the top, so the ends are one key away.
    expect(box()).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[3].id);

    await user.keyboard("{Home}");
    expect(box()).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[0].id);
    await user.keyboard("{End}");
    expect(box()).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[3].id);
  });

  it("commits the highlighted option with Enter", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Picker onCommit={onCommit} />);

    await user.click(box());
    // Opening by click puts the highlight on the current value ("Uncategorized"
    // here), so two steps down is Dining out — and Enter commits the row the
    // user is looking at, not the top of the list.
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");

    expect(onCommit).toHaveBeenCalledWith("c-dine");
    expect(box()).toHaveValue("🍽 Dining out");
    expect(box()).toHaveAttribute("aria-expanded", "false");
  });

  it("filters as you type and commits the top match", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Picker onCommit={onCommit} />);

    await user.type(box(), "trav");
    // The query is a search, not a value: only matches are listed.
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: /Travel/ })).toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(onCommit).toHaveBeenCalledWith("c-trav");
  });

  it("starts a fresh search when typing into the closed control", async () => {
    const user = userEvent.setup();
    render(<Picker initial="c-groc" />);
    expect(box()).toHaveValue("🛒 Groceries");

    // Not "🛒 Groceriesdin" — the label is a value, and typing replaces it.
    await user.type(box(), "din");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: /Dining out/ })).toBeInTheDocument();
  });

  it("shows the whole list again when the query is cleared", async () => {
    const user = userEvent.setup();
    render(<Picker />);

    await user.type(box(), "zzz");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("No matches")).toBeInTheDocument();

    await user.clear(box());
    expect(screen.getAllByRole("option")).toHaveLength(4);
  });

  it("Escape dismisses the list and keeps the value, and does not reach the page", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    // A Dialog listens for Escape on the document; dismissing the list must not
    // dismiss the sheet the list is inside.
    const onDocumentKey = vi.fn();
    document.addEventListener("keydown", onDocumentKey);
    render(<Picker onCommit={onCommit} />);

    await user.click(box());
    await user.keyboard("{ArrowDown}");
    // Only the dismissal is under test: the arrow above is a keydown that is
    // *meant* to reach the page, and it would mask what Escape did.
    onDocumentKey.mockClear();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onCommit).not.toHaveBeenCalled();
    expect(box()).toHaveAttribute("aria-expanded", "false");
    expect(onDocumentKey).not.toHaveBeenCalled();
    document.removeEventListener("keydown", onDocumentKey);
  });

  it("Tab leaves without committing a half-typed search", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(
      <>
        <Picker onCommit={onCommit} />
        <Input aria-label="Elsewhere" />
      </>,
    );

    await user.click(box());
    await user.type(box(), "dining");
    await user.tab();

    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Elsewhere")).toHaveFocus();
    // The control goes back to showing what is actually stored — the empty
    // option's label, not the abandoned query.
    expect(box()).toHaveValue("Uncategorized");
  });

  it("marks the chosen option and closes when it is clicked", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Picker onCommit={onCommit} />);

    await user.click(box());
    await user.click(screen.getByRole("option", { name: /Groceries/ }));

    expect(onCommit).toHaveBeenCalledWith("c-groc");
    expect(box()).toHaveValue("🛒 Groceries");
    // Focus stays on the control it just changed, so the next Tab is not a jump
    // back to the top of the form.
    expect(box()).toHaveFocus();
  });

  it("carries Field's error wiring and the required attribute on the input", () => {
    render(
      <Field label="Account" htmlFor="acct" error="Pick one" required>
        <Combobox id="acct" value="" options={CATS} listLabel="Account" onChange={() => {}} />
      </Field>,
    );

    const input = screen.getByLabelText(/Account/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "acct-error");
    expect(input).toBeRequired();
  });

  it("renders the platform picker on a phone instead of a popup", async () => {
    // §4.4/§5: the native picker is the right control on a phone, so the
    // combobox stands down there rather than replacing it.
    vi.stubGlobal(
      "matchMedia",
      (query: string) => ({
        matches: true,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    );
    const user = userEvent.setup();
    render(<Picker />);

    expect(box().tagName).toBe("SELECT");
    // No popup, ever: the platform draws the list.
    await user.click(box());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Travel/ })).toBeInTheDocument();
  });
});
