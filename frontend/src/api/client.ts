// Thin fetch wrapper: cookie auth (credentials: include) + CSRF header on
// mutations. Data is never cached by the service worker (see vite PWA config).

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** A response body as JSON, or `undefined` when it is not JSON at all.
 *
 * A *failed* request does not necessarily answer with JSON. A 500 from the app
 * is the plain text `Internal Server Error`, and anything in front of it answers
 * with HTML. Parsing that unconditionally throws before the caller can look at
 * `res.ok`, so the reader is handed `Unexpected token 'l', "Internal S"... is
 * not valid JSON` — a syntax error about a 500, saying nothing about the 500.
 * The status is the one fact always available, so it must not be the one lost.
 */
function parseJson(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** What to tell the reader about a failed response.
 *
 * The server's `detail` when it sent one, then the body as it stands, then the
 * status. That last fallback is not a formality: `statusText` is empty under
 * HTTP/2, so a bare `|| res.statusText` can leave the message blank.
 */
function detailOf(res: { status: number; statusText: string }, data: unknown, text: string) {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  }
  // Bounded: a proxy's error page is not a message anyone reads to the end, and
  // it should not become one because it happens to be the body.
  return text.trim().slice(0, 200) || res.statusText || `HTTP ${res.status}`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = parseJson(text);
  if (!res.ok) throw new ApiError(res.status, detailOf(res, data, text));
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/**
 * POST a multipart body. Every file upload in the app goes through here.
 *
 * `request` above cannot express this — it JSON-encodes whatever it is given —
 * so the two upload paths (the CSV/OFX importer and the document import) would
 * otherwise each carry their own copy of the same twelve lines, and the copy is
 * where the CSRF header quietly stops being sent.
 *
 * The CSRF token is a parameter rather than the module-private `csrfToken`
 * because callers read it from the auth context, which is the copy this module
 * is allowed to see; `setCsrfToken` is for the session bootstrap.
 */
export async function upload<T>(
  path: string,
  form: FormData,
  csrf: string | null,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrf) headers["X-CSRF-Token"] = csrf;
  // No Content-Type of our own: the browser sets it, boundary and all.
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    credentials: "include",
    body: form,
  });

  const text = await res.text();
  const data = parseJson(text);
  if (!res.ok) throw new ApiError(res.status, detailOf(res, data, text));
  return data as T;
}
