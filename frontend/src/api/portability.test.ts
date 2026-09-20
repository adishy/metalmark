import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError } from "@/api/client";
import { downloadAccountCsv, downloadExport } from "@/api/portability";

// jsdom has no object URLs and no download plumbing, so the last step of the
// helper is stubbed: what is under test is the request it makes, the name it
// saves under, and — the part that matters — what it does when the server
// answers with an error instead of a file.

const fetchMock = vi.fn();
/** Every anchor the module built, so its `download` name can be read back. */
const anchors: HTMLAnchorElement[] = [];

function fileResponse(body = "{}", disposition: string | null = null) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    headers: { get: (k: string) => (k.toLowerCase() === "content-disposition" ? disposition : null) },
    blob: async () => new Blob([body]),
  } as unknown as Response;
}

function errorResponse(status: number, body: string) {
  return {
    ok: false,
    status,
    statusText: "Unauthorized",
    headers: { get: () => null },
    text: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock.mockReset();
  anchors.length = 0;
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:stub"),
    configurable: true,
    writable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true, writable: true });
  // A real click on a blob: href makes jsdom complain that navigation is not
  // implemented; the element is still the thing under test.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const realCreate = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
    const el = realCreate(tag);
    if (tag === "a") anchors.push(el as HTMLAnchorElement);
    return el;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("downloadExport", () => {
  it("fetches the export and saves it under the server's filename", async () => {
    fetchMock.mockResolvedValue(
      fileResponse("{}", 'attachment; filename="metalmark-export-2026-09-20.json"'),
    );

    await downloadExport();

    expect(fetchMock).toHaveBeenCalledWith("/api/export", { credentials: "include" });
    expect(anchors).toHaveLength(1);
    // The date in the name is the server's, and it is the part that says which
    // export this is — a name this module invented would lose it.
    expect(anchors[0].download).toBe("metalmark-export-2026-09-20.json");
    expect(anchors[0].href).toBe("blob:stub");
  });

  it("falls back to its own filename when the server names no file", async () => {
    fetchMock.mockResolvedValue(fileResponse());

    await downloadExport();

    expect(anchors[0].download).toBe("metalmark-export.json");
  });

  // The failure this guards is silent corruption: a 40-byte JSON error saved as
  // `metalmark-export-2026-09-20.json` is a file that looks like a backup and is
  // discovered not to be one when it is imported, or when it is needed.
  it("throws instead of saving an error response as a file", async () => {
    fetchMock.mockResolvedValue(errorResponse(401, '{"detail":"Not authenticated"}'));

    const err = await downloadExport().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("Not authenticated");
    expect(anchors).toHaveLength(0);
  });

  it("survives an error body that is not JSON", async () => {
    fetchMock.mockResolvedValue(errorResponse(502, "<html>bad gateway</html>"));

    const err = await downloadExport().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
  });
});

describe("downloadAccountCsv", () => {
  it("asks for the one account it was given", async () => {
    fetchMock.mockResolvedValue(fileResponse("Date,Amount\n", null));

    await downloadAccountCsv("acct-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/export/transactions.csv?account_id=acct-1",
      { credentials: "include" },
    );
    // The route requires the account, so the fallback name is only ever used
    // when a proxy strips the header — but it must not be the export's name.
    expect(anchors[0].download).toBe("metalmark-transactions.csv");
  });
});
