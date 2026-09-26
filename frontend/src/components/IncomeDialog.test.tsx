import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Owner, OwnerIncomeProfile, Paystub } from "@/api/types";
import IncomeDialog from "@/components/IncomeDialog";

// The dialog owns its six calls (profile read/write, paystub list/create/
// update/delete), so stub them rather than standing up a QueryClient and a
// fetch mock for what is a form test. The mutate calls are the assertions:
// the payloads the forms build are the contract with the server (ADR-0052).
const h = vi.hoisted(() => ({
  profile: null as OwnerIncomeProfile | null,
  paystubs: [] as Paystub[],
  updateProfile: vi.fn(),
  createPaystub: vi.fn(),
  updatePaystub: vi.fn(),
  deletePaystub: vi.fn(),
}));

vi.mock("@/api/income", () => ({
  useIncomeProfile: () => ({
    data: h.profile,
    isPending: false,
    isError: false,
    error: null,
  }),
  usePaystubs: () => ({
    data: h.paystubs,
    isPending: false,
    isError: false,
    error: null,
  }),
  useUpdateIncomeProfile: () => ({
    mutate: h.updateProfile,
    isPending: false,
    isError: false,
    error: null,
  }),
  useCreatePaystub: () => ({
    mutate: h.createPaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
  useUpdatePaystub: () => ({
    mutate: h.updatePaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
  useDeletePaystub: () => ({
    mutate: h.deletePaystub,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

const ALICE: Owner = { id: "person-1", name: "Alice", kind: "person", sort: 1 };

/** What GET returns before the household has stated anything: the envelope is
 *  always there, ``profile`` inside it is null. */
const NO_PROFILE: OwnerIncomeProfile = {
  owner_id: ALICE.id,
  profile: null,
  summary: { annualized_gross: null, ytd: [], effective_tax_rate: null },
};

const PROFILE: OwnerIncomeProfile = {
  owner_id: ALICE.id,
  profile: {
    id: "prof-1",
    currency: "USD",
    annual_gross_income: "120000.00",
    pay_frequency: "biweekly",
    filing_status: "single",
    tax_region: "US-CA",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  summary: {
    annualized_gross: "120000.00",
    ytd: [{ kind: "tax", amount: "1400.00" }],
    effective_tax_rate: "0.1517",
  },
};

const PAYSTUB: Paystub = {
  id: "pay-1",
  owner_id: ALICE.id,
  pay_date: "2026-09-15",
  period_start: null,
  period_end: null,
  employer: "Acme Corp",
  currency: "USD",
  gross: "4615.38",
  net: "3715.38",
  lines: [
    { id: "line-1", kind: "earning", label: "Salary", amount: "4615.38", ytd_amount: null, position: 0 },
    { id: "line-2", kind: "tax", label: "Federal", amount: "700.00", ytd_amount: null, position: 1 },
    {
      id: "line-3",
      kind: "pre_tax_deduction",
      label: "401(k)",
      amount: "200.00",
      ytd_amount: null,
      position: 2,
    },
  ],
  created_at: "2026-09-16T00:00:00Z",
  updated_at: "2026-09-16T00:00:00Z",
};

function renderDialog() {
  return render(
    <IncomeDialog owner={ALICE} householdCurrency="USD" onClose={vi.fn()} />,
  );
}

beforeEach(() => {
  h.profile = NO_PROFILE;
  h.paystubs = [];
  h.updateProfile.mockReset();
  h.createPaystub.mockReset();
  h.updatePaystub.mockReset();
  h.deletePaystub.mockReset();
});

describe("<IncomeDialog /> profile", () => {
  it("names the owner in the title and shows an unstated profile as blanks", () => {
    renderDialog();
    expect(screen.getByTestId("income-dialog")).toHaveTextContent("Income & pay — Alice");
    expect(screen.getByTestId("income-gross")).toHaveValue("");
    expect(screen.getByTestId("summary-annualized-gross")).toHaveTextContent("—");
  });

  it("shows the stored profile and the derived summary", () => {
    h.profile = PROFILE;
    renderDialog();
    expect(screen.getByTestId("income-gross")).toHaveValue("120000.00");
    expect(screen.getByTestId("income-frequency")).toHaveValue("biweekly");
    expect(screen.getByTestId("income-filing-status")).toHaveValue("single");
    expect(screen.getByTestId("income-region")).toHaveValue("US-CA");
    expect(screen.getByTestId("summary-ytd-tax")).toHaveTextContent("$1,400.00");
    expect(screen.getByTestId("summary-effective-tax-rate")).toHaveTextContent("15.2%");
  });

  it("saves the profile, sending null for what was cleared", async () => {
    h.profile = PROFILE;
    renderDialog();
    const user = userEvent.setup();
    await user.clear(screen.getByTestId("income-gross"));
    await user.clear(screen.getByTestId("income-region"));
    await user.click(screen.getByTestId("income-profile-save"));
    expect(h.updateProfile).toHaveBeenCalledWith({
      annual_gross_income: null,
      currency: "USD",
      pay_frequency: "biweekly",
      filing_status: "single",
      tax_region: null,
    });
  });
});

describe("<IncomeDialog /> paystubs", () => {
  it("lists the owner's paystubs with their net", () => {
    h.paystubs = [PAYSTUB];
    renderDialog();
    const row = screen.getByTestId(`paystub-row-${PAYSTUB.id}`);
    expect(row).toHaveTextContent("Acme Corp");
    expect(row).toHaveTextContent("$3,715.38");
  });

  it("says so when there are none yet", () => {
    renderDialog();
    expect(screen.getByTestId("no-paystubs")).toBeInTheDocument();
  });

  it("creates a paystub from the editor, lines in the order entered", async () => {
    renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("add-paystub"));

    await user.type(screen.getByTestId("paystub-employer"), "Acme Corp");
    await user.type(screen.getByTestId("paystub-gross"), "4615.38");
    await user.type(screen.getByTestId("paystub-net"), "3715.38");
    await user.click(screen.getByTestId("paystub-add-line-earning"));
    await user.type(screen.getByLabelText("Earning label"), "Salary");
    await user.clear(screen.getByLabelText("Earning amount"));
    await user.type(screen.getByLabelText("Earning amount"), "4615.38");
    await user.click(screen.getByTestId("paystub-add-line-tax"));
    await user.type(screen.getByLabelText("Tax label"), "Federal");
    await user.clear(screen.getByLabelText("Tax amount"));
    await user.type(screen.getByLabelText("Tax amount"), "700.00");
    await user.click(screen.getByTestId("paystub-add-line-pre_tax_deduction"));
    await user.type(screen.getByLabelText("Pre-tax deduction label"), "401(k)");
    await user.clear(screen.getByLabelText("Pre-tax deduction amount"));
    await user.type(screen.getByLabelText("Pre-tax deduction amount"), "200.00");

    await user.click(screen.getByTestId("paystub-save"));
    expect(h.createPaystub).toHaveBeenCalledTimes(1);
    const body = h.createPaystub.mock.calls[0][0];
    expect(body.gross).toBe("4615.38");
    expect(body.net).toBe("3715.38");
    expect(body.employer).toBe("Acme Corp");
    expect(body.lines.map((l: { kind: string; label: string; position: number }) => [l.kind, l.label, l.position])).toEqual([
      ["earning", "Salary", 0],
      ["tax", "Federal", 1],
      ["pre_tax_deduction", "401(k)", 2],
    ]);
  });

  it("warns in the editor when the lines do not add up to the header", async () => {
    renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("add-paystub"));
    await user.type(screen.getByTestId("paystub-gross"), "1000");
    await user.type(screen.getByTestId("paystub-net"), "1000");
    await user.click(screen.getByTestId("paystub-add-line-earning"));
    await user.clear(screen.getByLabelText("Earning amount"));
    await user.type(screen.getByLabelText("Earning amount"), "900");
    expect(screen.getByTestId("paystub-mismatch")).toBeInTheDocument();
    expect(h.createPaystub).not.toHaveBeenCalled();
  });

  it("edits an existing paystub prefilled, and saves through PATCH", async () => {
    h.paystubs = [PAYSTUB];
    renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId(`paystub-edit-${PAYSTUB.id}`));
    expect(screen.getByTestId("paystub-gross")).toHaveValue("4615.38");
    const form = screen.getByTestId("paystub-form");
    expect(within(form).getByDisplayValue("Salary")).toBeInTheDocument();
    await user.clear(screen.getByTestId("paystub-employer"));
    await user.type(screen.getByTestId("paystub-employer"), "Acme LLC");
    await user.click(screen.getByTestId("paystub-save"));
    expect(h.updatePaystub).toHaveBeenCalledTimes(1);
    const arg = h.updatePaystub.mock.calls[0][0];
    expect(arg.id).toBe(PAYSTUB.id);
    expect(arg.body.employer).toBe("Acme LLC");
    // Lines ride along untouched-as-entered: replace-all means the editor
    // always sends the whole set it is showing.
    expect(arg.body.lines).toHaveLength(3);
  });

  it("asks before deleting, then deletes", async () => {
    h.paystubs = [PAYSTUB];
    renderDialog();
    const user = userEvent.setup();
    await user.click(screen.getByTestId(`paystub-delete-${PAYSTUB.id}`));
    expect(screen.getByTestId(`paystub-delete-confirm-${PAYSTUB.id}`)).toBeInTheDocument();
    expect(h.deletePaystub).not.toHaveBeenCalled();
    await user.click(screen.getByTestId(`paystub-delete-confirm-${PAYSTUB.id}`));
    expect(h.deletePaystub).toHaveBeenCalledWith(PAYSTUB.id, expect.anything());
  });
});
