import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, upload } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/form";
import { Instant } from "@/components/datetime";

interface Document {
  id: string;
  account_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  created_at: string;
}
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export function AccountDocuments({ accountId }: { accountId: string }) {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
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
        if (file.size === 0 || file.size > 20 * 1024 * 1024) {
          setLocalError("Choose a nonempty file up to 20 MB.");
          return;
        }
        add.mutate(file);
      }} />
      <p className="text-sm text-fg-muted">PDFs, documents, presentations and other files. Up to 20 MB each, 32 MB across all accounts.</p>
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
          <p className="text-xs text-fg-muted">{Math.ceil(file.size_bytes / 1024).toLocaleString()} KB · <Instant value={file.created_at} /></p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {file.media_type === "application/pdf" && <a className="inline-flex min-h-11 items-center rounded-control px-3 text-sm text-accent hover:bg-surface-inset" href={`${BASE}${path}/${file.id}/content?preview=true`} target="_blank" rel="noopener noreferrer">View PDF</a>}
            <a className="inline-flex min-h-11 items-center rounded-control px-3 text-sm text-accent hover:bg-surface-inset" href={`${BASE}${path}/${file.id}/content`}>Download</a>
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
    </section>
  );
}
