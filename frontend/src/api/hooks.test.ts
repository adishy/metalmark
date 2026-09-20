import { describe, it, expect } from "vitest";
import { txnQuery } from "@/api/hooks";

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
});
