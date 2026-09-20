import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Transaction } from "@/api/types";
import type { TransferCandidate, TransferDetail } from "@/api/transfers";
import TxnDetailSheet from "@/components/TxnDetailSheet";

// The sheet owns its data through the transfer hooks, so those are stubbed rather
// than standing up a QueryClient and a fetch mock — the same trade the other
// component tests here make.
const h = vi.hoisted(() => ({
  transfer: undefined as TransferDetail | undefined,
  candidates: [] as TransferCandidate[],
  unlink: vi.fn(),
  link: vi.fn(),
}));

vi.mock("@/api/transfers", () => ({
  useTransfer: () => ({ data: h.transfer, isLoading: false, isError: false, error: null }),
  useTransferCandidates: () => ({
    data: { items: h.candidates },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useUnlinkTransfer: () => ({ mutate: h.unlink, isPending: false, isError: false, error: null }),
  useLinkTransfer: () => ({ mutate: h.link, isPending: false, isError: false, error: null }),
}));

vi.mock("@/api/hooks", () => ({
  useHousehold: () => ({ data: { name: "Home", base_currency: "USD", timezone: "UTC" } }),
  useOwners: () => ({ data: [] }),
  // The sheet's owner picker wants this one too.
  useCreateOwner: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useUpdateTransaction: () => ({
    mutate: vi.fn(), isPending: false, isError: false, error: null,
  }),
  useDeleteTransaction: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useReplaceSplits: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

const USD_ACCOUNT: Account = {
  id: "acct-usd", name: "Checking", type: "depository", currency: "USD", subtype: null,
  institution: null, current_balance: "0", balance_date: null, is_asset: true,
  owner_id: "shared-1", is_manual: true, is_hidden: false,
};
const EUR_ACCOUNT: Account = {
  ...USD_ACCOUNT, id: "acct-eur", name: "Euro", currency: "EUR",
};

function txn(over: Partial<Transaction> = {}): Transaction {
  return {
    id: "txn-out",
    account_id: USD_ACCOUNT.id,
    amount: "-100.0000",
    currency: "USD",
    base_amount: "-100.0000",
    fx_rate_date: null,
    transacted_at: "2026-01-10T12:00:00Z",
    posted_at: null,
    description: "Transfer out",
    merchant: null,
    category_id: null,
    owner_id: null,
    effective_owner_id: "shared-1",
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
  };
}

const EURO_LEG = txn({
  id: "txn-out", account_id: EUR_ACCOUNT.id, amount: "-100.0000", currency: "EUR",
  base_amount: "-108.0000", description: "To savings",
});
const DOLLAR_LEG = txn({
  id: "txn-in", account_id: USD_ACCOUNT.id, amount: "106.0000", currency: "USD",
  base_amount: "106.0000", description: "From euro account",
});

function renderSheet(t: Transaction, onReplaced = vi.fn()) {
  const view = render(
    <TxnDetailSheet
      txn={t}
      accounts={[USD_ACCOUNT, EUR_ACCOUNT]}
      categories={[]}
      tags={[]}
      onClose={vi.fn()}
      onReplaced={onReplaced}
    />,
  );
  return { view, onReplaced };
}

beforeEach(() => {
  h.transfer = undefined;
  h.candidates = [];
  h.unlink.mockClear();
  h.link.mockClear();
  // The real mutation hands its result to `onSuccess`; the sheet reflects the new
  // link from there rather than waiting for the list behind it to refetch.
  h.link.mockImplementation((...args: unknown[]) => {
    const opts = args[1] as { onSuccess?: (group: { transfer_group_id: string }) => void };
    opts?.onSuccess?.({ transfer_group_id: "grp-new" });
  });
});

describe("<TxnDetailSheet /> transfers", () => {
  it("surfaces the FX cost of a cross-currency pair, and names the other leg", () => {
    h.transfer = {
      transfer_group_id: "grp-1",
      matched_by: "manual",
      fx_cost_base: "-2.0000",
      legs: [EURO_LEG, DOLLAR_LEG],
    };
    renderSheet(txn({ ...EURO_LEG, transfer_group_id: "grp-1" }));

    // The counterpart is shown by name, not just by id.
    expect(screen.getByTestId("transfer-counterpart")).toHaveTextContent("From euro account");
    // ADR-0018: the real cost is displayed, never hidden by the exclusion.
    const cost = screen.getByTestId("transfer-fx-cost");
    expect(cost).toHaveTextContent("FX cost");
    expect(cost).toHaveTextContent("$2.00");
  });

  it("says there is no FX cost when both legs share a currency", () => {
    h.transfer = {
      transfer_group_id: "grp-1",
      matched_by: "manual",
      fx_cost_base: null,
      legs: [txn({ id: "txn-out", transfer_group_id: "grp-1" }),
             txn({ id: "txn-in", amount: "100.0000" })],
    };
    renderSheet(txn({ transfer_group_id: "grp-1" }));
    expect(screen.getByTestId("transfer-fx-cost")).toHaveTextContent("No FX cost");
  });

  it("does not claim there is no cost when a rate is simply missing", () => {
    // A cross-currency pair with no rate has no residual yet, which is not the
    // same statement as "this transfer was free".
    h.transfer = {
      transfer_group_id: "grp-1",
      matched_by: "manual",
      fx_cost_base: null,
      legs: [EURO_LEG, DOLLAR_LEG],
    };
    renderSheet(txn({ ...EURO_LEG, base_amount: null, transfer_group_id: "grp-1" }));
    expect(screen.getByTestId("transfer-fx-cost")).toHaveTextContent("no exchange rate");
  });

  it("navigates to the counterpart when it is opened", async () => {
    h.transfer = {
      transfer_group_id: "grp-1",
      matched_by: "manual",
      fx_cost_base: null,
      legs: [txn({ id: "txn-out", transfer_group_id: "grp-1" }), txn({ id: "txn-in" })],
    };
    const { onReplaced } = renderSheet(txn({ transfer_group_id: "grp-1" }));

    await userEvent.setup().click(screen.getByTestId("transfer-counterpart"));
    expect(onReplaced).toHaveBeenCalledWith(expect.objectContaining({ id: "txn-in" }));
  });

  it("offers unlink on a linked leg", async () => {
    h.transfer = {
      transfer_group_id: "grp-1",
      matched_by: "manual",
      fx_cost_base: null,
      legs: [txn({ id: "txn-out", transfer_group_id: "grp-1" }), txn({ id: "txn-in" })],
    };
    renderSheet(txn({ transfer_group_id: "grp-1" }));

    await userEvent.setup().click(screen.getByTestId("transfer-unlink"));
    expect(h.unlink).toHaveBeenCalledWith("grp-1", expect.anything());
  });

  it("shows each candidate's residual before the user commits, then links the pick", async () => {
    const other = txn({
      id: "txn-eur", account_id: EUR_ACCOUNT.id, amount: "-100.0000", currency: "EUR",
      base_amount: "-108.0000", description: "Euro side",
    });
    h.candidates = [
      { transaction: other, days_apart: 1, fx_cost_base: "-2.0000", within_tolerance: true },
    ];
    const { onReplaced } = renderSheet(txn({ id: "txn-usd", amount: "106.0000" }));

    // Nothing is fetched or shown until the user asks for it.
    expect(screen.queryByTestId("transfer-candidate-txn-eur")).toBeNull();
    await userEvent.setup().click(screen.getByTestId("transfer-match-open"));

    expect(screen.getByTestId("transfer-candidate-txn-eur")).toHaveTextContent("Euro side");
    expect(screen.getByTestId("transfer-candidate-cost-txn-eur")).toHaveTextContent("FX cost");
    expect(screen.getByTestId("transfer-candidate-cost-txn-eur")).toHaveTextContent("$2.00");

    await userEvent.setup().click(screen.getByTestId("transfer-candidate-txn-eur"));
    expect(h.link).toHaveBeenCalledWith(
      { from_txn_id: "txn-usd", to_txn_id: "txn-eur" },
      expect.anything(),
    );
    expect(onReplaced).toHaveBeenCalledWith(
      expect.objectContaining({ id: "txn-usd", transfer_group_id: "grp-new" }),
    );
  });

  it("flags a candidate whose spread is wider than a bank spread usually is", async () => {
    const other = txn({ id: "txn-odd", amount: "-500.0000", description: "Odd one out" });
    h.candidates = [
      { transaction: other, days_apart: 4, fx_cost_base: "-120.0000", within_tolerance: false },
    ];
    renderSheet(txn({ id: "txn-usd", amount: "380.0000", account_id: EUR_ACCOUNT.id }));

    await userEvent.setup().click(screen.getByTestId("transfer-match-open"));
    const cost = screen.getByTestId("transfer-candidate-cost-txn-odd");
    expect(cost).toHaveTextContent("FX cost");
    expect(cost).toHaveTextContent("check it is the right leg");
    // The FX cost is still offered: explicit linking is the override (ADR-0018).
    expect(screen.getByTestId("transfer-candidate-txn-odd")).toBeEnabled();
  });
});
