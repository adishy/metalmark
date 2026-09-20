// Statement import: choose a file → check what it holds → import → read what
// happened. One dialog for both formats, because for the user they are one task:
// "put the file my bank gave me into the ledger".
//
// A CSV needs a step the OFX branch does not. The importer refuses to guess, so
// the user's confirmation *is* the mapping: shown pre-filled from the headers,
// correctable column by column, and sent as they left it. An OFX/QFX file names
// its own fields (ADR-0030 §4), so its middle step shows what the file says
// about itself instead — which account the bank printed on it, the dates it
// covers, and how many rows are coming — and the human picks the ledger account
// exactly as before.
//
// Either way, anything the importer would not accept — a file with no date
// column, a cell it cannot read, an OFX 1.x file (refused by name, with the way
// out) — comes back as a message rather than a silent default, and the row
// errors name the file line, or for OFX the transaction's position in the file,
// so the row can be found.
import { useState } from "react";
import type { Account, Category } from "@/api/types";
import {
  MAPPABLE_FIELDS,
  isOfxFile,
  useCsvCommit,
  useCsvPreview,
  useOfxCommit,
  useOfxPreview,
  type CsvCommitResult,
  type CsvPreview,
  type MappableField,
  type OfxCommitResult,
  type OfxPreview,
} from "@/api/import";
import Dialog from "@/components/Dialog";
import { Button, Checkbox, Field, Input, Select, Spinner, useFieldId } from "@/components/form";

interface ImportDialogProps {
  onClose: () => void;
  accounts: Account[];
  categories: Category[];
}

type Step = "pick" | "map" | "review" | "done";

/** A file's preview as the dialog holds it: the two formats answer differently,
 * and each is only rendered by the step that asked for it. */
type Preview =
  | { kind: "csv"; csv: CsvPreview }
  | { kind: "ofx"; ofx: OfxPreview }
  | null;

/** One "what the file says" fact. Absent facts render nothing rather than a
 * blank: an empty row would claim the bank sent a value and sent it empty. */
function OfxFact({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <>
      <dt className="text-fg-muted">{label}</dt>
      <dd className="text-fg">{value}</dd>
    </>
  );
}

/** Mounted only while it is open (like the detail sheet), so the picked file and
 * the previous import's result are gone by the time it is opened again. */
export default function ImportDialog({ onClose, accounts, categories }: ImportDialogProps) {
  const [step, setStep] = useState<Step>("pick");
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [preview, setPreview] = useState<Preview>(null);
  const [mapping, setMapping] = useState<Record<string, MappableField | null>>({});
  const [defaultCategoryId, setDefaultCategoryId] = useState("");
  const [dayfirst, setDayfirst] = useState(false);
  const [csvResult, setCsvResult] = useState<CsvCommitResult | null>(null);
  const [ofxResult, setOfxResult] = useState<OfxCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewMut = useCsvPreview();
  const commitMut = useCsvCommit();
  const ofxPreviewMut = useOfxPreview();
  const ofxCommitMut = useOfxCommit();
  const reading = previewMut.isPending || ofxPreviewMut.isPending;
  const importing = commitMut.isPending || ofxCommitMut.isPending;

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
    setCsvResult(null);
    setOfxResult(null);
    setError(null);
  };

  // Preview on pick, not on a second click: the next step is built from the
  // file, so there is nothing to decide before seeing it. Preview writes
  // nothing, so the only cost of choosing the wrong file is choosing another.
  const onFile = (picked: File | null) => {
    setFile(picked);
    setCsvResult(null);
    setOfxResult(null);
    setError(null);
    if (!picked) return;
    const failed = (e: unknown) => setError((e as Error).message);
    if (isOfxFile(picked)) {
      setPreview(null);
      ofxPreviewMut.mutate(picked, {
        onSuccess: (p) => {
          setPreview({ kind: "ofx", ofx: p });
          setStep("review");
        },
        onError: failed,
      });
      return;
    }
    setPreview(null);
    previewMut.mutate(picked, {
      onSuccess: (p) => {
        setPreview({ kind: "csv", csv: p });
        setMapping(p.suggested);
        setStep("map");
      },
      onError: failed,
    });
  };

  const ofx = preview?.kind === "ofx" ? preview.ofx : null;
  const csv = preview?.kind === "csv" ? preview.csv : null;

  // The refused rows, either format, as one list: a CSV error names the file
  // line and an OFX error the transaction's position in the statement, because
  // OFX has no line numbers to point at. The row's own coordinates are the
  // "fixable" part of the message either way.
  const errors = csvResult
    ? csvResult.errors.map((e) => ({ label: `Line ${e.line}`, message: e.message }))
    : (ofxResult?.errors ?? []).map((e) => ({
        label: `Transaction ${e.position}`,
        message: e.message,
      }));

  const fields = Object.values(mapping);
  // The server enforces this too; doing it here is what lets the button say why
  // it is not ready, instead of the answer being a 400 after the click.
  const hasAmount = fields.some((f) => f === "amount" || f === "debit" || f === "credit");
  const ready = !!file && !!accountId && fields.includes("date") && hasAmount;

  const commit = () => {
    if (!file) return;
    setError(null);
    const done = () => setStep("done");
    const failed = (e: unknown) => setError((e as Error).message);
    if (preview?.kind === "ofx") {
      ofxCommitMut.mutate(
        { file, accountId, defaultCategoryId: defaultCategoryId || null },
        {
          onSuccess: (r) => {
            setOfxResult(r);
            done();
          },
          onError: failed,
        },
      );
      return;
    }
    commitMut.mutate(
      { file, accountId, mapping, defaultCategoryId: defaultCategoryId || null, dayfirst },
      {
        onSuccess: (r) => {
          setCsvResult(r);
          done();
        },
        onError: failed,
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
        <Button
          onClick={commit}
          disabled={!ready || importing}
          aria-busy={commitMut.isPending}
          data-testid="import-commit"
        >
          {commitMut.isPending && <Spinner />}
          Import
        </Button>
      </>
    ) : step === "review" ? (
      <>
        <Button variant="ghost" onClick={reset} data-testid="import-back">
          Choose a different file
        </Button>
        {/* Nothing to map, but an account still has to be chosen — the file's
            own <ACCTID> is shown above and never obeyed (ADR-0030 §4). */}
        <Button
          onClick={commit}
          disabled={!accountId || importing || (ofx?.transaction_count ?? 0) === 0}
          aria-busy={ofxCommitMut.isPending}
          data-testid="import-commit"
        >
          {ofxCommitMut.isPending && <Spinner />}
          Import
        </Button>
      </>
    ) : (
      <Button variant="ghost" onClick={onClose} data-testid="import-cancel">
        Cancel
      </Button>
    );

  return (
    <Dialog open onClose={onClose} title="Import a statement" testid="import-dialog" footer={footer}>
      {error && (
        <p
          role="alert"
          className="mb-3 rounded-control bg-negative/10 px-3 py-2 text-sm text-negative"
          data-testid="import-error"
        >
          {error}
        </p>
      )}

      {step === "pick" && (
        <div className="space-y-4">
          <Field
            label="Statement file"
            htmlFor={ids.file}
            hint="CSV, OFX or QFX. Read here, and again when you import it — nothing is stored in between."
          >
            <Input
              id={ids.file}
              type="file"
              accept=".csv,.ofx,.qfx,text/csv,application/x-ofx"
              onChange={(e) => onFile(e.target.files?.[0] ?? null)}
              // The shared control class supplies the 44 px box and 16 px text;
              // only the browser's own file button is left to style here.
              className="file:mr-3 file:rounded file:border-0 file:bg-surface-inset file:px-2 file:py-1 file:text-fg"
              data-testid="import-file"
            />
          </Field>
          {/* A loading label is a status message too (§4.1): it appears while the
              read is in flight and nothing moves focus to it. */}
          {reading && (
            <p role="status" aria-atomic="true" className="text-sm text-fg-muted">
              Reading the file…
            </p>
          )}
          {accounts.length === 0 && (
            <p className="text-sm text-warning" data-testid="import-no-accounts">
              Add an account first — an import has to land somewhere.
            </p>
          )}
        </div>
      )}

      {step === "review" && ofx && (
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

          <div
            className="rounded-control border border-border bg-surface-inset/40 p-3 text-sm"
            data-testid="import-ofx-summary"
          >
            <p className="mb-1 text-xs font-medium text-fg-muted">What the file says</p>
            {/* The bank's own account fields, shown so the human can confirm they
                picked the right download — and never matched on (ADR-0030 §4). */}
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
              <OfxFact label="Institution" value={ofx.org} />
              <OfxFact label="Bank account" value={ofx.acct_id} />
              <OfxFact label="Account type" value={ofx.acct_type} />
              <OfxFact label="Currency" value={ofx.currency} />
              <OfxFact
                label="Covers"
                value={
                  ofx.start && ofx.end
                    ? `${ofx.start.slice(0, 10)} to ${ofx.end.slice(0, 10)}`
                    : null
                }
              />
            </dl>
            <p role="status" aria-atomic="true" className="mt-2 text-fg">
              <strong data-testid="import-ofx-count">{ofx.transaction_count}</strong>
              {ofx.transaction_count === 1 ? " transaction" : " transactions"} in this file
              {ofx.investment_count > 0 && (
                <>
                  {" · "}
                  <strong className="text-warning" data-testid="import-ofx-investments">
                    {ofx.investment_count}
                  </strong>{" "}
                  investment {ofx.investment_count === 1 ? "row" : "rows"} stay out
                </>
              )}
            </p>
          </div>

          {ofx.investment_count > 0 && (
            <p className="text-xs text-fg-muted" data-testid="import-ofx-investment-note">
              Investments land in the ledger in a later workstream, so those rows are counted and
              left out rather than imported as cash movements — a purchase is not a payment.
            </p>
          )}

          {ofx.transaction_count === 0 && (
            <p className="text-sm text-warning" data-testid="import-ofx-empty">
              There are no banking transactions in this file to import.
            </p>
          )}

          <Field
            label="Default category"
            htmlFor={ids.defaultCategory}
            hint="Where an uncategorised row lands. Everything else is left for your rules."
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
        </div>
      )}

      {step === "map" && csv && (
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
            {/* A wide CSV scrolls this sideways, so §4.7 applies: the region
                has to be reachable by keyboard (2.1.1) and the headers scoped,
                or the sample is a grid of unexplained cells to a screen
                reader. */}
            <div
              className="overflow-x-auto rounded-control border border-border"
              role="region"
              aria-label="Sample rows from the file"
              tabIndex={0}
              data-testid="import-table"
            >
              <table className="w-full text-left text-xs">
                <caption className="sr-only">
                  A sample of rows from the file, under the column headings as they appear in it
                </caption>
                <thead className="bg-surface-inset/60 text-fg-muted">
                  <tr>
                    {csv.headers.map((h) => (
                      <th key={h} scope="col" className="whitespace-nowrap px-2 py-1 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {csv.sample.map((row, i) => (
                    <tr key={i} className="border-t border-border text-fg">
                      {csv.headers.map((h, j) => (
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
              {csv.headers.map((h, i) => {
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
            <Checkbox
              id={ids.dayfirst}
              className="mt-1"
              label="Read ambiguous dates as day-first (dd/mm/yyyy)"
              hint="Only decides values like 05/03/2026, which are dates both ways."
              checked={dayfirst}
              onChange={(e) => setDayfirst(e.target.checked)}
              data-testid="import-dayfirst"
            />
          </div>
        </div>
      )}

      {step === "done" && (csvResult || ofxResult) && (
        <div className="space-y-3" data-testid="import-result">
          {/* One region around the whole sentence, not around the numerals: the
              announcement is "Imported 12 · skipped 3", and a bare "12" is not a
              status message (§7.6). */}
          <p role="status" aria-atomic="true" className="text-sm text-fg">
            Imported{" "}
            <strong data-testid="import-inserted">
              {(csvResult ?? ofxResult)!.inserted}
            </strong>
            {(csvResult ?? ofxResult)!.skipped > 0 && (
              <>
                {" · skipped "}
                <strong data-testid="import-skipped">{(csvResult ?? ofxResult)!.skipped}</strong>{" "}
                already in the ledger
              </>
            )}
            {(csvResult ?? ofxResult)!.suspects > 0 && (
              <>
                {" · "}
                <strong className="text-warning" data-testid="import-suspects">
                  {(csvResult ?? ofxResult)!.suspects}
                </strong>{" "}
                possible duplicate{(csvResult ?? ofxResult)!.suspects === 1 ? "" : "s"} flagged for
                review
              </>
            )}
            {ofxResult && ofxResult.investments_skipped > 0 && (
              <>
                {" · "}
                <strong className="text-warning" data-testid="import-investments-skipped">
                  {ofxResult.investments_skipped}
                </strong>{" "}
                investment {ofxResult.investments_skipped === 1 ? "row" : "rows"} skipped
              </>
            )}
          </p>

          {/* The count alone reads as a bug in an all-investment file — "0
              imported, 5 skipped" — so the row that explains it is not optional
              (ADR-0030 §5). */}
          {ofxResult && ofxResult.investments_skipped > 0 && (
            <p className="text-xs text-fg-muted" data-testid="import-investment-note">
              An investment row is not a cash movement — a purchase and the security it buys are
              two views of one event — so those rows are left out until investments have their own
              ledger. Re-importing the file once they do will pick them up.
            </p>
          )}

          {errors.length > 0 && (
            <div data-testid="import-errors">
              {/* The message about the list is the status message; the list below
                  it is a results list and stays plain (§7.6). */}
              <p role="status" aria-atomic="true" className="text-xs font-medium text-fg-muted">
                {errors.length} row{errors.length === 1 ? "" : "s"} could not be read and{" "}
                {errors.length === 1 ? "was" : "were"} left out:
              </p>
              <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto text-xs text-negative">
                {errors.map((e) => (
                  <li key={e.label}>
                    {e.label}: {e.message}
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
