// CSV import: choose a file → check the mapping against a real sample of it →
// import → read what happened.
//
// Three steps in one dialog because the middle one is the whole point. The
// importer refuses to guess, so the user's confirmation *is* the mapping: it is
// shown pre-filled from the headers, correctable column by column, and sent as
// they left it. Anything the importer would not accept — a file with no date
// column, a cell it cannot read — comes back as a message rather than a silent
// default, and the row errors name the file line so the cell can be found.
import { useState } from "react";
import type { Account, Category } from "@/api/types";
import {
  MAPPABLE_FIELDS,
  useCsvCommit,
  useCsvPreview,
  type CsvCommitResult,
  type CsvPreview,
  type MappableField,
} from "@/api/import";
import Dialog from "@/components/Dialog";
import { Button, Field, Select, useFieldId } from "@/components/form";

interface ImportDialogProps {
  onClose: () => void;
  accounts: Account[];
  categories: Category[];
}

type Step = "pick" | "map" | "done";

/** Mounted only while it is open (like the detail sheet), so the picked file and
 * the previous import's result are gone by the time it is opened again. */
export default function ImportDialog({ onClose, accounts, categories }: ImportDialogProps) {
  const [step, setStep] = useState<Step>("pick");
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, MappableField | null>>({});
  const [defaultCategoryId, setDefaultCategoryId] = useState("");
  const [dayfirst, setDayfirst] = useState(false);
  const [result, setResult] = useState<CsvCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewMut = useCsvPreview();
  const commitMut = useCsvCommit();

  const ids = {
    file: useFieldId("import-file"),
    account: useFieldId("import-account"),
    defaultCategory: useFieldId("import-default-category"),
    dayfirst: useFieldId("import-dayfirst"),
    map: useFieldId("import-map"),
  };

  const reset = () => {
    setStep("pick");
    setFile(null);
    setPreview(null);
    setMapping({});
    setResult(null);
    setError(null);
  };

  // Preview on pick, not on a second click: the mapping UI is built from the
  // file, so there is nothing to decide before seeing it. Preview writes
  // nothing, so the only cost of choosing the wrong file is choosing another.
  const onFile = (picked: File | null) => {
    setFile(picked);
    setResult(null);
    setError(null);
    if (!picked) return;
    previewMut.mutate(picked, {
      onSuccess: (p) => {
        setPreview(p);
        setMapping(p.suggested);
        setStep("map");
      },
      onError: (e) => setError((e as Error).message),
    });
  };

  const fields = Object.values(mapping);
  // The server enforces this too; doing it here is what lets the button say why
  // it is not ready, instead of the answer being a 400 after the click.
  const hasAmount = fields.some((f) => f === "amount" || f === "debit" || f === "credit");
  const ready = !!file && !!accountId && fields.includes("date") && hasAmount;

  const commit = () => {
    if (!file) return;
    setError(null);
    commitMut.mutate(
      { file, accountId, mapping, defaultCategoryId: defaultCategoryId || null, dayfirst },
      {
        onSuccess: (r) => {
          setResult(r);
          setStep("done");
        },
        onError: (e) => setError((e as Error).message),
      },
    );
  };

  const footer =
    step === "done" ? (
      <>
        <Button variant="secondary" onClick={reset} data-testid="import-again">
          Import another file
        </Button>
        <Button onClick={onClose} data-testid="import-done">
          Done
        </Button>
      </>
    ) : step === "map" ? (
      <>
        <Button variant="ghost" onClick={reset} data-testid="import-back">
          Choose a different file
        </Button>
        <Button onClick={commit} disabled={!ready || commitMut.isPending} data-testid="import-commit">
          {commitMut.isPending ? "Importing…" : "Import"}
        </Button>
      </>
    ) : (
      <Button variant="ghost" onClick={onClose} data-testid="import-cancel">
        Cancel
      </Button>
    );

  return (
    <Dialog open onClose={onClose} title="Import CSV" testid="import-dialog" footer={footer}>
      {error && (
        <p
          className="mb-3 rounded-control bg-negative/10 px-3 py-2 text-sm text-negative"
          data-testid="import-error"
        >
          {error}
        </p>
      )}

      {step === "pick" && (
        <div className="space-y-4">
          <Field
            label="CSV file"
            htmlFor={ids.file}
            hint="Read here, and again when you import it. Nothing is stored in between."
          >
            <input
              id={ids.file}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => onFile(e.target.files?.[0] ?? null)}
              className="w-full rounded-control border border-border-strong bg-surface-inset px-3 py-2 text-sm text-fg file:mr-3 file:rounded file:border-0 file:bg-surface-inset file:px-2 file:py-1 file:text-fg"
              data-testid="import-file"
            />
          </Field>
          {previewMut.isPending && <p className="text-sm text-fg-muted">Reading the file…</p>}
          {accounts.length === 0 && (
            <p className="text-sm text-warning" data-testid="import-no-accounts">
              Add an account first — an import has to land somewhere.
            </p>
          )}
        </div>
      )}

      {step === "map" && preview && (
        <div className="space-y-4">
          <Field label="Import into" htmlFor={ids.account} required>
            <Select
              id={ids.account}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              data-testid="import-account"
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Field>

          <div>
            <p className="mb-1 text-xs font-medium text-fg-muted">Sample from your file</p>
            <div
              className="overflow-x-auto rounded-control border border-border"
              data-testid="import-table"
            >
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-inset/60 text-fg-muted">
                  <tr>
                    {preview.headers.map((h) => (
                      <th key={h} className="whitespace-nowrap px-2 py-1 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.sample.map((row, i) => (
                    <tr key={i} className="border-t border-border text-fg">
                      {preview.headers.map((h, j) => (
                        <td key={h} className="whitespace-nowrap px-2 py-1">
                          {row[j] ?? ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-fg-muted">
              Which column is which — correct anything the headers got wrong
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {preview.headers.map((h, i) => {
                const id = `${ids.map}-${i}`;
                return (
                  <Field key={h} label={h} htmlFor={id}>
                    <Select
                      id={id}
                      value={mapping[h] ?? ""}
                      onChange={(e) =>
                        setMapping((m) => ({
                          ...m,
                          [h]: (e.target.value || null) as MappableField | null,
                        }))
                      }
                      data-testid={`import-map-${i}`}
                    >
                      <option value="">Ignore this column</option>
                      {MAPPABLE_FIELDS.map((f) => (
                        <option key={f} value={f}>
                          {f}
                        </option>
                      ))}
                    </Select>
                  </Field>
                );
              })}
            </div>
            <p className="mt-1 text-xs text-fg-muted" data-testid="import-ready-hint">
              {ready
                ? "A date column and an amount column are mapped."
                : "A date column and an amount (or debit/credit) column are required."}
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              label="Default category"
              htmlFor={ids.defaultCategory}
              hint="Where a category name the household does not have lands."
            >
              <Select
                id={ids.defaultCategory}
                value={defaultCategoryId}
                onChange={(e) => setDefaultCategoryId(e.target.value)}
                data-testid="import-default-category"
              >
                <option value="">Uncategorized</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <label
              htmlFor={ids.dayfirst}
              className="mt-1 flex items-start gap-2 text-xs text-fg"
            >
              <input
                id={ids.dayfirst}
                type="checkbox"
                checked={dayfirst}
                onChange={(e) => setDayfirst(e.target.checked)}
                className="mt-0.5"
                data-testid="import-dayfirst"
              />
              <span>
                Read ambiguous dates as day-first (dd/mm/yyyy)
                <span className="block text-fg-muted">
                  Only decides values like 05/03/2026, which are dates both ways.
                </span>
              </span>
            </label>
          </div>
        </div>
      )}

      {step === "done" && result && (
        <div className="space-y-3" data-testid="import-result">
          <p className="text-sm text-fg">
            Imported <strong data-testid="import-inserted">{result.inserted}</strong>
            {result.skipped > 0 && (
              <>
                {" · skipped "}
                <strong data-testid="import-skipped">{result.skipped}</strong> already in the ledger
              </>
            )}
            {result.suspects > 0 && (
              <>
                {" · "}
                <strong className="text-warning" data-testid="import-suspects">
                  {result.suspects}
                </strong>{" "}
                possible duplicate{result.suspects === 1 ? "" : "s"} flagged for review
              </>
            )}
          </p>

          {result.errors.length > 0 && (
            <div data-testid="import-errors">
              <p className="text-xs font-medium text-fg-muted">
                {result.errors.length} row{result.errors.length === 1 ? "" : "s"} could not be read
                and {result.errors.length === 1 ? "was" : "were"} left out:
              </p>
              <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto text-xs text-negative">
                {result.errors.map((e) => (
                  <li key={e.line}>
                    Line {e.line}: {e.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
