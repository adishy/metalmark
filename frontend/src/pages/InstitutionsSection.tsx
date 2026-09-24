// Settings → Institutions: a logo for each bank the household's accounts name.
//
// Two ways to get one, and the page says which each logo came from: fetched once
// by the server from the institution's own website (only for institutions it
// knows the site of), or uploaded by a person. Either way the image is stored
// here and served from here; the browser never asks the web for it (§4.15).
import { useRef, useState } from "react";
import {
  logoUrl,
  useDeleteLogo,
  useFetchLogos,
  useInstitutions,
  useUploadLogo,
  type Institution,
} from "@/api/institutions";
import { useAuth } from "@/auth/AuthContext";
import AccountMark from "@/components/AccountMark";
import { Button, Spinner } from "@/components/form";

const MAX_BYTES = 512 * 1024;

export default function InstitutionsSection() {
  const institutions = useInstitutions();
  const fetchLogos = useFetchLogos();
  const { me } = useAuth();
  const canFetch = !!me && (me.user.is_admin || me.role === "owner");
  const rows = institutions.data ?? [];
  const missingKnown = rows.filter((r) => r.fetchable && !r.has_logo).length;
  const r = fetchLogos.data;

  return (
    <section className="space-y-4 rounded-card bg-surface-raised p-4" data-testid="institutions">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-fg">Institution logos</h2>
        <p className="text-sm text-fg-muted">
          Logos appear beside your accounts and transactions. They are stored on this server; your
          browser never loads them from anywhere else.
        </p>
      </div>

      {canFetch && missingKnown > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            onClick={() => fetchLogos.mutate()}
            disabled={fetchLogos.isPending}
            aria-busy={fetchLogos.isPending}
            data-testid="institutions-fetch"
          >
            {fetchLogos.isPending && <Spinner />}
            Get logos from bank websites
          </Button>
          <span className="text-sm text-fg-muted">
            Asks each bank&rsquo;s own site for its icon, once. {missingKnown} to get.
          </span>
        </div>
      )}
      {r && (
        <p className="text-sm text-fg" role="status" data-testid="institutions-fetch-result">
          {r.fetched.length > 0 ? `Got ${r.fetched.length} logo${r.fetched.length === 1 ? "" : "s"}.` : "No new logos."}
          {r.failed.length > 0 && ` Couldn’t get ${r.failed.join(", ")} — upload one instead.`}
        </p>
      )}
      {fetchLogos.isError && (
        <p className="text-sm text-negative" role="alert">{(fetchLogos.error as Error).message}</p>
      )}

      {institutions.isPending ? (
        <p className="text-sm text-fg-muted"><Spinner /> Loading institutions</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-fg-muted" data-testid="institutions-empty">
          No institutions yet. Give an account an institution, or connect a bank, and it shows up here.
        </p>
      ) : (
        <ul className="divide-y divide-border" data-testid="institutions-list">
          {rows.map((inst) => (
            <InstitutionRow key={inst.key} inst={inst} />
          ))}
        </ul>
      )}
    </section>
  );
}

function InstitutionRow({ inst }: { inst: Institution }) {
  const upload = useUploadLogo();
  const remove = useDeleteLogo();
  const file = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const onFile = (f: File | undefined) => {
    setErr(null);
    if (!f) return;
    if (f.size > MAX_BYTES) {
      setErr("That image is over 512 KB. Pick a smaller one.");
      return;
    }
    if (f.type === "image/svg+xml") {
      setErr("SVG isn’t accepted. Use a PNG, JPEG, GIF, WebP or ICO.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () =>
      upload.mutate({ key: inst.key, name: inst.name, dataUrl: String(reader.result) });
    reader.readAsDataURL(f);
  };

  return (
    <li className="flex flex-wrap items-center gap-3 py-3" data-testid={`institution-${inst.key}`}>
      {inst.has_logo ? (
        <span className="inline-flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-white">
          <img src={logoUrl(inst)} alt={`${inst.name} logo`} className="size-full object-contain p-0.5" />
        </span>
      ) : (
        <AccountMark name={inst.name} size="lg" />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-medium">{inst.name}</p>
        <p className="text-xs text-fg-muted">
          {inst.logo_source === "uploaded"
            ? "Logo uploaded by you"
            : inst.logo_source === "fetched"
              ? "Logo from the bank’s website"
              : inst.fetchable
                ? "No logo yet — get it from the bank’s website, or upload one"
                : "No logo yet — upload one"}
        </p>
      </div>
      <input
        ref={file}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp,image/x-icon,.ico"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          onFile(e.target.files?.[0]);
          e.target.value = "";
        }}
        data-testid={`institution-file-${inst.key}`}
      />
      <div className="flex gap-2">
        <Button
          variant="secondary"
          onClick={() => file.current?.click()}
          disabled={upload.isPending}
          aria-busy={upload.isPending}
          aria-label={`${inst.has_logo ? "Replace" : "Upload"} the logo for ${inst.name}`}
          data-testid={`institution-upload-${inst.key}`}
        >
          {upload.isPending && <Spinner />}
          {inst.has_logo ? "Replace" : "Upload"}
        </Button>
        {inst.has_logo &&
          (confirming ? (
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)}>Keep</Button>
              <Button
                variant="danger"
                disabled={remove.isPending}
                onClick={() => remove.mutate(inst.key, { onSettled: () => setConfirming(false) })}
                data-testid={`institution-remove-confirm-${inst.key}`}
              >
                Remove logo
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              onClick={() => setConfirming(true)}
              aria-label={`Remove the logo for ${inst.name}`}
              data-testid={`institution-remove-${inst.key}`}
            >
              Remove
            </Button>
          ))}
      </div>
      {(err || upload.isError) && (
        <p className="w-full text-sm text-negative" role="alert">
          {err ?? (upload.error as Error).message}
        </p>
      )}
    </li>
  );
}
