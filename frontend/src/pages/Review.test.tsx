import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Category, CategoryGroup, Owner, Transaction } from "@/api/types";
import Review from "@/pages/Review";
import ReviewTable from "@/pages/ReviewTable";

// `/review` is §9's one recorded exception to "a page must not have two JSX trees
// branching on viewport" (ADR-0057), and this file is half of what makes that
// exception safe: both trees are exercised. The other half is the e2e pair —
// `review.spec.ts` (pinned below `lg:`, the deck and §4.17) and the table tests
// in `desktop.spec.ts` (the columns, the keys, the geometry).
//
// jsdom has no `matchMedia`, so `useIsDesktop()` reports "not desktop" and
// `<Review />` mounts the deck unless a test stubs it — which is exactly how the
// switch itself is tested below. The table's own behaviour is tested by rendering
// `ReviewTable` directly: it is only ever mounted at `lg:`, and a test that had to
// lie about the viewport to reach it would be testing the lie.

const h = vi.hoisted(() => ({
  items: [] as unknown[],
  categories: [] as unknown[],
  groups: [] as unknown[],
  accounts: [] as unknown[],
  owners: [] as unknown[],
  /** Every verdict this file's mutation was called with. */
  verdicts: [] as { id: string; body: Record<string, unknown> }[],
  /** Every category write this file's mutation was called with. */
  categorised: [] as { id: string; body: Record<string, unknown> }[],
  /** Set to make the verdict mutation fail. */
  verdictError: null as Error | null,
}));

// The transfer half of the detail sheet, which the sheet owns through its own
// module. Stubbed rather than served by a real QueryClient, the same trade
// `TxnDetailSheet.test.tsx` makes.
vi.mock("@/api/transfers", () => ({
  useTransfer: () => ({ data: undefined, isPending: false, isError: false, error: null }),
  useTransferCandidates: () => ({
    data: undefined,
    isPending: false,
    isError: false,
    error: null,
  }),
  useUnlinkTransfer: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useLinkTransfer: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  const query = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  const idle = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null });
  return {
    ...actual,
    useTransactions: () => query({ items: h.items, next_cursor: null }),
    useCategories: () => query(h.categories),
    useCategoryGroups: () => query(h.groups),
    useAccounts: () => query(h.accounts),
    useOwners: () => query(h.owners),
    useTags: () => query([]),
    // Everything the transaction sheet reaches for on its own. Real mutations
    // would each want a QueryClient this file deliberately does not stand up.
    useHousehold: () => query({ name: "Home", base_currency: "USD", timezone: "UTC" }),
    useCreateOwner: idle,
    useDeleteTransaction: idle,
    useReplaceSplits: idle,
    useInvalidateLedger: () => () => {},
    // Two calls to this hook are two mutations: the verdict and the category.
    // Which is which cannot be told from the hook, so the kind is read off the
    // body the caller passes — `review_status` is a verdict, `category_id` is a
    // category — and each is recorded where its own test can see it.
    useUpdateTransaction: () => ({
      mutate: (
        v: { id: string; body: Record<string, unknown> },
        opts?: { onError?: (e: Error) => void },
      ) => {
        if ("review_status" in v.body) {
          h.verdicts.push(v);
          if (h.verdictError) opts?.onError?.(h.verdictError);
        } else {
          h.categorised.push(v);
        }
      },
      isError: false,
      error: null,
    }),
  };
});

const ACC = {
  id: "acc-1",
  name: "Everyday Checking",
  institution: "Fake Bank",
  type: "depository",
  currency: "USD",
  balance: "100.00",
  balance_date: "2026-09-20",
  owner_id: null,
  effective_owner_id: "own-1",
} as unknown as Account;

const OWNER = { id: "own-1", name: "Aditya", kind: "person", sort: 0 } as unknown as Owner;

const GROUPS = [{ id: "g-food", name: "Food & Dining", type: "expense", sort: 0 }] as CategoryGroup[];
const CATS = [
  { id: "c-groc", group_id: "g-food", name: "Groceries", icon: "🛒", color: null, sort: 0 },
] as Category[];

function txn(n: number, over: Partial<Transaction> = {}): Transaction {
  return {
    id: `t-${n}`,
    account_id: "acc-1",
    amount: "-7.77",
    currency: "USD",
    base_amount: "-7.77",
    fx_rate_date: null,
    transacted_at: "2026-09-20T12:00:00Z",
    posted_at: "2026-09-20T12:00:00Z",
    description: null,
    merchant: `Merchant ${n}`,
    category_id: null,
    owner_id: "own-1",
    effective_owner_id: "own-1",
    is_pending: false,
    review_status: "needs_review",
    is_hidden: false,
    is_split_parent: false,
    transfer_group_id: null,
    field_sources: {},
    notes: null,
    source: "manual",
    tag_ids: [],
    splits: [],
    ...over,
  } as Transaction;
}

/** The row that currently holds the list's one tab stop. */
function currentRow(): HTMLElement | undefined {
  return screen.getAllByTestId(/^review-row-/).find((r) => r.querySelector('[tabindex="0"]'));
}

/** Enter the queue the way a keyboard does — Tab reaches it, ↑/↓ move inside it.
 *  `down` is how many rows to step down from the first. Through user-event rather
 *  than `element.focus()`, because a real focus is a real `onFocus` and a state
 *  update; going around user-event is how warnings about unwrapped `act` start. */
async function tabIntoQueue(user: ReturnType<typeof userEvent.setup>, down = 0) {
  await user.tab();
  for (let i = 0; i < down; i += 1) await user.keyboard("{ArrowDown}");
}

beforeEach(() => {
  h.items = [];
  h.categories = CATS;
  h.groups = GROUPS;
  h.accounts = [ACC];
  h.owners = [OWNER];
  h.verdicts = [];
  h.categorised = [];
  h.verdictError = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("<Review /> — which instrument is mounted (ADR-0057, §9)", () => {
  it("mounts the deck below lg:, and never the table", () => {
    // No matchMedia stub: jsdom reports "not desktop", which is the phone.
    h.items = [txn(1)];
    render(<Review />);

    expect(screen.getByTestId("review-deck")).toBeInTheDocument();
    expect(screen.queryByTestId("review-rows")).not.toBeInTheDocument();
  });

  it("mounts the table at lg:, and never the deck", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    h.items = [txn(1)];
    render(<Review />);

    expect(screen.getByTestId("review-rows")).toBeInTheDocument();
    expect(screen.queryByTestId("review-deck")).not.toBeInTheDocument();
    // The deck's swipe card is what must not be here: one card at a time is the
    // phone's shape, and two trees that both rendered would be the drift §9
    // forbids rather than the exception it records.
    expect(screen.queryByTestId("swipe-card")).not.toBeInTheDocument();
  });

  it("mounts the deck with an empty queue, and builds no card at all", () => {
    // Regression test for a real white page: `ReviewQueueBody` is handed the
    // card as its `children`, and a child element is *built* by the render that
    // passes it — before `ReviewQueueBody` decides whether to draw it. Without
    // the `current ?` guard in `Review.tsx`, the card's own JSX runs
    // `current.id` on an empty queue and throws, and the whole route goes
    // blank. The states are asserted in the deck's own words because that is
    // the tree a phone gets.
    render(<Review />);

    expect(screen.getByTestId("review-empty")).toHaveTextContent("Nothing to review. 🎉");
    expect(screen.getByTestId("review-remaining")).toHaveTextContent("All done");
    expect(screen.queryByTestId("review-deck")).not.toBeInTheDocument();
    // Not merely hidden: nothing under the card was evaluated, so the deck's
    // controls do not exist to be reached by a keyboard or a screen reader.
    expect(screen.queryByTestId("review-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-category")).not.toBeInTheDocument();
  });
});

describe("<ReviewTable /> — §9.4's row, at the queue's scale", () => {
  it("renders a row per queued transaction, under a real header row naming the columns", () => {
    h.items = [txn(1), txn(2)];
    render(<ReviewTable />);

    expect(screen.getAllByTestId(/^review-row-/)).toHaveLength(2);
    const header = screen.getByTestId("review-rows").firstElementChild as HTMLElement;
    // §9.4: "A real header row appears above the list, naming the columns."
    for (const name of ["Merchant", "Date", "Category", "Owner", "Amount"]) {
      expect(header.textContent, name).toContain(name);
    }
    // `aria-hidden`, the same call the ledger's header makes: every row already
    // carries its values as text, and a screen reader hearing the column names
    // before each row would be told the same thing twice.
    expect(header).toHaveAttribute("aria-hidden", "true");
  });

  it("shows the effective owner, and marks the ones that only inherit it", () => {
    h.items = [txn(1), txn(2, { owner_id: null })];
    render(<ReviewTable />);

    expect(screen.getByTestId("review-owner-t-1")).toHaveTextContent("Aditya");
    expect(screen.getByTestId("review-owner-t-1")).not.toHaveTextContent("↳");
    // Inherited is not a colour or an italic alone (§7.8): the glyph is
    // decorative and the sentence behind it is the accessible text.
    const inherited = screen.getByTestId("review-owner-t-2");
    expect(inherited).toHaveTextContent("↳");
    expect(inherited).toHaveTextContent("(inherited from Everyday Checking)");
  });

  it("puts one tab stop in the queue, and moves it with ↑ and ↓", async () => {
    const user = userEvent.setup();
    h.items = [txn(1), txn(2), txn(3)];
    render(<ReviewTable />);

    // One tab stop for the whole queue rather than one per control per row:
    // without it a fifty-row queue is an obstacle course rather than a keyboard
    // route, and the fiftieth row is the one that matters.
    expect(
      screen.getAllByTestId(/^review-row-/).map((r) => r.querySelectorAll('[tabindex="0"]').length),
    ).toEqual([1, 0, 0]);
    // Landing on the page does not move focus — nothing here is focused yet.
    expect(document.body).toHaveFocus();

    // Tab reaches the row; from there the arrows do the rest.
    await tabIntoQueue(user);
    expect(screen.getByTestId("review-open-t-1")).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByTestId("review-open-t-2")).toHaveFocus();
    expect(currentRow()).toBe(screen.getByTestId("review-row-t-2"));

    await user.keyboard("{ArrowDown}{ArrowUp}");
    expect(screen.getByTestId("review-open-t-2")).toHaveFocus();

    // Clamped, not wrapped: a queue has a top and a bottom, and falling off one
    // end onto the other is how someone files a row they never saw.
    await user.keyboard("{ArrowUp}");
    expect(screen.getByTestId("review-open-t-1")).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByTestId("review-open-t-1")).toHaveFocus();
  });

  it("files the row → was pressed in, and puts focus on the row that took its place", async () => {
    const user = userEvent.setup();
    h.items = [txn(1), txn(2), txn(3)];
    render(<ReviewTable />);

    await tabIntoQueue(user, 1);
    await user.keyboard("{ArrowRight}");

    // The middle row, and the mutation that filed it — not the row the tab stop
    // would have moved to had the decision been read off the wrong row.
    expect(h.verdicts).toEqual([{ id: "t-2", body: { review_status: "reviewed" } }]);
    expect(screen.queryByTestId("review-row-t-2")).not.toBeInTheDocument();
    expect(screen.getByTestId("review-open-t-3")).toHaveFocus();
    expect(screen.getByTestId("review-remaining")).toHaveTextContent("2 to review");
    expect(screen.getByTestId("review-last")).toHaveTextContent("Merchant 2 reviewed.");
  });

  it("ignores on ←, and ↑ after a decision goes to the row that slid up", async () => {
    const user = userEvent.setup();
    h.items = [txn(1), txn(2), txn(3)];
    render(<ReviewTable />);

    // Standing on the last row: the row above it is the one that takes the
    // vacated place.
    await tabIntoQueue(user, 2);
    await user.keyboard("{ArrowLeft}");

    expect(h.verdicts).toEqual([{ id: "t-3", body: { review_status: "ignored" } }]);
    expect(screen.getByTestId("review-open-t-2")).toHaveFocus();
    expect(screen.getByTestId("review-last")).toHaveTextContent("Merchant 3 ignored.");
  });

  it("files the last row of the queue, and says the queue is clear", async () => {
    const user = userEvent.setup();
    h.items = [txn(1)];
    render(<ReviewTable />);

    await tabIntoQueue(user);
    await user.keyboard("{ArrowRight}");

    expect(h.verdicts).toEqual([{ id: "t-1", body: { review_status: "reviewed" } }]);
    expect(screen.getByTestId("review-empty")).toHaveTextContent("Nothing to review. 🎉");
    expect(screen.getByTestId("review-remaining")).toHaveTextContent("All done");
  });

  it("puts the row back when the decision fails, and says why", async () => {
    const user = userEvent.setup();
    h.items = [txn(1), txn(2)];
    h.verdictError = new Error("upstream said no");
    render(<ReviewTable />);

    await tabIntoQueue(user);
    await user.keyboard("{ArrowRight}");

    // A row that vanished and was never filed is the one outcome a triage pass
    // must never produce: the row is back, focus is on it, and the alert is what
    // a sighted reader gets in place of the throw the deck would have shown.
    expect(screen.getByTestId("review-row-t-1")).toBeInTheDocument();
    expect(screen.getByTestId("review-decide-error")).toHaveTextContent(
      "Merchant 1 was not reviewed: upstream said no",
    );
    expect(screen.getByTestId("review-open-t-1")).toHaveFocus();
    expect(screen.getByTestId("review-last")).toHaveTextContent("");
    expect(screen.getByTestId("review-remaining")).toHaveTextContent("2 to review");
    expect(screen.getByTestId("review-row-t-2")).toBeInTheDocument();
  });

  it("assigns a category from the row, and the row keeps its place", async () => {
    const user = userEvent.setup();
    h.items = [txn(1), txn(2)];
    render(<ReviewTable />);

    await tabIntoQueue(user, 1);
    await user.keyboard("c");

    // The picker is the deck's picker: the app has one category-assignment
    // component, and a table row is not a reason for a second one (ADR-0057).
    const picker = screen.getByTestId("review-category-picker");
    await user.click(within(picker).getByRole("button", { name: /Groceries/ }));

    expect(h.categorised).toEqual([{ id: "t-2", body: { category_id: "c-groc" } }]);
    // Shown on the row at once, before the refetch lands, and the row is still
    // there: filing a category is not deciding the transaction — §4.17's rule,
    // which holds at both widths.
    expect(screen.getByTestId("review-category-t-2")).toHaveTextContent("Groceries");
    expect(screen.getByTestId("review-row-t-2")).toBeInTheDocument();
    expect(h.verdicts).toEqual([]);
  });

  it("opens the transaction over the table, never beside it", async () => {
    const user = userEvent.setup();
    h.items = [txn(1)];
    render(<ReviewTable />);

    await user.click(screen.getByTestId("review-open-t-1"));

    expect(screen.getByTestId("txn-detail")).toBeInTheDocument();
    expect(screen.getByTestId("txn-detail")).toHaveAttribute("role", "dialog");
    // And it is not a decision: the row is still asking.
    expect(screen.getByTestId("review-row-t-1")).toBeInTheDocument();
  });

  it("carries the three queue states, in the deck's own words", () => {
    render(<ReviewTable />);

    expect(screen.getByTestId("review-empty")).toHaveTextContent("Nothing to review. 🎉");
    expect(screen.getByTestId("review-remaining")).toHaveTextContent("All done");
    // Nothing to review means no rows to be in the tab order (§4.7's point about
    // a tab stop that does nothing).
    expect(screen.queryByTestId("review-rows")).not.toBeInTheDocument();
  });

  it("does not swallow a shortcut that belongs to the browser", () => {
    h.items = [txn(1), txn(2)];
    render(<ReviewTable />);

    // ⌘← and Ctrl+← are the browser's and the OS's. Pressed *from a row*, so the
    // row lookup below succeeds and the modifier guard is the only thing that
    // can be doing the work — the same key with no modifier is the test above.
    fireEvent.keyDown(screen.getByTestId("review-open-t-1"), {
      key: "ArrowLeft",
      ctrlKey: true,
      bubbles: true,
    });

    expect(h.verdicts).toEqual([]);
    expect(screen.getByTestId("review-row-t-1")).toBeInTheDocument();
  });
});
