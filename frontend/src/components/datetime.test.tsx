// The pair of components is the enforcement of one rule — the ISO form is never
// the visible text — so these tests are about that rule and about the two frames
// staying in their own lanes. The vocabulary itself is tested in `lib/dates.test.ts`.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Day, Instant } from "@/components/datetime";
import { isoDay } from "@/lib/dates";

/** `YYYY-MM-DD` anywhere in what a person can read. */
const ISO_IN_TEXT = /\d{4}-\d{2}-\d{2}/;

describe("Day", () => {
  it("shows the vocabulary, not the ISO value", () => {
    render(<Day value="2026-09-20T12:00:00Z" />);
    expect(screen.getByText("Sep 20")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(ISO_IN_TEXT);
  });

  it("carries the ISO value in title and datetime", () => {
    // The two places it is allowed to be: hover, and the machine-readable value
    // a screen reader or a copy-paste gets.
    render(<Day value="2026-09-20T12:00:00Z" />);
    const el = screen.getByText("Sep 20");
    expect(el).toHaveAttribute("title", "2026-09-20");
    expect(el).toHaveAttribute("datetime", "2026-09-20");
  });

  it("puts the day, not the instant, in datetime", () => {
    // A bank import has no time of day. Claiming midnight would be inventing a
    // precision the record does not have, and `<time datetime>` is what a
    // machine reads.
    render(<Day value="2026-09-20T12:00:00Z" />);
    expect(screen.getByText("Sep 20")).toHaveAttribute("datetime", "2026-09-20");
  });

  it("renders the long style", () => {
    render(<Day value="2026-01-02" style="long" />);
    expect(screen.getByText("Jan 02 2026")).toHaveAttribute("title", "2026-01-02");
  });

  it("says Today for the current day, and still carries the ISO", () => {
    // Built from the local day rather than `toISOString()`: at some hours those
    // differ, and this component reads the day.
    render(<Day value={`${isoDay(new Date())}T12:00:00Z`} style="compact" />);
    const el = screen.getByText("Today");
    expect(el).toHaveAttribute("title", isoDay(new Date()));
  });

  it("passes through the props a call site owns", () => {
    render(<Day value="2026-09-20" className="text-muted" data-testid="d" />);
    expect(screen.getByTestId("d")).toHaveClass("text-muted");
  });

  it("does not let a call site overwrite the value's own attributes", () => {
    // `title` and `dateTime` are omitted from the accepted props on purpose:
    // a second way to set them is a second way to get the rule wrong. This is
    // the compiler-level assertion, so `npm run typecheck` is the enforcement.
    // @ts-expect-error - `title` is owned by the component
    render(<Day value="2026-09-20" title="2026-09-20" />);
    // @ts-expect-error - `dateTime` is owned by the component
    const ignored = <Day value="2026-09-20" dateTime="2026-09-20" />;
    void ignored;
    expect(screen.getByText("Sep 20")).toHaveAttribute("title", "2026-09-20");
  });
});

describe("Instant", () => {
  it("shows the vocabulary and keeps the full ISO in title and datetime", () => {
    const { container } = render(<Instant value="2026-09-20T23:30:00Z" />);
    const el = container.querySelector("time")!;
    expect(el).toHaveAttribute("title", "2026-09-20T23:30:00Z");
    expect(el).toHaveAttribute("datetime", "2026-09-20T23:30:00Z");
    expect(el.textContent).not.toMatch(ISO_IN_TEXT);
  });

  it("keeps the seconds in datetime even at medium precision", () => {
    // Two sync events a second apart are distinguishable here and nowhere else
    // in the UI, which is the reason `datetime` is not the rendered string.
    const { container } = render(<Instant value="2026-09-20T12:00:03Z" style="weekday" />);
    expect(container.querySelector("time")!).toHaveAttribute(
      "datetime",
      "2026-09-20T12:00:03Z",
    );
  });

  it("renders the relative style", () => {
    const iso = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    const { container } = render(<Instant value={iso} style="relative" />);
    expect(screen.getByText("3 h ago")).toBeInTheDocument();
    expect(container.querySelector("time")!).toHaveAttribute("title", iso);
  });
});
