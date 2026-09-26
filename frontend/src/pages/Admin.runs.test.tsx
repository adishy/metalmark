import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Connection, SyncRun } from "@/api/types";
import { RecentRuns } from "@/pages/Admin";

// Issue #36: the run history grew the page as syncs accumulated. What is being
// tested here is the *shape* of the fix — a labelled, focusable, bounded region
// and a line that says how much of the history is in it — because that is the
// part a unit test can see. The cap's geometry (its height, and that it scrolls
// rather than the page) is measured in `e2e/desktop.spec.ts`, where there is a
// layout to measure.

const h = vi.hoisted(() => ({
  runs: [] as unknown[],
  connections: [] as unknown[],
  asked: [] as unknown[][],
}));

vi.mock("@/api/sync", async () => {
  const actual = await vi.importActual<typeof import("@/api/sync")>("@/api/sync");
  return {
    ...actual,
    useConnections: () => ({
      data: h.connections,
      isPending: false,
      isError: false,
      error: null,
    }),
    useSyncRuns: (connectionId: string | null, limit?: number) => {
      h.asked.push([connectionId, limit]);
      return { data: h.runs, isPending: false, isError: false, error: null, isSuccess: true };
    },
    // A row only asks for its log once it is expanded, and nothing here expands
    // one — but the hook runs on every row, and the real one needs a query
    // client this file does not build.
    useSyncRunDetail: () => ({ data: undefined, isPending: false, isError: false, error: null }),
  };
});

const CONN = {
  id: "c1",
  institution: "Fake Bank",
  nickname: "Everyday",
  status: "ok",
} as unknown as Connection;

function run(n: number): SyncRun {
  return {
    id: `r${n}`,
    connection_id: "c1",
    connection_label: "Everyday",
    trigger: "manual",
    status: "ok",
    started_at: "2026-09-20T10:00:00Z",
    finished_at: "2026-09-20T10:00:04Z",
    duration_ms: 4_000,
    http_ms: 900,
    http_status: 200,
    bytes_fetched: 1_024,
    accounts_seen: 2,
    accounts_created: 0,
    accounts_remapped: 0,
    txns_rekeyed: 0,
    txns_inserted: 3,
    txns_updated: 1,
    txns_reconciled: 0,
    pendings_expired: 0,
    transfers_matched: 0,
    rules_applied: 0,
    error: null,
  };
}

beforeEach(() => {
  h.runs = [];
  h.connections = [CONN];
  h.asked = [];
});

describe("RecentRuns", () => {
  it("bounds the list in a region that can be reached and scrolled by keyboard", () => {
    h.runs = [run(1), run(2), run(3)];
    render(<RecentRuns />);

    const region = screen.getByTestId("run-region");
    // §4.7: an `overflow` box is not focusable on its own, and a scroll region a
    // keyboard cannot scroll is a 2.1.1 failure. The label is how a reader who
    // lands on it learns what they are in and how much of it there is.
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveAttribute("aria-label", "Run history, 3 runs, newest first");
    // The cap itself. `max-h-96` on the box, not a limit on the query — every
    // row the server sent is rendered and the box is what keeps its height.
    expect(region.className).toContain("max-h-96");
    expect(region.className).toContain("overflow-y-auto");
    expect(screen.getAllByTestId(/^run-row-/)).toHaveLength(3);
  });

  it("says how many runs are in it, and names the number it asked for", () => {
    h.runs = [run(1), run(2), run(3)];
    render(<RecentRuns />);

    expect(screen.getByTestId("run-count")).toHaveTextContent("3 runs, newest first.");
    // The count in the copy is a claim about the *request*, so the request has to
    // be this file's number rather than whatever the endpoint defaults to.
    expect(h.asked).toEqual([[null, 50]]);
  });

  it("says older runs are not listed when the page came back full", () => {
    h.runs = Array.from({ length: 50 }, (_, i) => run(i));
    render(<RecentRuns />);

    expect(screen.getByTestId("run-count")).toHaveTextContent(
      "The 50 most recent runs. Older runs, if any, are not listed.",
    );
    // Still all fifty in the DOM: the cap is on the box, so nothing the server
    // sent is dropped.
    expect(screen.getAllByTestId(/^run-row-/)).toHaveLength(50);
  });

  it("keeps the filter, and asks the server for the filtered history", () => {
    h.runs = [run(1)];
    render(<RecentRuns />);

    fireEvent.change(screen.getByTestId("run-filter"), { target: { value: "c1" } });
    expect(h.asked).toContainEqual(["c1", 50]);
  });

  it("does not put an empty scroll region in the tab order", () => {
    render(<RecentRuns />);

    expect(screen.getByTestId("no-runs")).toHaveTextContent("No runs yet.");
    // A focusable region with nothing in it and nothing to scroll is a tab stop
    // that does nothing, which is worse than no tab stop (§4.7 is about content
    // a keyboard has to be able to reach).
    expect(screen.queryByTestId("run-region")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-count")).not.toBeInTheDocument();
  });
});
