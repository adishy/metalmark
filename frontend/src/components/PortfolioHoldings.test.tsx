import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AccountValuation, Holding, HoldingValue, Portfolio } from "@/api/types";
import PortfolioHoldings from "@/components/PortfolioHoldings";
import { formatMoney } from "@/lib/format";

// Both calls this view makes are stubbed together, because the ADR-0034 line only
// exists when the two responses are joined: `/investments/portfolio` values a
// position, `/investments/holdings` says where its quantity came from. `positionKey`
// and `indexHoldings` are deliberately the *real* ones — they are the join, and a
// stubbed copy would let the component and this test agree on a key the app does
// not use.
const h = vi.hoisted(() => ({ portfolio: vi.fn(), holdings: vi.fn() }));

vi.mock("@/api/investments", async () => {
  const actual = await vi.importActual<typeof import("@/api/investments")>("@/api/investments");
  return {
    ...actual,
    usePortfolio: () => h.portfolio(),
    useHoldings: () => h.holdings(),
  };
});

function query<T>(data: T | undefined, over: Record<string, unknown> = {}) {
  return { data, isPending: false, isError: false, error: null, refetch: vi.fn(), ...over };
}

function valued(over: Partial<HoldingValue> & Pick<HoldingValue, "security_id" | "name">): HoldingValue {
  return {
    holding_id: `hold-${over.security_id}`,
    account_id: "acct-broker",
    ticker: null,
    security_type: "etf",
    quantity: "10.00000000",
    price: null,
    price_date: null,
    price_currency: null,
    value_native: null,
    value_account: null,
    value_base: null,
    stale_days: null,
    reason: null,
    ...over,
  };
}

const VTI = valued({
  security_id: "sec-vti",
  name: "Vanguard Total Market",
  ticker: "VTI",
  security_type: "etf",
  quantity: "10.00000000",
  price: "300.00000000",
  price_date: "2026-09-18",
  price_currency: "USD",
  value_native: "3000.00",
  value_account: "3000.00",
  value_base: "3000.00",
  stale_days: 2,
});

// A position whose price nobody has entered. It has no value to add to any total
// and, critically, no zero either.
const MYSTERY = valued({
  security_id: "sec-mystery",
  name: "Mystery Corp",
  quantity: "5.00000000",
  reason: "no_price",
});

// Priced in EUR on an account whose currency is USD: the position *is* known, only
// the conversion is missing.
const EUROBOND = valued({
  security_id: "sec-euro",
  name: "Euro Bond",
  security_type: "bond",
  quantity: "10.00000000",
  price: "100.00000000",
  price_date: "2026-09-19",
  price_currency: "EUR",
  value_native: "1000.00",
  stale_days: 1,
  reason: "no_rate",
});

// ADR-0033 §4: uninvested cash is a real holding with a real value.
const CASH = valued({
  security_id: "sec-cash",
  name: "Cash",
  security_type: "cash",
  quantity: "1200.00000000",
  price: "1.00000000",
  price_date: "2026-09-20",
  price_currency: "USD",
  value_native: "1200.00",
  value_account: "1200.00",
  value_base: "1200.00",
  stale_days: 0,
});

const BROKER: AccountValuation = {
  account_id: "acct-broker",
  name: "Brokerage",
  currency: "USD",
  balance_source: "derived",
  balance_account: "4200.00",
  market_value_account: "4200.00",
  market_value_base: "4200.00",
  // Null for a `derived` account: Σ(holdings) is the balance, and the server only
  // fills this for a `stated` one.
  stated_balance_base: null,
  unaccounted_cash_base: "0.0000",
  holdings: [VTI, MYSTERY, EUROBOND, CASH],
  unpriced: 1,
  no_rate: 1,
  oldest_price_date: "2026-09-18",
  max_stale_days: 2,
  is_fully_valued: false,
};

const PORTFOLIO: Portfolio = {
  as_of: "2026-09-20",
  base_currency: "USD",
  total_base: "4200.00",
  accounts: [BROKER],
};

function recorded(over: Partial<Holding> & Pick<Holding, "security_id">): Holding {
  return {
    id: `hold-${over.security_id}`,
    account_id: "acct-broker",
    security: {
      id: over.security_id,
      name: "Vanguard Total Market",
      ticker: null,
      security_type: "etf",
      currency: "USD",
      is_manual: true,
    },
    quantity: "10.00000000",
    cost_basis: null,
    quantity_source: "manual",
    basis_source: "manual",
    manual_quantity: null,
    manual_cost_basis: null,
    as_of: null,
    ...over,
  };
}

// The ADR-0034 case: recorded trades are the position (12), and a human typed 10.
const HOLDINGS: Holding[] = [
  recorded({
    security_id: "sec-vti",
    quantity: "12.00000000",
    quantity_source: "history",
    manual_quantity: "10.00000000",
  }),
  // Nobody typed anything, and history is not what it is: no override to report.
  recorded({ security_id: "sec-mystery", quantity: "5.00000000", manual_quantity: "5.00000000" }),
  // History and the typed number agree, so there is nothing to reconcile.
  recorded({
    security_id: "sec-euro",
    quantity: "10.00000000",
    quantity_source: "history",
    manual_quantity: "10.00000000",
  }),
  // No row for the cash position at all: a valued row with nothing behind it.
];

beforeEach(() => {
  h.portfolio.mockReset();
  h.holdings.mockReset();
  h.portfolio.mockImplementation(() => query(PORTFOLIO));
  h.holdings.mockImplementation(() => query(HOLDINGS));
});

describe("<PortfolioHoldings />", () => {
  it("values each position as of the response's own date", () => {
    render(<PortfolioHoldings />);

    expect(screen.getByTestId("portfolio-as-of")).toHaveTextContent(/as of/);
    expect(screen.getByTestId("portfolio-total")).toHaveTextContent(formatMoney("4200.00", "USD"));
    expect(screen.getByTestId("account-value-acct-broker")).toHaveTextContent(
      formatMoney("4200.00", "USD"),
    );
    expect(screen.getByTestId("holding-value-acct-broker:sec-vti")).toHaveTextContent(
      formatMoney("3000.00", "USD"),
    );
    // Quantity, price and the age of the price, in the notation a statement uses.
    const detail = screen.getByTestId("holding-detail-acct-broker:sec-vti");
    expect(detail).toHaveTextContent("10 @");
    expect(detail).toHaveTextContent(formatMoney("300.00000000", "USD"));
    expect(detail).toHaveTextContent("priced");
    expect(detail).toHaveTextContent("(2 days old)");
    expect(screen.getByTestId("holding-acct-broker:sec-vti")).toHaveAttribute("data-reason", "valued");
  });

  it("refuses to value an unpriced position, and says why in words", () => {
    render(<PortfolioHoldings />);

    const row = screen.getByTestId("holding-acct-broker:sec-mystery");
    // The machine-readable half: a test (and a reader of the DOM) can tell "no
    // price" from "$0.00" without parsing the copy.
    expect(row).toHaveAttribute("data-reason", "no_price");
    expect(screen.getByTestId("holding-value-acct-broker:sec-mystery")).toHaveTextContent("No price");
    // The one rendering this view exists to prevent.
    expect(screen.getByTestId("holding-value-acct-broker:sec-mystery")).not.toHaveTextContent("0.00");

    const reason = screen.getByTestId("holding-reason-acct-broker:sec-mystery");
    expect(reason).toHaveTextContent("No price on or before");
    expect(reason.querySelector("time")).toHaveAttribute("datetime", "2026-09-20");
    expect(reason).toHaveTextContent("left out of every total, not counted as zero");
  });

  it("keeps the part of a no-rate position that is known, and names only what is missing", () => {
    render(<PortfolioHoldings />);

    expect(screen.getByTestId("holding-acct-broker:sec-euro")).toHaveAttribute("data-reason", "no_rate");
    expect(screen.getByTestId("holding-value-acct-broker:sec-euro")).toHaveTextContent("No rate");
    expect(screen.getByTestId("holding-value-acct-broker:sec-euro")).not.toHaveTextContent("0.00");

    const reason = screen.getByTestId("holding-reason-acct-broker:sec-euro");
    // The position *is* priced — in EUR. Only the conversion is absent, so the
    // known half is shown and the missing half is the one named.
    expect(reason).toHaveTextContent(formatMoney("1000.00", "EUR"));
    expect(reason).toHaveTextContent("cannot be converted to USD");
    expect(reason).toHaveTextContent("not counted as zero");
  });

  it("says beside every total which positions the total is missing", () => {
    render(<PortfolioHoldings />);

    const excluded = screen.getByTestId("portfolio-excluded");
    expect(excluded).toHaveTextContent("1 position with no price");
    expect(excluded).toHaveTextContent("1 position with no rate");
    expect(excluded).toHaveTextContent("not counted as zero");
    // And the account's own header repeats it: a total quietly short is the
    // failure ADR-0032 §5 exists to prevent, and it is only visible where the
    // total is.
    expect(screen.getByTestId("account-excluded-acct-broker")).toHaveTextContent(
      "Excludes 1 position with no price and 1 position with no rate",
    );
    // Nothing anywhere in the list became a zero.
    expect(screen.getByTestId("portfolio")).not.toHaveTextContent("$0.00");
  });

  it("renders cash as the money it is (ADR-0033 §4)", () => {
    render(<PortfolioHoldings />);

    const row = screen.getByTestId("holding-acct-broker:sec-cash");
    expect(row).toHaveTextContent("Cash");
    expect(screen.getByTestId("holding-value-acct-broker:sec-cash")).toHaveTextContent(
      formatMoney("1200.00", "USD"),
    );
    // A cash position's quantity *is* its amount — read as "1,200 at $1.00" it
    // would be worse, and read through the account's currency it would mislabel.
    expect(screen.getByTestId("holding-detail-acct-broker:sec-cash")).toHaveTextContent(
      formatMoney("1200.00", "USD"),
    );
  });

  it("shows what a human entered when recorded trades override it (ADR-0034)", () => {
    render(<PortfolioHoldings />);

    const override = screen.getByTestId("holding-manual-acct-broker:sec-vti");
    expect(override).toHaveTextContent("You entered 10");
    expect(override).toHaveTextContent("recorded trades give 12");
    expect(override).toHaveTextContent("history is what counts here");
  });

  it("stays quiet when there is no disagreement to report", () => {
    render(<PortfolioHoldings />);

    // The typed number and history agree — nothing to reconcile.
    expect(screen.queryByTestId("holding-manual-acct-broker:sec-euro")).not.toBeInTheDocument();
    // `quantity_source: "manual"`: the stored scalar is all there is, so nothing
    // is overriding anything.
    expect(screen.queryByTestId("holding-manual-acct-broker:sec-mystery")).not.toBeInTheDocument();
    // No row behind the position at all, and no crash for the missing join.
    expect(screen.queryByTestId("holding-manual-acct-broker:sec-cash")).not.toBeInTheDocument();
    expect(screen.getByTestId("holding-acct-broker:sec-cash")).toBeInTheDocument();
  });

  it("states the age of the oldest price an account's value used", () => {
    render(<PortfolioHoldings />);

    const stale = screen.getByTestId("account-stale-acct-broker");
    expect(stale).toHaveTextContent("Oldest price used: 2 days old");
    expect(stale.querySelector("time")).toHaveAttribute("datetime", "2026-09-18");
  });

  it("warns when the oldest price is old enough to stop reading as current", () => {
    h.portfolio.mockImplementation(() =>
      query({
        ...PORTFOLIO,
        accounts: [{ ...BROKER, max_stale_days: 50, oldest_price_date: "2026-08-01" }],
      }),
    );
    render(<PortfolioHoldings />);

    const stale = screen.getByTestId("account-stale-acct-broker");
    expect(stale).toHaveTextContent("50 days old");
    // Colour is never the only signal (§7.8): the words say it too.
    expect(stale.className).toContain("text-warning");
  });

  it("reports the stated balance's remainder as the real money it is (ADR-0021)", () => {
    h.portfolio.mockImplementation(() =>
      query({
        ...PORTFOLIO,
        // The shape the server actually sends for a `stated` account: the
        // holdings still sum to 4200, and the provider says the account holds
        // 5000 — so `market_value_base` stays Σ(holdings) and the extra 800 is
        // the plug. Writing 5000 into `market_value_base` would be a payload the
        // backend cannot produce, and it would hide the very distinction the
        // headline depends on.
        accounts: [
          {
            ...BROKER,
            balance_source: "stated",
            balance_account: "5000.00",
            stated_balance_base: "5000.00",
            unaccounted_cash_base: "800.00",
          },
        ],
      }),
    );
    render(<PortfolioHoldings />);

    expect(screen.getByTestId("account-valuation-acct-broker")).toHaveTextContent(
      "balance from the account",
    );
    // The headline is the account's balance, not Σ(holdings): it is the number
    // the portfolio total counts this account at, and a header that disagreed
    // with its own line in the total would read as a bug in the total.
    expect(screen.getByTestId("account-value-acct-broker")).toHaveTextContent(
      formatMoney("5000.00", "USD"),
    );
    expect(screen.getByTestId("account-unaccounted-acct-broker")).toHaveTextContent(
      formatMoney("800.00", "USD"),
    );
  });

  it("says a stated balance is missing from the total when there is no rate (ADR-0021)", () => {
    h.portfolio.mockImplementation(() =>
      query({
        ...PORTFOLIO,
        // `stated_balance_base` null is the *only* signal here: the plug is "0"
        // and the account adds nothing to the total in both this case and a
        // genuinely-zero one, so without the null the view would print the
        // balance as `$0.00` and say nothing about why.
        accounts: [
          {
            ...BROKER,
            balance_source: "stated",
            balance_account: "5000.00",
            stated_balance_base: null,
            unaccounted_cash_base: "0.0000",
          },
        ],
      }),
    );
    render(<PortfolioHoldings />);

    const header = screen.getByTestId("account-value-acct-broker");
    expect(header).toHaveTextContent("No USD rate");
    // The forbidden rendering: a missing rate shown as a number.
    expect(header).not.toHaveTextContent("0.00");
    expect(screen.getByTestId("account-no-rate-acct-broker")).toHaveTextContent(
      `${formatMoney("5000.00", "USD")} USD`,
    );
    // And the sentence says which way it is absent — not that the account is
    // worth nothing.
    expect(screen.getByTestId("account-no-rate-acct-broker")).toHaveTextContent(
      "not at zero",
    );
  });

  it("does not invent an unaccounted-cash line for a zero plug", () => {
    render(<PortfolioHoldings />);
    expect(screen.getByTestId("account-valuation-acct-broker")).toHaveTextContent(
      "balance from its holdings",
    );
    expect(screen.queryByTestId("account-unaccounted-acct-broker")).not.toBeInTheDocument();
  });

  it("names the currency a cross-currency account was converted from (§6.4)", () => {
    h.portfolio.mockImplementation(() =>
      query({
        ...PORTFOLIO,
        // For a `derived` account `balance_account` *is* Σ(holdings) in the
        // account's currency — the two fields agree, and the header reads the one
        // that means "the account's own balance".
        accounts: [
          {
            ...BROKER,
            currency: "EUR",
            market_value_account: "3900.00",
            balance_account: "3900.00",
          },
        ],
      }),
    );
    render(<PortfolioHoldings />);
    // A converted figure carries the currency it was converted from, so the
    // base-currency total above it is not read as the account's own money.
    expect(screen.getByTestId("account-native-acct-broker")).toHaveTextContent(
      `${formatMoney("3900.00", "EUR")} EUR`,
    );
  });

  it("shows an account with nothing in it as empty, not as broken", () => {
    h.portfolio.mockImplementation(() =>
      query({ ...PORTFOLIO, accounts: [{ ...BROKER, holdings: [], unpriced: 0, no_rate: 0 }] }),
    );
    render(<PortfolioHoldings />);
    expect(screen.getByTestId("account-no-positions-acct-broker")).toHaveTextContent(
      "No positions in this account.",
    );
  });

  it("shows the empty state only when there is no investment account at all", () => {
    h.portfolio.mockImplementation(() =>
      query({ ...PORTFOLIO, accounts: [], total_base: "0" }),
    );
    render(<PortfolioHoldings />);
    expect(screen.getByTestId("portfolio-empty")).toHaveTextContent("No investment accounts yet.");
    // Nothing is excluded from a total of nothing, so the live region stays quiet.
    expect(screen.queryByTestId("portfolio-excluded")).not.toBeInTheDocument();
    // And no total card at all: `$0.00` above "No investment accounts yet." is a
    // figure about a portfolio that does not exist, and the allocation card beside
    // this one shows its empty state alone. Read as "your investments are worth
    // nothing", it is a different and alarming claim.
    expect(screen.queryByTestId("portfolio-total")).not.toBeInTheDocument();
  });

  it("says so when the recorded positions could not be loaded, and offers the retry", async () => {
    // The ADR-0034 line comes from a second call. A failure there does not
    // invalidate the values — but it does mean every row is now missing an
    // override it may have had, and nothing appearing is indistinguishable from
    // nothing to appear: "no override to show" and "the request never arrived"
    // render identically unless one of them says which it is.
    const refetch = vi.fn();
    h.holdings.mockImplementation(() =>
      query(undefined, { isError: true, error: new Error("inventory unavailable"), refetch }),
    );
    render(<PortfolioHoldings />);

    expect(screen.getByTestId("holdings-error")).toHaveTextContent(
      "recorded positions",
    );
    expect(screen.getByTestId("holdings-error")).toHaveTextContent("overridden by trades");
    // The portfolio itself is still shown: this is a degraded list, not a failure.
    expect(screen.getByTestId("holding-value-acct-broker:sec-vti")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-error")).not.toBeInTheDocument();

    // §4.11 gives every failed read an escape, supplementary ones included — and
    // deliberately not the `QueryError` card, which replaces the section and would
    // claim the portfolio itself failed.
    await userEvent.setup().click(screen.getByTestId("holdings-error-retry"));
    expect(refetch).toHaveBeenCalled();
  });

  it("shows a failed load as a failure, with the API's own message and a retry", async () => {
    const refetch = vi.fn();
    h.portfolio.mockImplementation(() =>
      query(undefined, { isError: true, error: new Error("valuation refused: no such account"), refetch }),
    );
    render(<PortfolioHoldings />);

    // The apostrophe is U+2019, as the component writes it (`&rsquo;`).
    expect(screen.getByTestId("portfolio-error")).toHaveTextContent("Couldn’t load your holdings");
    expect(screen.getByTestId("portfolio-error")).toHaveTextContent("no such account");
    await userEvent.setup().click(screen.getByTestId("portfolio-error-retry"));
    expect(refetch).toHaveBeenCalled();
  });

  it("renders the valued rows without the ADR-0034 line while the second read is in flight", () => {
    // The join is an enhancement, not a precondition: a slow `/holdings` must not
    // hold the portfolio off the screen.
    h.holdings.mockImplementation(() => query(undefined, { isPending: true, data: undefined }));
    render(<PortfolioHoldings />);

    expect(screen.getByTestId("holding-value-acct-broker:sec-vti")).toBeInTheDocument();
    expect(screen.queryByTestId("holding-manual-acct-broker:sec-vti")).not.toBeInTheDocument();
  });

  it("reserves the rows' space while loading rather than collapsing the card", () => {
    h.portfolio.mockImplementation(() => query(undefined, { isPending: true, data: undefined }));
    render(<PortfolioHoldings />);
    expect(screen.getByTestId("portfolio-loading")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your holdings");
  });
});
