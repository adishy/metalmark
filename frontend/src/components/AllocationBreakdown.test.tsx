import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Allocation, AllocationGroup } from "@/api/types";
import AllocationBreakdown from "@/components/AllocationBreakdown";
import { formatMoney } from "@/lib/format";

// The component owns its one call, so the hook is stubbed rather than standing up
// a QueryClient and a fetch mock. The stub is a plain function of `group_by`,
// which is what makes the "switching the control re-queries under the new
// grouping" assertion possible at all.
const h = vi.hoisted(() => ({ allocation: vi.fn() }));

vi.mock("@/api/investments", async () => {
  const actual = await vi.importActual<typeof import("@/api/investments")>("@/api/investments");
  return {
    ...actual,
    useAllocation: (groupBy: AllocationGroup) => h.allocation(groupBy),
  };
});

function query(data: Allocation | undefined, over: Record<string, unknown> = {}) {
  return { data, isPending: false, isError: false, error: null, refetch: vi.fn(), ...over };
}

const BY_SECURITY: Allocation = {
  as_of: "2026-09-20",
  base_currency: "USD",
  group_by: "security",
  total_base: "12345.67",
  rows: [
    { key: "sec-vti", label: "VTI", value_base: "10000.00", percent: "81.0012", holdings: 2 },
    { key: "sec-bnd", label: "BND", value_base: "2345.67", percent: "18.9988", holdings: 1 },
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
    { key: "etf", label: "etf", value_base: "10000.00", percent: "81.0012", holdings: 2 },
    { key: "mutual_fund", label: "mutual_fund", value_base: "2345.67", percent: "18.9988", holdings: 1 },
  ],
};

beforeEach(() => {
  h.allocation.mockReset();
  h.allocation.mockImplementation((groupBy: AllocationGroup) =>
    query(groupBy === "type" ? BY_TYPE : BY_SECURITY),
  );
});

describe("<AllocationBreakdown />", () => {
  it("renders a row per group with its value, share and position count", () => {
    render(<AllocationBreakdown />);

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
    render(<AllocationBreakdown />);

    const excluded = screen.getByTestId("allocation-excluded");
    // Both counts, and both words: "enter a price" and "enter an FX rate" are
    // different fixes, and this is the sentence that says the total is short.
    expect(excluded).toHaveTextContent("2 positions with no price");
    expect(excluded).toHaveTextContent("1 position with no rate");
    expect(excluded).toHaveTextContent("not counted as zero");
    // The failure this view exists to prevent: a position that cannot be valued
    // rendering as one worth nothing.
    expect(screen.getByTestId("allocation")).not.toHaveTextContent("$0.00");
  });

  it("states how old the oldest price behind the total is", () => {
    render(<AllocationBreakdown />);
    expect(screen.getByTestId("allocation-stale")).toHaveTextContent("Oldest price used: 3 days old.");
  });

  it("asks the API for a different grouping when the control changes", async () => {
    const user = userEvent.setup();
    render(<AllocationBreakdown />);
    expect(h.allocation).toHaveBeenLastCalledWith("security");

    await user.click(screen.getByTestId("allocation-groupby-type"));

    expect(h.allocation).toHaveBeenLastCalledWith("type");
    // And the new grouping's own rows are what is rendered — the wire token is
    // named as words rather than shown as `mutual_fund`.
    expect(screen.getByTestId("allocation-row-mutual_fund")).toHaveTextContent("Mutual fund");
    expect(screen.getByTestId("allocation-groupby-type")).toHaveAttribute("aria-selected", "true");
  });

  it("says a total is short rather than saying there is nothing, when nothing could be valued", () => {
    // Every position unpriced is emphatically not "No holdings yet": the
    // household holds things, and the honest statement is that none of them
    // could be valued.
    h.allocation.mockImplementation(() =>
      query({ ...BY_SECURITY, rows: [], total_base: "0", unpriced_positions: 4, no_rate_positions: 0 }),
    );
    render(<AllocationBreakdown />);

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
    render(<AllocationBreakdown />);

    expect(screen.getByTestId("allocation-empty")).toHaveTextContent("No holdings yet.");
    expect(screen.queryByTestId("allocation-unvalued")).not.toBeInTheDocument();
    // Nothing is excluded, so nothing is announced — a live region with an empty
    // sentence still announces.
    expect(screen.queryByTestId("allocation-excluded")).not.toBeInTheDocument();
  });

  it("bounds a nonzero share instead of rounding it away", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: [{ key: "sec-dust", label: "DUST", value_base: "0.01", percent: "0.0001", holdings: 1 }],
      }),
    );
    render(<AllocationBreakdown />);
    expect(screen.getByTestId("allocation-percent-sec-dust")).toHaveTextContent("<0.1%");
  });

  it("bounds a negative share too, and keeps its direction", () => {
    // A short position is not rounded away by the valuation, so a group's share
    // can be negative — and `(-0.04).toFixed(1)` is `"-0.0%"`, a non-zero share
    // displayed as zero. Bounding only `n > 0` left exactly that hole.
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: [{ key: "sec-short", label: "SHORT", value_base: "-0.04", percent: "-0.0004", holdings: 1 }],
      }),
    );
    render(<AllocationBreakdown />);

    const percent = screen.getByTestId("allocation-percent-sec-short");
    expect(percent).toHaveTextContent(">-0.1%");
    expect(percent).not.toHaveTextContent("0.0%");
    // The sign is not lost, and the value on the row still carries it.
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
          { key: "etf", label: "etf", value_base: "10000.00", percent: "99.9", holdings: 2 },
          // The unaccounted-cash plug is the one `type` row the server names in
          // prose. Renaming it would also collide it with the real cash holdings
          // it is not — "cash" is a genuine `security_type` (ADR-0033 §4).
          {
            key: "cash",
            label: "Unaccounted cash",
            value_base: "800.00",
            percent: "0.1",
            holdings: 1,
          },
        ],
      }),
    );
    render(<AllocationBreakdown />);
    // The renaming is the `type` grouping's job, so the control has to be there.
    await user.click(screen.getByTestId("allocation-groupby-type"));

    expect(screen.getByTestId("allocation-row-etf")).toHaveTextContent("ETF");
    expect(screen.getByTestId("allocation-row-cash")).toHaveTextContent("Unaccounted cash");
  });

  it("shows a load failure as a failure, and offers the retry", async () => {
    const refetch = vi.fn();
    h.allocation.mockImplementation(() =>
      query(undefined, { isError: true, error: new Error("upstream said no") , refetch }),
    );
    render(<AllocationBreakdown />);

    // The apostrophe is U+2019, as the component writes it (`&rsquo;`).
    expect(screen.getByTestId("allocation-error")).toHaveTextContent("Couldn’t load your allocation");
    expect(screen.getByTestId("allocation-error")).toHaveTextContent("upstream said no");
    await userEvent.setup().click(screen.getByTestId("allocation-error-retry"));
    expect(refetch).toHaveBeenCalled();
  });

  it("reserves the rows' space while loading rather than collapsing the card", () => {
    h.allocation.mockImplementation(() => query(undefined, { isPending: true, data: undefined }));
    render(<AllocationBreakdown />);
    expect(screen.getByTestId("allocation-loading")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your allocation");
  });
});
