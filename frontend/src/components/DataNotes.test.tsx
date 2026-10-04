import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import DataNotes from "@/components/DataNotes";

const summaryOf = (testid: string) => screen.getByTestId(testid).querySelector("summary")!;

describe("<DataNotes />", () => {
  it("renders nothing when there is nothing to say", () => {
    render(<DataNotes notes={[]} testid="notes" />);
    expect(screen.queryByTestId("notes")).toBeNull();
  });

  it("starts collapsed, with a quiet summary that counts the notes", () => {
    render(<DataNotes notes={["No rate for EUR.", "2 positions unpriced."]} testid="notes" />);
    expect(screen.getByTestId("notes")).not.toHaveAttribute("open");
    expect(summaryOf("notes")).toHaveTextContent("Some data is incomplete · 2 notes");
    expect(summaryOf("notes").className).not.toContain("text-warning");
  });

  it("says 'note' for exactly one", () => {
    render(<DataNotes notes={["No rate for EUR."]} testid="notes" />);
    expect(summaryOf("notes")).toHaveTextContent("· 1 note");
    expect(summaryOf("notes")).not.toHaveTextContent("1 notes");
    expect(screen.getByTestId("notes")).not.toHaveTextContent("×");
  });

  it("keeps one line per distinct sentence but counts every note", () => {
    // Two trades each missing a rate on the same day produce the same sentence
    // twice. That is two things missing: the line carries ×2 and the summary
    // counts three notes, not two distinct strings.
    const same = "No rate for EUR on 2026-09-18.";
    render(<DataNotes notes={[same, "2 positions unpriced.", same]} testid="notes" />);
    const items = screen.getAllByRole("listitem", { hidden: true });
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(`${same} ×2`);
    expect(items[1]).toHaveTextContent("2 positions unpriced.");
    expect(items[1]).not.toHaveTextContent("×");
    expect(summaryOf("notes")).toHaveTextContent("· 3 notes");
  });

  it("takes a caller's summary and warning tone", () => {
    render(<DataNotes notes={["a"]} summary="Chart leaves out 1 account" tone="warning" testid="notes" />);
    expect(summaryOf("notes")).toHaveTextContent("Chart leaves out 1 account · 1 note");
    expect(summaryOf("notes").className).toContain("text-warning");
  });
});
