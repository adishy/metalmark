// Driving a `Combobox` from a test.
//
// A Combobox is not a `<select>`, so `selectOptions` does not apply to it: the
// list exists only while the control is open, and its rows are elements the
// user clicks rather than options the browser holds. Every call site in the app
// picked one of these up for a list of records, so these helpers say the same
// thing `selectOptions` used to say — open it, pick a row — and the control's
// own keyboard and ARIA behaviour is covered directly in `form.test.tsx`.
//
// Not a `.test.ts` file: vitest collects `*.test.*` only, so this ships to the
// test run as a plain module.
import { screen, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";

type User = ReturnType<UserEvent["setup"]>;

/**
 * The popup the open control is showing.
 *
 * Found by role and used for everything below, because a native `<option>` has
 * role `option` too: a screen that carries both a `Combobox` and an untouched
 * `<select>` would otherwise answer "what is this picker offering" with the
 * options of the other control.
 */
function listbox() {
  return within(screen.getByRole("listbox"));
}

/** Open the picker carrying `testid`, exactly as a mouse user would. */
export async function openPicker(user: User, testid: string): Promise<void> {
  await user.click(screen.getByTestId(testid));
}

/** What the open picker is offering, in the order it lists it. */
export function pickerOptions(): string[] {
  return listbox()
    .getAllByRole("option")
    .map((o) => o.textContent ?? "");
}

/**
 * Pick the option whose visible text matches. The caller has the picker open
 * already — a test that means "choose Dan" should read as one line, and one that
 * means "open, look, then choose" should not have the two fused.
 */
export async function pickOption(user: User, name: string | RegExp): Promise<void> {
  await user.click(listbox().getByRole("option", { name }));
}
