import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { NetWorthSeries } from "@/api/types";
import { formatMoney } from "@/lib/format";
import Reconciliation from "@/components/Reconciliation";

function series(over: Partial<NetWorthSeries> = {}): NetWorthSeries {
  return {
    base_currency: "USD",
    // The window and the cut the server answered for. `Reconciliation` renders
    // neither — it is here because the type requires them, and a fixture that
    // lied about which fields the API sends would stop being a contract test.
    start: "2026-01-01",
    end: "2026-12-31",
    granularity: "month",
    points: [{ date: "2026-09-20", net_worth: "1000.0000", missing: [] }],
    delta_net_worth: "1000.0000",
    net_cash_flow: "1200.0000",
    currency_revaluation: "-50.0000",
    market_appreciation: "250.0000",
    unexplained: "-400.0000",
    unexplained_by_account: [],
    warnings: [],
    attribution: "account",
    ...over,
  };
}

const step = (testid: string) => screen.getByTestId(testid).textContent ?? "";

describe("Reconciliation", () => {
  it("states each term of the identity, with the delta as the last word", () => {
    render(<Reconciliation series={series()} />);
    expect(step("reconciliation-cash-flow")).toContain(`+${formatMoney("1200.0000", "USD")}`);
    expect(step("reconciliation-revaluation")).toContain(formatMoney("-50.0000", "USD"));
    expect(step("reconciliation-appreciation")).toContain(`+${formatMoney("250.0000", "USD")}`);
    expect(screen.getByText("Change in net worth")).toBeTruthy();
    expect(screen.getByTestId("reconciliation").textContent).toContain(
      formatMoney("1000.0000", "USD"),
    );
  });

  it("carries the sign as a character, never as colour alone", () => {
    // A reader who cannot separate red from green still has to be able to tell a
    // gain from a loss (§7 item 1), so the sign is in the text.
    render(<Reconciliation series={series()} />);
    expect(step("reconciliation-cash-flow")).toContain("+");
    expect(step("reconciliation-revaluation")).toContain("−"); // U+2212, from formatMoney
  });

  it("gives a zero step no sign at all — it is neither a gain nor a loss", () => {
    render(<Reconciliation series={series({ currency_revaluation: "0.0000" })} />);
    const text = step("reconciliation-revaluation");
    expect(text).toContain(formatMoney("0.0000", "USD"));
    expect(text).not.toContain("+");
    expect(text).not.toContain("−");
  });

  it("explains a non-zero residual, and stays quiet when there is none", () => {
    const { unmount } = render(<Reconciliation series={series()} />);
    expect(screen.getByTestId("reconciliation-note")).toBeTruthy();
    unmount();

    render(<Reconciliation series={series({ unexplained: "0.0000", delta_net_worth: "1400.0000" })} />);
    expect(screen.queryByTestId("reconciliation-note")).toBeNull();
    expect(screen.queryByTestId("reconciliation-attribution")).toBeNull();
  });

  it("names the accounts behind a residual, in the order the service ranked them", () => {
    render(
      <Reconciliation
        series={series({
          unexplained_by_account: [
            { account_id: "a1", name: "Everyday Checking", amount: "900.0000" },
            { account_id: "a2", name: "Brokerage", amount: "-500.0000" },
          ],
        })}
      />,
    );
    const rows = screen.getAllByTestId("reconciliation-attribution-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      `Everyday Checking${formatMoney("900.0000", "USD")}`,
      `Brokerage${formatMoney("-500.0000", "USD")}`,
    ]);
    // "Of which", not "these add up to the number above": the parts too small to
    // name are still inside the residual, so the label must not promise a partition.
    expect(screen.getByTestId("reconciliation-attribution").textContent).toContain("Of which");
  });

  it("says so when the residual is too thin to blame on any one account", () => {
    render(<Reconciliation series={series({ unexplained: "0.0100" })} />);
    expect(screen.getByTestId("reconciliation-unattributed")).toBeTruthy();
  });

  it("renders every warning a term could not be computed without", () => {
    render(
      <Reconciliation
        series={series({ warnings: ["No rate for EUR on 2026-09-18.", "2 positions unpriced."] })}
      />,
    );
    const block = screen.getByTestId("reconciliation-warnings");
    expect(block.textContent).toContain("No rate for EUR on 2026-09-18.");
    expect(block.textContent).toContain("2 positions unpriced.");
  });

  it("hides the warnings block entirely when nothing is missing", () => {
    render(<Reconciliation series={series()} />);
    expect(screen.queryByTestId("reconciliation-warnings")).toBeNull();
  });
});
