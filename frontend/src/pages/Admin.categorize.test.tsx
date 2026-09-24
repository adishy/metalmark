import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AutoCategorize } from "@/pages/Admin";

const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  data: undefined as unknown,
}));

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  return {
    ...actual,
    useAutoCategorizeAll: () => ({
      mutate: h.mutate,
      isPending: false,
      isError: false,
      error: null,
      data: h.data,
    }),
  };
});

beforeEach(() => {
  h.mutate.mockReset();
  h.data = undefined;
});

describe("AutoCategorize", () => {
  it("warns that it overwrites before it runs", () => {
    render(<AutoCategorize />);
    fireEvent.click(screen.getByTestId("auto-categorize"));
    expect(h.mutate).not.toHaveBeenCalled();
    expect(screen.getByTestId("auto-categorize-confirm")).toHaveTextContent(
      "including ones you chose yourself",
    );
    fireEvent.click(screen.getByTestId("auto-categorize-run"));
    expect(h.mutate).toHaveBeenCalledTimes(1);
  });

  it("says what it did", () => {
    h.data = { examined: 173, categorized: 150, changed: 148, left_blank: 23, transfers_linked: 2 };
    render(<AutoCategorize />);
    expect(screen.getByTestId("auto-categorize-result")).toHaveTextContent(
      "Looked at 173 transactions: changed 148, 23 still uncategorized, and linked 2 transfers.",
    );
  });
});
