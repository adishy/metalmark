import { describe, it, expect } from "vitest";
import { accountsUrl, reportUrl, txnQuery } from "@/api/hooks";

describe("txnQuery filter serialization", () => {
  it("returns empty string for no filters", () => {
    expect(txnQuery({})).toBe("");
  });

  it("repeats account_id and category_id params", () => {
    const q = txnQuery({ account_id: ["a1", "a2"], category_id: ["c1"] });
    expect(q).toContain("account_id=a1");
    expect(q).toContain("account_id=a2");
    expect(q).toContain("category_id=c1");
  });

  it("includes date range, search and cursor", () => {
    const q = txnQuery(
      { start: "2026-01-01T00:00:00Z", end: "2026-12-31T23:59:59Z", search: "coffee" },
      "cursor123",
    );
    expect(q).toContain("start=2026-01-01");
    expect(q).toContain("end=2026-12-31");
    expect(q).toContain("search=coffee");
    expect(q).toContain("cursor=cursor123");
  });

  it("passes review_status and include_hidden", () => {
    const q = txnQuery({ review_status: "needs_review", include_hidden: true });
    expect(q).toContain("review_status=needs_review");
    expect(q).toContain("include_hidden=true");
  });

  it("scopes to an owner only when one is selected", () => {
    expect(txnQuery({ owner_id: "o-1" })).toBe("?owner_id=o-1");
    expect(txnQuery({})).not.toContain("owner_id");
  });
});

describe("owner-scoped URLs", () => {
  it("appends owner_id to the owner-filterable GETs", () => {
    expect(accountsUrl("/accounts", "o-1")).toBe("/accounts?owner_id=o-1");
    expect(accountsUrl("/accounts/net-worth", "o-1")).toBe("/accounts/net-worth?owner_id=o-1");
  });

  it("leaves the path bare when no owner is selected", () => {
    expect(accountsUrl("/accounts", null)).toBe("/accounts");
    expect(accountsUrl("/accounts")).toBe("/accounts");
  });

  it("keeps start/end first and adds owner_id to reports", () => {
    const q = reportUrl("/reports/cash-flow", "2026-01-01", "2026-12-31", "o-1");
    expect(q).toBe("/reports/cash-flow?start=2026-01-01&end=2026-12-31&owner_id=o-1");
    expect(reportUrl("/reports/spending", "2026-01-01", "2026-12-31")).toBe(
      "/reports/spending?start=2026-01-01&end=2026-12-31",
    );
  });
});
