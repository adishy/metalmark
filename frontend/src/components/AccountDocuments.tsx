import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, upload } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import Dialog from "@/components/Dialog";
import { Button } from "@/components/form";
import { Instant } from "@/components/datetime";

interface Document {
  id: string;
  account_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  created_at: string;
  /** How the app can show it in place; null means it is a download only. */
  preview: "pdf" | "image" | "text" | null;
}
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
/** `documents.MAX_FILE_BYTES`: one file, not a total. */
const MAX_FILE_BYTES = 100 * 1024 * 1024;
/** Text past this is not put in the page; the download has all of it. */
const MAX_TEXT_CHARS = 200_000;

const LINK = "inline-flex min-h-11 items-center rounded-control px-3 text-sm text-accent hover:bg-surface-inset";

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024).toLocaleString()} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`;
}

const PdfPages = lazy(() => import("@/components/PdfPages"));

type Loaded =
  | { kind: "pdf"; blob: Blob }
  | { kind: "image"; url: string }
  | { kind: "text"; text: string; cut: boolean };

/**
 * A file, shown in the app. The bytes are fetched and shown from a local copy
 * rather than by pointing a frame at the API: the deployment forbids framing its
 * own responses, and a phone would not draw a framed PDF anyway.
 */
function DocumentViewer({ file, href, onClose }: { file: Document; href: string; onClose: () => void }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setLoaded(null);
    setFailed(false);
    (async () => {
      const res = await fetch(`${href}?preview=true`, { credentials: "include" });
      if (!res.ok) throw new Error(res.statusText);
      const blob = await res.blob();
      if (cancelled) return;
      if (file.preview === "text") {
        const text = await blob.text();
        if (!cancelled) setLoaded({ kind: "text", text: text.slice(0, MAX_TEXT_CHARS), cut: text.length > MAX_TEXT_CHARS });
        return;
      }
      if (file.preview === "pdf") {
        setLoaded({ kind: "pdf", blob });
        return;
      }
      url = URL.createObjectURL(blob);
      setLoaded({ kind: "image", url });
    })().catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [href, file.preview]);

  // In the body rather than where it is declared: the list it opens from lives
  // inside the account's own dialog, whose panel is transformed and scrolls.
  return createPortal(
    <Dialog
      open
      wide
      onClose={onClose}
      title={file.filename}
      testid="document-viewer"
      footer={<>
        <a className={LINK} href={`${href}?preview=true`} target="_blank" rel="noopener noreferrer">Open in new tab</a>
        <a className={LINK} href={href}>Download</a>
      </>}
    >
      {failed && <p role="alert" className="text-sm text-negative">Could not open this file. Download it instead.</p>}
      {!failed && !loaded && <p role="status" className="text-sm text-fg-muted">Opening…</p>}
      {loaded?.kind === "pdf" && <Suspense fallback={<p role="status" className="text-sm text-fg-muted">Opening…</p>}>
        <PdfPages data={loaded.blob} name={file.filename} />
      </Suspense>}
      {loaded?.kind === "image" && <img
        src={loaded.url}
        alt={file.filename}
        className="mx-auto max-h-[70vh] max-w-full rounded-control"
      />}
      {loaded?.kind === "text" && <>
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-control bg-surface-inset p-3 text-xs" tabIndex={0}>{loaded.text}</pre>
        {loaded.cut && <p className="mt-2 text-xs text-fg-muted">This is the start of the file. Download it to see the rest.</p>}
      </>}
    </Dialog>,
    document.body,
  );
}

export function AccountDocuments({ accountId }: { accountId: string }) {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [viewing, setViewing] = useState<Document | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const path = `/accounts/${accountId}/documents`;
  const key = ["account-documents", accountId];
  const files = useQuery({ queryKey: key, queryFn: () => api.get<Document[]>(path) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const add = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return upload<Document>(path, form, me?.csrf_token ?? null);
    },
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`${path}/${id}`),
    onSuccess: () => { setConfirming(null); void refresh(); },
  });
  const error = localError || (add.error instanceof Error ? add.error.message : null)
    || (remove.error instanceof Error ? remove.error.message : null);

  return (
    <section className="space-y-3" aria-label="Account documents">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-base font-semibold">Documents</h3>
        <Button variant="secondary" disabled={add.isPending} onClick={() => input.current?.click()}>
          {add.isPending ? "Uploading…" : "Upload file"}
        </Button>
      </div>
      <input ref={input} type="file" hidden aria-label="Choose account document" onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        setLocalError(null);
        add.reset();
        if (file.size === 0 || file.size > MAX_FILE_BYTES) {
          setLocalError("Choose a file that is not empty and is 100 MB or smaller.");
          return;
        }
        add.mutate(file);
      }} />
      <p className="text-sm text-fg-muted">Statements, PDFs, images, spreadsheets and other files, up to 100 MB each. PDFs, images and text open here; other files download.</p>
      {error && <p role="alert" className="text-sm text-negative">{error}</p>}
      {files.isPending && <p role="status" className="text-sm text-fg-muted">Loading documents…</p>}
      {files.isError && <div role="alert" className="space-y-2">
        <p className="text-sm text-negative">Could not load documents.</p>
        <Button variant="secondary" onClick={() => void files.refetch()}>Retry</Button>
      </div>}
      {files.data?.length === 0 && <p className="text-sm text-fg-muted">No documents attached.</p>}
      <ul className="space-y-2">
        {files.data?.map((file) => <li key={file.id} className="rounded-control border border-border p-3">
          <p className="break-words text-sm font-medium">{file.filename}</p>
          <p className="text-xs text-fg-muted">{fileSize(file.size_bytes)} · <Instant value={file.created_at} /></p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {file.preview && <Button variant="ghost" onClick={() => setViewing(file)}>View</Button>}
            <a className={LINK} href={`${BASE}${path}/${file.id}/content`}>Download</a>
            <Button variant="ghost" onClick={() => setConfirming(file.id)}>Delete</Button>
          </div>
          {confirming === file.id && <div className="mt-3 space-y-2">
            <p className="text-sm">Delete this document permanently?</p>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setConfirming(null)}>Keep file</Button>
              <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(file.id)}>Delete file</Button>
            </div>
          </div>}
        </li>)}
      </ul>
      {viewing && <DocumentViewer
        file={viewing}
        href={`${BASE}${path}/${viewing.id}/content`}
        onClose={() => setViewing(null)}
      />}
    </section>
  );
}
