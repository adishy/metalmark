import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Category, Owner, Tag } from "@/api/types";
import type { Rule } from "@/api/rules";
import RuleBuilder from "@/components/RuleBuilder";

// The builder owns its data (taxonomy for the pickers) and its two mutations, so
// stub them rather than standing up a QueryClient and a fetch mock for what is a
// form test. The mutate calls are the assertions: the payload the form builds is
// the contract with the server.
const h = vi.hoisted(() => ({
  categories: [] as Category[],
  accounts: [] as Account[],
  owners: [] as Owner[],
  tags: [] as Tag[],
  create: vi.fn(),
  update: vi.fn(),
  updateError: null as Error | null,
}));

vi.mock("@/api/hooks", () => ({
  useCategories: () => ({ data: h.categories }),
  useAccounts: () => ({ data: h.accounts }),
  useOwners: () => ({ data: h.owners }),
  useTags: () => ({ data: h.tags }),
}));

vi.mock("@/api/rules", () => ({
  useCreateRule: () => ({ mutate: h.create, isPending: false, isError: false, error: null }),
  useUpdateRule: () => ({
    mutate: h.update,
    isPending: false,
    isError: h.updateError !== null,
    error: h.updateError,
  }),
}));

const DINING: Category = { id: "cat-1", group_id: "grp-1", name: "Dining", icon: null, color: null, sort: 0 };
const TRAVEL: Category = { id: "cat-2", group_id: "grp-1", name: "Travel", icon: null, color: null, sort: 1 };
const CHECKING: Account = {
  id: "acct-1",
  name: "Checking",
  type: "depository",
  currency: "USD",
  subtype: null,
  institution: null,
  current_balance: "100.00",
  balance_date: null,
  is_asset: true,
  owner_id: "person-1",
  is_manual: false,
  is_hidden: false,
};
const SHARED: Owner = { id: "shared-1", name: "Shared", kind: "shared", sort: 0 };
const ALICE: Owner = { id: "person-1", name: "Alice", kind: "person", sort: 1 };
const COFFEE: Tag = { id: "tag-1", name: "coffee", color: null };
const WEEKEND: Tag = { id: "tag-2", name: "weekend", color: null };

/** A rule as the server sends one: every condition and action key present, the
 * unset ones null. */
const RULE: Rule = {
  id: "rule-1",
  name: "Amazon",
  priority: 5,
  enabled: true,
  conditions: {
    merchant_contains: "amzn",
    description_regex: null,
    amount_min: null,
    amount_max: null,
    direction: "out",
    account_ids: null,
    category_id: null,
    is_pending: null,
  },
  actions: {
    set_category_id: "cat-2",
    add_tag_ids: ["tag-1"],
    set_owner_id: null,
    rename_merchant: null,
    set_hidden: true,
    mark_reviewed: null,
  },
  created_at: "2026-09-20T00:00:00Z",
};

/** The same rule with the split action (ADR-0031): an amount leg and the leg
 * that takes what is left, as the server sends one — every leg key present, the
 * unset ones null. `RULE`'s `direction: "out"` is what makes a negative amount
 * leg the one this rule can actually have. */
const SPLIT_RULE: Rule = {
  ...RULE,
  actions: {
    ...RULE.actions,
    split: [
      {
        amount: "-50.00",
        percent: null,
        remainder: null,
        category_id: DINING.id,
        owner_id: null,
        notes: null,
      },
      {
        amount: null,
        percent: null,
        remainder: true,
        category_id: TRAVEL.id,
        owner_id: SHARED.id,
        notes: "the rest of the week",
      },
    ],
  },
};

beforeEach(() => {
  h.categories = [DINING, TRAVEL];
  h.accounts = [CHECKING];
  h.owners = [SHARED, ALICE];
  h.tags = [COFFEE, WEEKEND];
  h.create.mockReset();
  h.update.mockReset();
  h.updateError = null;
});

describe("<RuleBuilder />", () => {
  it("labels every control", () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    // Label association, not just presence: each control is reachable by name.
    expect(screen.getByLabelText(/Name/)).toHaveAttribute("data-testid", "rule-name");
    expect(screen.getByLabelText("Merchant contains")).toBeInTheDocument();
    expect(screen.getByLabelText("Description matches")).toBeInTheDocument();
    expect(screen.getByLabelText("Set owner")).toBeInTheDocument();
    expect(screen.getByLabelText("Review status")).toBeInTheDocument();
  });

  it("refuses to save without a name", async () => {
    const onClose = vi.fn();
    render(<RuleBuilder onClose={onClose} />);
    await userEvent.setup().click(screen.getByTestId("rule-save"));

    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(h.create).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("sends only the keys the user set, and closes on save", async () => {
    const onClose = vi.fn();
    h.create.mockImplementation((_body: unknown, opts: { onSuccess: () => void }) =>
      opts.onSuccess(),
    );
    render(<RuleBuilder onClose={onClose} />);

    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Coffee");
    await user.type(screen.getByTestId("rule-merchant-contains"), "starbucks");
    await user.type(screen.getByTestId("rule-amount-max"), "-10");
    await user.selectOptions(screen.getByTestId("rule-set-category"), DINING.id);
    await user.click(screen.getByTestId("rule-tag-tag-1"));
    await user.click(screen.getByTestId("rule-save"));

    expect(h.create).toHaveBeenCalledTimes(1);
    const [body] = h.create.mock.calls[0];
    expect(body.name).toBe("Coffee");
    expect(body.priority).toBe(100);
    expect(body.enabled).toBe(true);
    // Amounts ride as decimal strings and untouched keys are absent, which is
    // how a cleared condition disappears (conditions replace wholesale).
    expect(body.conditions).toEqual({ merchant_contains: "starbucks", amount_max: "-10" });
    expect(body.actions).toEqual({ set_category_id: DINING.id, add_tag_ids: [COFFEE.id] });
    expect(onClose).toHaveBeenCalled();
  });

  it("sends a false action rather than dropping it", async () => {
    // Unhide and needs-review are real actions, and "" (leave unchanged) is a
    // third state — a truthiness check on the draft would lose the first two.
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Unhide groceries");
    await user.selectOptions(screen.getByTestId("rule-set-hidden"), "false");
    await user.selectOptions(screen.getByTestId("rule-mark-reviewed"), "false");
    await user.click(screen.getByTestId("rule-save"));

    expect(h.create.mock.calls[0][0].actions).toEqual({
      set_hidden: false,
      mark_reviewed: false,
    });
  });

  it("leaves unset actions out entirely", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Watch only");
    await user.click(screen.getByTestId("rule-save"));

    expect(h.create.mock.calls[0][0].actions).toEqual({});
  });

  it("prefills from the rule being edited and patches it by id", async () => {
    render(<RuleBuilder rule={RULE} onClose={vi.fn()} />);

    expect(screen.getByTestId("rule-name")).toHaveValue("Amazon");
    expect(screen.getByTestId("rule-priority")).toHaveValue("5");
    expect(screen.getByTestId("rule-merchant-contains")).toHaveValue("amzn");
    expect(screen.getByTestId("rule-direction")).toHaveValue("out");
    expect(screen.getByTestId("rule-set-category")).toHaveValue(TRAVEL.id);
    expect(screen.getByTestId("rule-set-hidden")).toHaveValue("true");
    expect(screen.getByTestId("rule-tag-tag-1")).toBeChecked();
    expect(screen.getByTestId("rule-tag-tag-2")).not.toBeChecked();

    await userEvent.setup().click(screen.getByTestId("rule-save"));

    const [call] = h.update.mock.calls[0];
    expect(call.id).toBe(RULE.id);
    expect(call.body.conditions).toEqual({ merchant_contains: "amzn", direction: "out" });
    expect(call.body.actions).toEqual({
      set_category_id: TRAVEL.id,
      add_tag_ids: [COFFEE.id],
      set_hidden: true,
    });
  });

  it("keeps tags the rule already had and adds the newly ticked one", async () => {
    render(<RuleBuilder rule={RULE} onClose={vi.fn()} />);
    await userEvent.setup().click(screen.getByTestId("rule-tag-tag-2"));
    await userEvent.setup().click(screen.getByTestId("rule-save"));

    expect(h.update.mock.calls[0][0].body.actions.add_tag_ids).toEqual([
      COFFEE.id,
      WEEKEND.id,
    ]);
  });

  it("rejects an inverted amount range before it reaches the server", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Bad bounds");
    await user.type(screen.getByTestId("rule-amount-min"), "10");
    await user.type(screen.getByTestId("rule-amount-max"), "-10");
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByText("Lower bound is above the upper bound")).toBeInTheDocument();
    expect(h.create).not.toHaveBeenCalled();
  });

  it("rejects a regular expression that does not compile", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Bad regex");
    await user.type(screen.getByTestId("rule-description-regex"), "(unclosed");
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByText("Invalid regular expression")).toBeInTheDocument();
    expect(h.create).not.toHaveBeenCalled();
  });

  it("warns when a rule would match every transaction", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    const hint = screen.getByTestId("rule-no-conditions");
    expect(hint).toBeInTheDocument();

    await user.type(screen.getByTestId("rule-merchant-contains"), "amzn");
    expect(screen.queryByTestId("rule-no-conditions")).not.toBeInTheDocument();
  });

  it("surfaces a save failure and stays open", async () => {
    const onClose = vi.fn();
    h.updateError = new Error("Owner not found");
    render(<RuleBuilder rule={RULE} onClose={onClose} />);
    await userEvent.setup().click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-error")).toHaveTextContent("Owner not found");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes without saving on cancel", async () => {
    const onClose = vi.fn();
    render(<RuleBuilder onClose={onClose} />);
    await userEvent.setup().click(screen.getByTestId("rule-cancel"));

    expect(onClose).toHaveBeenCalled();
    expect(h.create).not.toHaveBeenCalled();
  });

  // ---- the split action (ADR-0031) ----------------------------------------

  it("adds a split with an amount leg and the leg that takes the rest", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Amazon split");
    await user.click(screen.getByTestId("rule-split-toggle"));

    // Switching it on seeds the shape a split has to have, so the first thing
    // the section shows is not a split the server would refuse.
    expect(screen.getByTestId("rule-split-leg-1-rest")).toBeChecked();
    expect(screen.getByTestId("rule-split-leg-1-takes")).toHaveTextContent("Takes the rest");

    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "-50");
    await user.selectOptions(screen.getByTestId("rule-split-leg-0-category"), DINING.id);
    expect(screen.getByTestId("rule-split-leg-0-takes")).toHaveTextContent("Takes -50");
    await user.click(screen.getByTestId("rule-save"));

    // The leg that is not the rest carries one of amount or percent; the rest
    // carries neither, which is what makes the sum exact for any parent amount.
    expect(h.create.mock.calls[0][0].actions.split).toEqual([
      { amount: "-50", category_id: DINING.id },
      { remainder: true },
    ]);
  });

  it("loads a stored split and sends it back unchanged", async () => {
    render(<RuleBuilder rule={SPLIT_RULE} onClose={vi.fn()} />);

    expect(screen.getByTestId("rule-split-leg-0-amount")).toHaveValue("-50.00");
    expect(screen.getByTestId("rule-split-leg-0-category")).toHaveValue(DINING.id);
    expect(screen.getByTestId("rule-split-leg-1-rest")).toBeChecked();
    expect(screen.getByTestId("rule-split-leg-1-owner")).toHaveValue(SHARED.id);
    expect(screen.getByTestId("rule-split-leg-1-takes")).toHaveTextContent("Takes the rest");

    await userEvent.setup().click(screen.getByTestId("rule-save"));

    const [call] = h.update.mock.calls[0];
    expect(call.id).toBe(SPLIT_RULE.id);
    // Every leg key the response carried, back out in the write shape: the unset
    // ones omitted, and the one note the server had still there — a re-save that
    // dropped it would be a silent edit.
    expect(call.body.actions).toEqual({
      set_category_id: TRAVEL.id,
      add_tag_ids: [COFFEE.id],
      set_hidden: true,
      split: [
        { amount: "-50.00", category_id: DINING.id },
        {
          remainder: true,
          category_id: TRAVEL.id,
          owner_id: SHARED.id,
          notes: "the rest of the week",
        },
      ],
    });
  });

  it("carries a percent leg and says what it takes", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Split by percent");
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.type(screen.getByTestId("rule-split-leg-0-percent"), "25");
    await user.selectOptions(screen.getByTestId("rule-split-leg-1-owner"), ALICE.id);

    expect(screen.getByTestId("rule-split-leg-0-takes")).toHaveTextContent("Takes 25%");
    await user.click(screen.getByTestId("rule-save"));

    expect(h.create.mock.calls[0][0].actions.split).toEqual([
      { percent: "25" },
      { remainder: true, owner_id: ALICE.id },
    ]);
  });

  it("removes the split when the toggle is switched off", async () => {
    render(<RuleBuilder rule={SPLIT_RULE} onClose={vi.fn()} />);
    await userEvent.setup().click(screen.getByTestId("rule-split-toggle"));
    await userEvent.setup().click(screen.getByTestId("rule-save"));

    const [call] = h.update.mock.calls[0];
    // Actions replace wholesale, so an omitted key is how a stored split is
    // cleared — the same way a cleared condition disappears.
    expect(call.body.actions).toEqual({
      set_category_id: TRAVEL.id,
      add_tag_ids: [COFFEE.id],
      set_hidden: true,
    });
  });

  it("drops a leg's share when the leg is marked as the rest", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "-50");
    expect(screen.getByTestId("rule-split-leg-0-amount")).toHaveValue("-50");

    await user.click(screen.getByTestId("rule-split-leg-0-rest"));

    // A leg that takes the rest takes no share of its own, so the amount it was
    // carrying goes rather than sitting there as a value the server refuses.
    expect(screen.getByTestId("rule-split-leg-0-amount")).toHaveValue("");
    expect(screen.getByTestId("rule-split-leg-0-takes")).toHaveTextContent("Takes the rest");
  });

  it("refuses a split with no leg taking the rest, in the API's own words", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("rule-name"), "Split");
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "-50");
    await user.type(screen.getByTestId("rule-split-leg-1-amount"), "-20");
    // Untick the seeded remainder: two fixed amounts, which is the shape ADR-0031
    // refuses, because a bank correcting the amount would leave a sum that does
    // not add up.
    await user.click(screen.getByTestId("rule-split-leg-1-rest"));
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /No leg takes the rest — mark exactly one leg as the remainder/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses two legs marked as the rest", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "-50");
    await user.click(screen.getByTestId("rule-split-leg-0-rest"));
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /leg 1 and leg 2 are marked as the rest/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses a leg carrying both an amount and a percent", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "-50");
    await user.type(screen.getByTestId("rule-split-leg-0-percent"), "25");
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /Leg 1 carries both an amount and a percent/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses a percent that is not strictly between 0 and 100", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    const percent = screen.getByTestId("rule-split-leg-0-percent");

    await user.type(percent, "0");
    await user.click(screen.getByTestId("rule-save"));
    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /percent must be greater than 0 and less than 100/,
    );

    await user.clear(percent);
    await user.type(percent, "100");
    await user.click(screen.getByTestId("rule-save"));
    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /percent must be greater than 0 and less than 100/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses percent legs that come to 100% between them", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.click(screen.getByTestId("rule-split-add-leg"));
    await user.type(screen.getByTestId("rule-split-leg-0-percent"), "60");
    await user.type(screen.getByTestId("rule-split-leg-2-percent"), "40");
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /they must come to less than 100%/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses an amount leg of zero", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.click(screen.getByTestId("rule-split-add-leg"));
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "0");
    await user.type(screen.getByTestId("rule-split-leg-2-amount"), "50");
    await user.click(screen.getByTestId("rule-save"));

    const errors = screen.getByTestId("rule-split-error");
    expect(errors).toHaveTextContent(/Leg 1: an amount leg must be non-zero/);
    // A zero leg has no sign to disagree with, so it is not also reported as
    // legs pointing both ways.
    expect(errors).not.toHaveTextContent(/point both ways/);
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses an enabled split with no legs at all", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.click(screen.getByTestId("rule-split-leg-1-remove"));
    await user.click(screen.getByTestId("rule-split-leg-0-remove"));
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /A split needs at least two legs/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });

  it("refuses amount legs pointing the other way from the rule's own direction", async () => {
    render(<RuleBuilder onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("rule-split-toggle"));
    await user.selectOptions(screen.getByTestId("rule-direction"), "out");
    // The rule matches money out, so every transaction it matches is negative —
    // and an amount leg has to share the parent's sign.
    await user.type(screen.getByTestId("rule-split-leg-0-amount"), "50");
    await user.click(screen.getByTestId("rule-save"));

    expect(screen.getByTestId("rule-split-error")).toHaveTextContent(
      /its amount legs must be negative too/,
    );
    expect(h.create).not.toHaveBeenCalled();
  });
});
