import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { Checks } from "@/api/types";
import { DataChecks } from "@/pages/Admin";

const h = vi.hoisted(() => ({ data: undefined as unknown }));

vi.mock("@/api/sync", async () => {
  const actual = await vi.importActual<typeof import("@/api/sync")>("@/api/sync");
  return {
    ...actual,
    useChecks: () => ({ data: h.data, isPending: false, isError: false, error: null }),
  };
});

const CHECKS: Checks = {
  schema_version: "0007",
  checks: [
    {
      id: "headline_matches_chart",
      status: "ok",
      count: 0,
      summary: "The Accounts total equals today's point on the net-worth chart.",
      items: [],
    },
    {
      id: "liabilities_signed",
      status: "warn",
      count: 1,
      summary: "Synced cards or loans that have only ever reported a positive balance.",
      items: [{ account_id: "a1", name: "Amex" }],
    },
    {
      id: "synced_investments_valued",
      status: "fail",
      count: 0,
      summary: "This check could not run (RuntimeError).",
      items: [],
    },
    { id: "something_new", status: "info", count: 2, summary: "Two things.", items: [] },
  ],
};

describe("DataChecks", () => {
  it("shows each check's status, its summary and the accounts behind it", () => {
    h.data = CHECKS;
    render(<DataChecks />);

    const ok = screen.getByTestId("check-headline_matches_chart");
    expect(within(ok).getByText("Accounts total matches the chart")).toBeInTheDocument();
    expect(within(ok).getByText("ok")).toBeInTheDocument();

    const warn = screen.getByTestId("check-liabilities_signed");
    expect(within(warn).getByText("look")).toBeInTheDocument();
    expect(within(warn).getByText("Amex")).toBeInTheDocument();

    const crashed = screen.getByTestId("check-synced_investments_valued");
    expect(within(crashed).getByText("failed")).toBeInTheDocument();
    expect(within(crashed).getByText(/could not run/)).toBeInTheDocument();

    // A check this build has no title for still shows, under its id.
    expect(within(screen.getByTestId("check-something_new")).getByText("something_new"))
      .toBeInTheDocument();
    expect(screen.getByTestId("schema-version")).toHaveTextContent("Schema 0007");
  });

  it("says so when the schema version cannot be read", () => {
    h.data = { ...CHECKS, schema_version: null };
    render(<DataChecks />);
    expect(screen.getByTestId("schema-version")).toHaveTextContent("Schema unknown");
  });
});
