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
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail =
      (data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) ||
      res.statusText;
    throw new ApiError(res.status, detail);
  }
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
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail =
      (data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) ||
      res.statusText;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}
