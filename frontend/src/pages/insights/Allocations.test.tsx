import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import type { Allocation, AllocationGroup } from "@/api/types";
import Allocations from "@/pages/insights/Allocations";
import { formatDay } from "@/lib/dates";
import { formatMoney } from "@/lib/format";
import { token } from "@/theme/chartTokens";

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

  it("names the treemap's groups in the words the list under it uses", async () => {
    const user = userEvent.setup();
    renderAllocations();

    await user.click(screen.getByTestId("allocation-groupby-type"));

    // The `type` rows arrive as wire tokens, and the list renames them. So does
    // the picture: the chart's own name, the tile drawn over each row and the
    // tooltip all say "Mutual fund" and "ETF", because a tile reading
    // `mutual_fund` above a row reading "Mutual fund" is two names for one group.
    const name = screen.getByTestId("allocation-treemap").getAttribute("aria-label") ?? "";
    expect(name).toContain("ETF is the largest");
    expect(name).not.toContain("etf is the largest");
    // Found rather than trusted from the fixture, so the assertion above is
    // "the picture says what the list says" and not "the picture says a string
    // this test also wrote down".
    expect(screen.getByTestId("allocation-row-etf")).toHaveTextContent("ETF");
    expect(screen.getByTestId("allocation-row-mutual_fund")).toHaveTextContent("Mutual fund");
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

  // ---- issue #34: the treemap view ----------------------------------------

  it("opens on the treemap, and states the picture's finding in the chart's own name", () => {
    renderAllocations();

    const chart = screen.getByTestId("allocation-treemap");
    // Canvas is invisible to assistive technology, so this accessible name *is*
    // what the chart says — and it states the finding rather than the chart type.
    expect(chart).toHaveAttribute("role", "img");
    const name = chart.getAttribute("aria-label") ?? "";
    expect(name).toContain(`Allocation by security, as of ${formatDay("2026-09-20")}`);
    expect(name).toContain(`${formatMoney("12345.67", "USD")} across 2 groups`);
    expect(name).toContain(`VTI is the largest at ${formatMoney("10000.00", "USD")} (81.0%)`);
    // The list under it is the text equivalent §2.9 requires, and the picture
    // is added above it rather than instead of it.
    expect(screen.getByTestId("allocation-rows")).toBeInTheDocument();
    expect(screen.getByTestId("allocation-row-sec-vti")).toHaveTextContent("VTI");
  });

  it("keys each row to its tile with the colour the tile is drawn in", () => {
    renderAllocations();

    // The palette in row order: the same slots `treemapOption` colours the tiles
    // with, so the swatch is a key to the picture rather than a decoration.
    expect(screen.getByTestId("allocation-swatch-sec-vti")).toHaveStyle({
      backgroundColor: token("chart-1"),
    });
    expect(screen.getByTestId("allocation-swatch-sec-bnd")).toHaveStyle({
      backgroundColor: token("chart-2"),
    });
    // Not announced: the row beside it says the name, and a colour read aloud is
    // noise.
    expect(screen.getByTestId("allocation-swatch-sec-vti")).toHaveAttribute("aria-hidden", "true");
  });

  it("switches to the list alone and back, keeping every row in both", async () => {
    const user = userEvent.setup();
    renderAllocations();

    await user.click(screen.getByTestId("allocation-view-list"));
    expect(screen.getByTestId("allocation-view-list")).toHaveAttribute("aria-selected", "true");
    // No picture, so nothing to key: the swatches go with it.
    expect(screen.queryByTestId("allocation-treemap")).not.toBeInTheDocument();
    expect(screen.queryByTestId("allocation-swatch-sec-vti")).not.toBeInTheDocument();
    // The figures are the same either way — this switch is about the picture,
    // not about what the page reports.
    expect(screen.getByTestId("allocation-rows")).toBeInTheDocument();
    expect(screen.getByTestId("allocation-value-sec-vti")).toHaveTextContent(
      formatMoney("10000.00", "USD"),
    );

    await user.click(screen.getByTestId("allocation-view-treemap"));
    expect(screen.getByTestId("allocation-treemap")).toBeInTheDocument();
    expect(screen.getByTestId("allocation-swatch-sec-vti")).toBeInTheDocument();
  });

  it("names a row the picture cannot draw, and keeps it in the list", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: [
          BY_SECURITY.rows[0],
          {
            key: "sec-short", label: "SHORT", value_base: "-1000.00", percent: "-10.0010",
            holdings: 1, sources: [],
          },
        ],
      }),
    );
    renderAllocations();

    const notDrawn = screen.getByTestId("allocation-not-drawn");
    expect(notDrawn).toHaveTextContent(
      `A treemap's area is value, so this row is not drawn: SHORT (${formatMoney("-1000.00", "USD")}).`,
    );
    expect(notDrawn).toHaveTextContent("It is in the list below, at the figure shown.");
    // The sentence is in the chart's name as well as under it, so a screen
    // reader gets the caveat with the picture rather than in a separate region.
    expect(screen.getByTestId("allocation-treemap").getAttribute("aria-label")).toContain(
      `SHORT (${formatMoney("-1000.00", "USD")})`,
    );
    // The row is still reported, and has no tile colour to be keyed to.
    expect(screen.getByTestId("allocation-row-sec-short")).toHaveTextContent("SHORT");
    expect(screen.queryByTestId("allocation-swatch-sec-short")).not.toBeInTheDocument();
  });

  it("measures its sentence against the total the list prints, tiles or no tiles", () => {
    // A household with a short: three groups and, once the short's $1,000 is out
    // of the total, $11,345.67. The tiles sum to $12,345.67 — a short has no
    // area — so "the tiles' share" and "the allocation's share" are different
    // numbers, and the sentence has to be built out of the one the list uses:
    // the same total the `Total` row prints, and the same shares the rows print.
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        total_base: "11345.67",
        rows: [
          { key: "sec-vti", label: "VTI", value_base: "10000.00", percent: "88.1398", holdings: 2, sources: [] },
          { key: "sec-bnd", label: "BND", value_base: "2345.67", percent: "20.6769", holdings: 1, sources: [] },
          {
            key: "sec-short", label: "SHORT", value_base: "-1000.00", percent: "-8.8167",
            holdings: 1, sources: [],
          },
        ],
      }),
    );
    renderAllocations();

    const total = screen.getByTestId("allocation-total").textContent ?? "";
    const vtiShare = screen.getByTestId("allocation-percent-sec-vti").textContent ?? "";
    const name = screen.getByTestId("allocation-treemap").getAttribute("aria-label") ?? "";
    // The finding's total is the list's total, character for character — one
    // number from one place, so the two can never drift apart.
    expect(total).toBe(formatMoney("11345.67", "USD"));
    expect(name).toContain(total);
    // Three groups in the allocation, two of them in the picture: the count is
    // the rows the printed total is made of, and it says how many are drawn.
    expect(name).toContain("across 3 groups, 2 of them drawn");
    // The largest group's share is the share that group's row prints — 88.1% of
    // the allocation, which is not the 81.0% of the tiles' area it covers.
    expect(vtiShare).toBe("88.1%");
    expect(name).toContain(`${formatMoney("10000.00", "USD")} (${vtiShare})`);
  });

  it("explains a treemap with nothing to draw, rather than leaving the view blank", () => {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        total_base: "-1000.00",
        rows: [
          {
            key: "sec-short", label: "SHORT", value_base: "-1000.00", percent: "-100.0000",
            holdings: 1, sources: [],
          },
        ],
      }),
    );
    renderAllocations();

    expect(screen.queryByTestId("allocation-treemap")).not.toBeInTheDocument();
    expect(screen.getByTestId("allocation-not-drawn")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("allocation-row-sec-short")).toBeInTheDocument();
  });

  // ---- issue #31: a long ranking is bounded, not a page ----------------------

  /** The geometry — the box's real height and that it scrolls — is measured in
   *  `e2e/desktop.spec.ts`, where there is a layout. What is testable here is the
   *  shape: which lists get a region, what it is called, and that the cap never
   *  drops a row. */
  function manyRows(n: number) {
    h.allocation.mockImplementation(() =>
      query({
        ...BY_SECURITY,
        rows: Array.from({ length: n }, (_, i) => ({
          key: `sec-${i}`,
          label: `SEC${i}`,
          value_base: "100.00",
          percent: "1.0",
          holdings: 1,
          sources: [],
        })),
      }),
    );
  }

  it("bounds a ranking long enough to scroll, in a region a keyboard can reach", () => {
    manyRows(12);
    renderAllocations();

    const region = screen.getByTestId("allocation-rows-region");
    // §4.7: an `overflow` box is not focusable on its own, and a scroll region a
    // keyboard cannot scroll fails 2.1.1. The label names the grouping and how
    // many rows are inside it.
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveAttribute("aria-label", "Allocation by security, 12 rows");
    expect(region.className).toContain("lg:max-h-96");
    expect(region.className).toContain("lg:overflow-y-auto");
    // Every row the server sent is in the DOM: the cap is on the box, not on the
    // list.
    expect(screen.getAllByTestId(/^allocation-row-/)).toHaveLength(12);
    // And the total stays outside the box, so it is on screen while the ranking
    // scrolls under it — the point of bounding rather than capping the data.
    expect(region).not.toContainElement(screen.getByTestId("allocation-total"));
  });

  it("leaves a ranking that fits alone: no region, and no tab stop that does nothing", () => {
    manyRows(7);
    renderAllocations();

    const box = screen.getByTestId("allocation-rows-region");
    expect(box).not.toHaveAttribute("role");
    expect(box).not.toHaveAttribute("tabindex");
    expect(box).not.toHaveAttribute("aria-label");
    expect(box.className).not.toContain("max-h");
    expect(screen.getAllByTestId(/^allocation-row-/)).toHaveLength(7);
  });
});
