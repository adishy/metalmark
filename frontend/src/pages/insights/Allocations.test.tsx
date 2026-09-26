import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import type { Allocation, AllocationGroup } from "@/api/types";
import Allocations from "@/pages/insights/Allocations";
import { formatMoney } from "@/lib/format";

// The component owns its one call, so the hook is stubbed rather than standing up
// a QueryClient and a fetch mock. The stub is a plain function of
// `(groupBy, includeCashAccounts)`, which is what makes "the cash toggle
// re-queries with the flag set" an assertion this file can make at all.
const h = vi.hoisted(() => ({ allocation: vi.fn() }));

vi.mock("@/api/investments", async () => {
  const actual = await vi.importActual<typeof import("@/api/investments")>("@/api/investments");
  return {
    ...actual,
    useAllocation: (groupBy: AllocationGroup, includeCashAccounts: boolean) =>
      h.allocation(groupBy, includeCashAccounts),
  };
});

function query(data: Allocation | undefined, over: Record<string, unknown> = {}) {
  return { data, isPending: false, isError: false, error: null, refetch: vi.fn(), ...over };
}

const BY_SECURITY: Allocation = {
  as_of: "2026-09-20",
  base_currency: "USD",
  group_by: "security",
  include_cash_accounts: false,
  total_base: "12345.67",
  rows: [
    {
      key: "sec-vti", label: "VTI", value_base: "10000.00", percent: "81.0012", holdings: 2,
      sources: [
        {
          account_id: "acc-1", account_name: "Brokerage", institution: null,
          value_base: "7000.00", share_of_group: "70.0000",
          quantity: "70", price: "100", price_currency: "USD", price_date: "2026-09-19",
        },
        {
          account_id: "acc-2", account_name: "IRA", institution: "Fidelity",
          value_base: "3000.00", share_of_group: "30.0000",
          quantity: "30", price: "100", price_currency: "USD", price_date: "2026-09-19",
        },
      ],
    },
    {
      key: "sec-bnd", label: "BND", value_base: "2345.67", percent: "18.9988", holdings: 1,
      sources: [
        {
          account_id: "acc-1", account_name: "Brokerage", institution: null,
          value_base: "2345.67", share_of_group: "100.0000",
          quantity: "20", price: "117.2835", price_currency: "USD", price_date: "2026-09-19",
        },
      ],
    },
  ],
  unpriced_positions: 2,
  no_rate_positions: 1,
  max_stale_days: 3,
};

// The server labels a `type` row with the wire token (`_add(h.security_type,
// h.security_type, …)` in app/services/investments.py), which is the whole reason
// the naming happens on this side.
const BY_TYPE: Allocation = {
  ...BY_SECURITY,
  group_by: "type",
  rows: [
    { key: "etf", label: "etf", value_base: "10000.00", percent: "81.0012", holdings: 2, sources: [] },
    {
      key: "mutual_fund", label: "mutual_fund", value_base: "2345.67", percent: "18.9988",
      holdings: 1, sources: [],
    },
  ],
};

function renderAllocations() {
  return render(
    <MemoryRouter>
      <Allocations />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  h.allocation.mockReset();
  h.allocation.mockImplementation((groupBy: AllocationGroup) =>
    query(groupBy === "type" ? BY_TYPE : BY_SECURITY),
  );
});

describe("<Allocations />", () => {
  it("renders a row per group with its value, share and position count", () => {
    renderAllocations();

    const row = screen.getByTestId("allocation-row-sec-vti");
    expect(row).toHaveTextContent("VTI");
    expect(screen.getByTestId("allocation-value-sec-vti")).toHaveTextContent(
      formatMoney("10000.00", "USD"),
    );
    expect(screen.getByTestId("allocation-percent-sec-vti")).toHaveTextContent("81.0%");
    // A 3% line that is one holding and a 3% line that is thirty read very
    // differently, so the count is on the row and not only in the total.
    expect(screen.getByTestId("allocation-holdings-sec-vti")).toHaveTextContent("2 positions");
    expect(screen.getByTestId("allocation-holdings-sec-bnd")).toHaveTextContent("1 position");

    expect(screen.getByTestId("allocation-total")).toHaveTextContent(formatMoney("12345.67", "USD"));
    expect(screen.getByTestId("allocation-as-of")).toHaveTextContent("as of");
  });

  it("counts the positions it could not value beside the total, never as a zero in it", () => {
    renderAllocations();

    const excluded = screen.getByTestId("allocation-excluded");
    expect(excluded).toHaveTextContent("2 positions with no price");
    expect(excluded).toHaveTextContent("1 position with no rate");
    expect(excluded).toHaveTextContent("not counted as zero");
    expect(screen.getByTestId("allocation")).not.toHaveTextContent("$0.00");
  });

  it("states how old the oldest price behind the total is", () => {
    renderAllocations();
    expect(screen.getByTestId("allocation-stale")).toHaveTextContent("Oldest price used: 3 days old.");
  });

  it("asks the API for a different grouping when the control changes", async () => {
    const user = userEvent.setup();
    renderAllocations();
    expect(h.allocation).toHaveBeenLastCalledWith("security", true);

    await user.click(screen.getByTestId("allocation-groupby-type"));

    expect(h.allocation).toHaveBeenLastCalledWith("type", true);
    expect(screen.getByTestId("allocation-row-mutual_fund")).toHaveTextContent("Mutual fund");
    expect(screen.getByTestId("allocation-groupby-type")).toHaveAttribute("aria-selected", "true");
  });

  it("says a total is short rather than saying there is nothing, when nothing could be valued", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY, rows: [], total_base: "0", unpriced_positions: 4, no_rate_positions: 0,
      }),
    );
    renderAllocations();

    expect(screen.getByTestId("allocation-unvalued")).toHaveTextContent(
      "No position could be valued",
    );
    expect(screen.getByTestId("allocation-unvalued")).toHaveTextContent("4 positions with no price");
    expect(screen.queryByTestId("allocation-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("allocation-rows")).not.toBeInTheDocument();
  });

  it("shows the empty state only when there is nothing held and nothing missing", () => {
    h.allocation.mockImplementation(() =>
      query({ ...BY_SECURITY, rows: [], total_base: "0", unpriced_positions: 0, no_rate_positions: 0 }),
    );
    renderAllocations();

    expect(screen.getByTestId("allocation-empty")).toHaveTextContent("No holdings yet.");
    expect(screen.queryByTestId("allocation-unvalued")).not.toBeInTheDocument();
    expect(screen.queryByTestId("allocation-excluded")).not.toBeInTheDocument();
  });

  it("bounds a nonzero share instead of rounding it away", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: [
          { key: "sec-dust", label: "DUST", value_base: "0.01", percent: "0.0001", holdings: 1, sources: [] },
        ],
      }),
    );
    renderAllocations();
    expect(screen.getByTestId("allocation-percent-sec-dust")).toHaveTextContent("<0.1%");
  });

  it("bounds a negative share too, and keeps its direction", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: [
          {
            key: "sec-short", label: "SHORT", value_base: "-0.04", percent: "-0.0004", holdings: 1,
            sources: [],
          },
        ],
      }),
    );
    renderAllocations();

    const percent = screen.getByTestId("allocation-percent-sec-short");
    expect(percent).toHaveTextContent(">-0.1%");
    expect(percent).not.toHaveTextContent("0.0%");
    expect(screen.getByTestId("allocation-value-sec-short")).toHaveTextContent(
      formatMoney("-0.04", "USD"),
    );
  });

  it("keeps a name the server gave a row, rather than renaming a wire token over it", async () => {
    const user = userEvent.setup();
    h.allocation.mockImplementation(() =>
      query({
        ...BY_TYPE,
        rows: [
          { key: "etf", label: "etf", value_base: "10000.00", percent: "99.9", holdings: 2, sources: [] },
          {
            key: "cash", label: "Unaccounted cash", value_base: "800.00", percent: "0.1",
            holdings: 1, sources: [],
          },
        ],
      }),
    );
    renderAllocations();
    await user.click(screen.getByTestId("allocation-groupby-type"));

    expect(screen.getByTestId("allocation-row-etf")).toHaveTextContent("ETF");
    expect(screen.getByTestId("allocation-row-cash")).toHaveTextContent("Unaccounted cash");
  });

  it("shows a load failure as a failure, and offers the retry", async () => {
    const refetch = vi.fn();
    h.allocation.mockImplementation(() =>
      query(undefined, { isError: true, error: new Error("upstream said no"), refetch }),
    );
    renderAllocations();

    expect(screen.getByTestId("allocation-error")).toHaveTextContent("Couldn’t load your allocation");
    expect(screen.getByTestId("allocation-error")).toHaveTextContent("upstream said no");
    await userEvent.setup().click(screen.getByTestId("allocation-error-retry"));
    expect(refetch).toHaveBeenCalled();
  });

  it("reserves the rows' space while loading rather than collapsing the card", () => {
    h.allocation.mockImplementation(() => query(undefined, { isPending: true, data: undefined }));
    renderAllocations();
    expect(screen.getByTestId("allocation-loading")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your allocation");
  });

  // ---- ADR-0054: bank cash + the tap-through detail sheet -------------------

  it("defaults the cash toggle on, and remembers a change across a remount", async () => {
    const user = userEvent.setup();
    const { unmount } = renderAllocations();
    expect(screen.getByTestId("allocation-include-cash")).toBeChecked();
    expect(h.allocation).toHaveBeenLastCalledWith("security", true);

    await user.click(screen.getByTestId("allocation-include-cash"));
    expect(h.allocation).toHaveBeenLastCalledWith("security", false);
    unmount();

    renderAllocations();
    expect(screen.getByTestId("allocation-include-cash")).not.toBeChecked();
    expect(h.allocation).toHaveBeenLastCalledWith("security", false);
  });

  it("opens a detail sheet naming the row's value, share and source accounts", async () => {
    const user = userEvent.setup();
    renderAllocations();

    await user.click(screen.getByTestId("allocation-row-sec-vti"));

    expect(screen.getByTestId("allocation-detail-value")).toHaveTextContent(
      formatMoney("10000.00", "USD"),
    );
    expect(screen.getByTestId("allocation-detail-percent")).toHaveTextContent("81.0%");
    const sources = screen.getByTestId("allocation-detail-sources");
    expect(sources).toHaveTextContent("Brokerage");
    expect(sources).toHaveTextContent("IRA");
    // A security row's sources carry quantity × price, not just a share.
    expect(screen.getByTestId("allocation-detail-source-acc-1")).toHaveTextContent("70 × $100.00");
    expect(screen.getByTestId("allocation-detail-source-acc-2")).toHaveTextContent("30 × $100.00");
  });

  it("does not show quantity × price for a non-security grouping's sources", async () => {
    const user = userEvent.setup();
    h.allocation.mockImplementation(() =>
      query({
        ...BY_TYPE,
        rows: [
          {
            key: "etf", label: "etf", value_base: "10000.00", percent: "100.0", holdings: 1,
            sources: [
              {
                account_id: "acc-1", account_name: "Brokerage", institution: null,
                value_base: "10000.00", share_of_group: "100.0000",
                quantity: null, price: null, price_currency: null, price_date: null,
              },
            ],
          },
        ],
      }),
    );
    renderAllocations();
    await user.click(screen.getByTestId("allocation-groupby-type"));
    await user.click(screen.getByTestId("allocation-row-etf"));

    expect(screen.getByTestId("allocation-detail-source-acc-1")).not.toHaveTextContent("×");
    expect(screen.getByTestId("allocation-detail-source-acc-1")).toHaveTextContent("100.0% of this group");
  });

  it("closes the detail sheet, and switching rows shows the new one", async () => {
    const user = userEvent.setup();
    renderAllocations();

    await user.click(screen.getByTestId("allocation-row-sec-vti"));
    expect(screen.getByTestId("allocation-detail")).toBeInTheDocument();
    await user.click(screen.getByTestId("allocation-detail-close"));
    expect(screen.queryByTestId("allocation-detail")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("allocation-row-sec-bnd"));
    expect(screen.getByTestId("allocation-detail-value")).toHaveTextContent(
      formatMoney("2345.67", "USD"),
    );
  });
});
