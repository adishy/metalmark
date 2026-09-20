// Export and import: the household as a document, and one account as a CSV
// (ADR-0036).
//
// Kept out of api/hooks.ts for the same reason api/import.ts is: it is one
// feature's surface, and api/hooks.ts is shared and pinned by other workstreams.
// It borrows that module's conventions — `upload` from api/client.ts, and the
// shared invalidation list after a write, because an import can add a whole
// ledger at once and every chart on every page is now wrong.
//
// The downloads are not `api.get` calls. They return *files*: one is a JSON
// document of several megabytes, the other a CSV. `api.get` parses its response
// as JSON and hands back the parsed value, which for a document this size means
// the whole export becomes a string in the tab's memory with the browser then
// being told to save it — and for a CSV it would simply throw. So the two
// downloads fetch, take the body as a Blob, and give it to the browser to save,
// which is the only one of those steps that has to happen at all.

import { useMutation } from "@tanstack/react-query";
import { ApiError, upload } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import { useAuth } from "@/auth/AuthContext";
import type { UUID } from "@/api/types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

/** What an import did, per entity (``ImportOut``).
 *
 * Keyed by entity name rather than fixed fields, so an entity the server learns
 * to import later is one more entry this UI already renders. An entity with
 * nothing to report is *absent*, not zero — the server omits the key — so a
 * reader lists what is there and never has to tell "created none" apart from
 * "did not look".
 */
export interface ImportResult {
  created: Record<string, number>;
  matched: Record<string, number>;
  /** References that could not be resolved — a rule pointing at a category the
   * document's household deleted. Not an error: the rest of the file landed. */
  warnings: string[];
}

/** The server's own filename for the download, if it sent one.
 *
 * The routes name their files with a date (`metalmark-export-2026-09-20.json`),
 * and the browser should save it under that name rather than one this module
 * invented — the date is the part that says which export this is.
 */
function filenameOf(disposition: string | null): string | null {
  if (!disposition) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return match ? decodeURIComponent(match[1]) : null;
}

/** Fetch a route that returns a file, and hand it to the browser to save.
 *
 * The failure path matters more than the success path: a download that fails
 * quietly saves a 40-byte JSON error as `metalmark-export-2026-09-20.json`, and
 * the user finds out when they try to import it — a month later, from the one
 * file they thought was their backup. So a non-2xx response is parsed for the
 * API's own message and thrown, exactly like every other call in the app.
 */
async function download(path: string, fallbackName: string): Promise<void> {
  // No CSRF header: this is a GET, and the cookie alone authenticates it.
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    let detail = res.statusText;
    try {
      const data = JSON.parse(text);
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      // Not JSON — a proxy error page, most likely. The status text stands.
    }
    throw new ApiError(res.status, detail);
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filenameOf(res.headers.get("Content-Disposition")) ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on a timer rather than immediately: the click starts a download the
  // browser has not read the blob for yet, and revoking the URL out from under
  // it is how a download becomes a zero-byte file in one browser and not in
  // another. A minute is longer than any of these takes to start.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** The whole household as one `metalmark.export` document. */
export function downloadExport(): Promise<void> {
  return download("/export", "metalmark-export.json");
}

/** One account's transactions as a CSV the CSV importer can read back.
 *
 * One account, because the importer is: a file holding several accounts' rows
 * would re-import them all into whichever account the user picked at the other
 * end. The route enforces it, this follows.
 */
export function downloadAccountCsv(accountId: UUID): Promise<void> {
  return download(
    `/export/transactions.csv?account_id=${encodeURIComponent(accountId)}`,
    "metalmark-transactions.csv",
  );
}

/** Load an exported document back into this household.
 *
 * Merges: nothing is deleted, and a document imported twice creates nothing the
 * second time. Owner-only server-side, so the caller mounts this for owners.
 */
export function useImportDocument() {
  const invalidate = useInvalidateLedger();
  const { me } = useAuth();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.set("file", file);
      return upload<ImportResult>("/import", form, me?.csrf_token ?? null);
    },
    onSuccess: invalidate,
  });
}
