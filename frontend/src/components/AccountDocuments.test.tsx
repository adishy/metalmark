import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AccountDocuments, fileSize } from "@/components/AccountDocuments";

const h = vi.hoisted(() => ({ get: vi.fn(), del: vi.fn(), upload: vi.fn() }));

vi.mock("@/api/client", () => ({ api: { get: h.get, del: h.del }, upload: h.upload }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ me: { csrf_token: "t" } }) }));

vi.mock("@/components/PdfPages", () => ({
  default: ({ name }: { name: string }) => <div data-testid="pdf-pages">{name}</div>,
}));

const FILES = [
  { id: "d1", account_id: "a1", filename: "statement.pdf", media_type: "application/pdf", size_bytes: 3 * 1024 * 1024, created_at: "2026-10-01T12:00:00Z", preview: "pdf" },
  { id: "d2", account_id: "a1", filename: "rows.csv", media_type: "text/csv", size_bytes: 900, created_at: "2026-10-01T12:00:00Z", preview: "text" },
  { id: "d3", account_id: "a1", filename: "deck.pptx", media_type: "application/octet-stream", size_bytes: 20_000, created_at: "2026-10-01T12:00:00Z", preview: null },
];

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccountDocuments accountId="a1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.get.mockReset().mockResolvedValue(FILES);
  h.upload.mockReset().mockResolvedValue(FILES[0]);
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => vi.unstubAllGlobals());

describe("AccountDocuments", () => {
  it("offers View only for files the app can show, and Download for all", async () => {
    mount();
    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(screen.getAllByRole("button", { name: "View" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "Download" })).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("3 MB");
    expect(rows[1]).toHaveTextContent("900 B");
  });

  it("opens a PDF in the app, from a copy it fetched", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["%PDF-1.7"], { type: "application/pdf" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    mount();
    await userEvent.click((await screen.findAllByRole("button", { name: "View" }))[0]);

    expect(await screen.findByTestId("pdf-pages")).toHaveTextContent("statement.pdf");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/accounts/a1/documents/d1/content?preview=true",
      { credentials: "include" },
    );
    expect(screen.getByRole("link", { name: "Open in new tab" })).toHaveAttribute(
      "href",
      "/api/accounts/a1/documents/d1/content?preview=true",
    );
  });

  it("shows text as text, and says so when the file cannot be opened", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      blob: async () => ({ text: async () => "date,amount\n<b>1</b>" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    mount();
    await userEvent.click((await screen.findAllByRole("button", { name: "View" }))[1]);
    // Markup in a file is its source, never elements.
    expect(await screen.findByText(/<b>1<\/b>/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));

    fetchMock.mockResolvedValueOnce({ ok: false, statusText: "Not Found" });
    await userEvent.click(screen.getAllByRole("button", { name: "View" })[1]);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not open this file"));
  });

  it("refuses an empty or oversized file before sending it", async () => {
    mount();
    const input = await screen.findByLabelText("Choose account document");
    const big = new File(["x"], "big.bin");
    Object.defineProperty(big, "size", { value: 100 * 1024 * 1024 + 1 });
    await userEvent.upload(input, big);
    expect(screen.getByRole("alert")).toHaveTextContent("100 MB or smaller");
    expect(h.upload).not.toHaveBeenCalled();

    const ok = new File(["x"], "ok.bin");
    Object.defineProperty(ok, "size", { value: 60 * 1024 * 1024 });
    await userEvent.upload(input, ok);
    expect(h.upload).toHaveBeenCalledTimes(1);
  });
});

describe("fileSize", () => {
  it("reads in the unit a person would use", () => {
    expect(fileSize(12)).toBe("12 B");
    expect(fileSize(2048)).toBe("2 KB");
    expect(fileSize(5.25 * 1024 * 1024)).toBe("5.3 MB");
  });
});
