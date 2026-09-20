// The CSV import vertical's own API surface: preview a file, then commit it.
//
// Kept out of api/hooks.ts (shared, and pinned by other workstreams) while
// following its conventions — the commit reuses the shared invalidation list, so
// an import refreshes the ledger and the reports exactly like any other write.
// That matters more here than elsewhere: one commit can add hundreds of rows.
//
// These two calls are multipart, which api/client.ts cannot express (its one
// `request` helper JSON-encodes every body), so they speak fetch directly. The
// CSRF token comes from the auth context rather than the client's module-private
// copy — the same token, read from a place this module is allowed to look.

import { useMutation } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import { useAuth } from "@/auth/AuthContext";
import type { UUID } from "@/api/types";

/** The fields a column can be mapped to — mirrors `MAPPABLE_FIELDS` server-side. */
export type MappableField =
  | "date"
  | "amount"
  | "debit"
  | "credit"
  | "description"
  | "category"
  | "owner"
  | "notes";

/** In the order the mapping picker offers them; "date" and an amount are required. */
export const MAPPABLE_FIELDS: MappableField[] = [
  "date",
  "amount",
  "debit",
  "credit",
  "description",
  "category",
  "owner",
  "notes",
];

export interface CsvPreview {
  headers: string[];
  /** The first data rows, verbatim, so the user can see what they are mapping. */
  sample: string[][];
  /** header → field, or null for "ignore this column". A suggestion, not a decision. */
  suggested: Record<string, MappableField | null>;
}

/** One row the importer refused to guess at. `line` is the file's own line number. */
export interface CsvRowError {
  line: number;
  message: string;
}

/** `suspects` ⊆ `inserted`: rows that landed but look like possible duplicates. */
export interface CsvCommitResult {
  inserted: number;
  skipped: number;
  suspects: number;
  errors: CsvRowError[];
}

export interface CsvCommitInput {
  file: File;
  accountId: UUID;
  mapping: Record<string, MappableField | null>;
  /** Where an unrecognised (or blank) category name lands; "Uncategorized" if unset. */
  defaultCategoryId?: UUID | null;
  /** Read genuinely ambiguous slash dates as dd/mm rather than mm/dd. */
  dayfirst?: boolean;
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

async function upload<T>(path: string, body: FormData, csrf: string | null): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrf) headers["X-CSRF-Token"] = csrf;
  // No Content-Type of our own: the browser sets it, boundary and all.
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    credentials: "include",
    body,
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

/** Headers, a sample of rows, and a suggested mapping. Writes nothing, so it is
 * safe to run on every file the user picks up. */
export function useCsvPreview() {
  const { me } = useAuth();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.set("file", file);
      return upload<CsvPreview>("/import/csv/preview", form, me?.csrf_token ?? null);
    },
  });
}

export function useCsvCommit() {
  const invalidate = useInvalidateLedger();
  const { me } = useAuth();
  return useMutation({
    mutationFn: (input: CsvCommitInput) => {
      const form = new FormData();
      form.set("file", input.file);
      form.set("account_id", input.accountId);
      // The mapping's keys are the file's own header names, which cannot be form
      // field names — so it rides as one JSON string.
      form.set("mapping", JSON.stringify(input.mapping));
      if (input.defaultCategoryId) form.set("default_category_id", input.defaultCategoryId);
      form.set("dayfirst", input.dayfirst ? "true" : "false");
      return upload<CsvCommitResult>("/import/csv/commit", form, me?.csrf_token ?? null);
    },
    onSuccess: invalidate,
  });
}
