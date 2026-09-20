import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, ApiError, setCsrfToken, upload } from "@/api/client";

// What a *failed* request looks like on the wire, which is the half of this
// module that nothing else exercises. Every test below is about a body that is
// not the JSON the happy path assumes.

const fetchMock = vi.fn();

function response(init: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  body?: string;
}) {
  const body = init.body ?? "";
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? "",
    text: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("a failure that is not JSON", () => {
  // The bug this file exists for. FastAPI's 500 is the *plain text* `Internal
  // Server Error`; parsing it unconditionally threw a SyntaxError out of the
  // response handler, before the `!res.ok` branch could run — so the reader saw
  // `Unexpected token 'l', "Internal S"... is not valid JSON` and the status
  // 500, which was the one thing the server had actually told us, never reached
  // them.
  it("reports the status instead of a JSON syntax error", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 500, statusText: "Internal Server Error", body: "Internal Server Error" }),
    );

    const err = await api.get("/investments/allocation").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("Internal Server Error");
  });

  it("survives an HTML error page from something in front of the app", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 502, body: "<html><body>Bad Gateway</body></html>" }),
    );

    const err = await api.get("/accounts").catch((e: unknown) => e);

    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toContain("Bad Gateway");
  });

  it("says something even with no body and no status text", async () => {
    // HTTP/2 drops the reason phrase, so `statusText` is usually the empty
    // string in production — a `|| statusText` fallback would render blank.
    fetchMock.mockResolvedValue(response({ ok: false, status: 500 }));

    const err = await api.get("/accounts").catch((e: unknown) => e);

    expect((err as ApiError).message).toBe("HTTP 500");
  });

  it("still prefers the server's detail when there is one", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 409, body: '{"detail":"That connection is paused."}' }),
    );

    const err = await api.get("/connections").catch((e: unknown) => e);

    expect((err as ApiError).message).toBe("That connection is paused.");
  });

  it("passes over a JSON error object with no detail rather than printing undefined", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 422, body: '{"errors":["amount is required"]}' }),
    );

    const err = await api.get("/transactions").catch((e: unknown) => e);

    expect((err as ApiError).message).toBe('{"errors":["amount is required"]}');
  });
});

describe("a success that is empty", () => {
  it("returns undefined without parsing an empty body", async () => {
    fetchMock.mockResolvedValue(response({ status: 204 }));

    await expect(api.del("/transactions/abc")).resolves.toBeUndefined();
  });
});

describe("upload", () => {
  it("reports a non-JSON failure the same way", async () => {
    fetchMock.mockResolvedValue(
      response({ ok: false, status: 500, statusText: "Internal Server Error", body: "Internal Server Error" }),
    );

    const err = await upload("/import", new FormData(), "token").catch((e: unknown) => e);

    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("Internal Server Error");
  });

  it("sends the CSRF token and lets the browser set the content type", async () => {
    fetchMock.mockResolvedValue(response({ body: "{}" }));

    await upload("/import", new FormData(), "token");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("token");
    // No Content-Type of our own: setting one loses the multipart boundary.
    expect(headers["Content-Type"]).toBeUndefined();
  });
});
