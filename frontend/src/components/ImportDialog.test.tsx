import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Category } from "@/api/types";
import type { CsvCommitResult, CsvPreview, OfxCommitResult, OfxPreview } from "@/api/import";
import ImportDialog from "@/components/ImportDialog";

// The dialog owns its four calls, so stub them rather than standing up an
// AuthProvider, a QueryClient and a fetch mock for a component test. The mocked
// mutate invokes the caller's own callback, which is how the real hook reports
// success too.
//
// `isOfxFile` is deliberately the *real* one: it is what decides which branch a
// picked file takes, and a stubbed copy would let the dialog and the tests agree
// on a rule the app does not have.
const h = vi.hoisted(() => ({
  preview: vi.fn(),
  commit: vi.fn(),
  ofxPreview: vi.fn(),
  ofxCommit: vi.fn(),
  previewPending: false,
  commitPending: false,
}));

vi.mock("@/api/import", async () => {
  const actual = await vi.importActual<typeof import("@/api/import")>("@/api/import");
  return {
    ...actual,
    useCsvPreview: () => ({ mutate: h.preview, isPending: h.previewPending }),
    useCsvCommit: () => ({ mutate: h.commit, isPending: h.commitPending }),
    useOfxPreview: () => ({ mutate: h.ofxPreview, isPending: false }),
    useOfxCommit: () => ({ mutate: h.ofxCommit, isPending: false }),
  };
});

const ACCOUNTS = [
  { id: "acct-1", name: "Checking" },
  { id: "acct-2", name: "Card" },
] as unknown as Account[];
const CATEGORIES = [{ id: "cat-1", name: "Dining" }] as unknown as Category[];

const PREVIEW: CsvPreview = {
  headers: ["Date", "Description", "Amount"],
  sample: [
    ["01/05/2026", "Coffee", "-5.00"],
    ["01/06/2026", "Payroll", "2500.00"],
  ],
  suggested: { Date: "date", Description: "description", Amount: "amount" },
};

const FILE = new File(["Date,Description,Amount\n"], "statement.csv", { type: "text/csv" });
// QFX because it is the shape that trips a naive reader and the one a bank hands
// you by default — no XML declaration, a <?OFX …?> instruction instead.
const OFX_FILE = new File(["<OFX>…</OFX>"], "statement.qfx", { type: "application/x-ofx" });

const OFX_PREVIEW: OfxPreview = {
  org: "Harborline Credit Union",
  acct_id: "000111222333",
  acct_type: "CHECKING",
  currency: "USD",
  start: "2026-01-01T00:00:00Z",
  end: "2026-01-31T00:00:00Z",
  transaction_count: 4,
  investment_count: 0,
};

function pick(file: File = FILE) {
  return userEvent.upload(screen.getByTestId("import-file"), file);
}

beforeEach(() => {
  h.preview.mockReset();
  h.commit.mockReset();
  h.ofxPreview.mockReset();
  h.ofxCommit.mockReset();
  h.previewPending = false;
  h.commitPending = false;
  h.preview.mockImplementation((_file: File, opts: { onSuccess: (p: CsvPreview) => void }) =>
    opts.onSuccess(PREVIEW),
  );
  h.ofxPreview.mockImplementation(
    (_file: File, opts: { onSuccess: (p: OfxPreview) => void }) => opts.onSuccess(OFX_PREVIEW),
  );
});

describe("<ImportDialog />", () => {
  it("previews the picked file and starts from the suggested mapping", async () => {
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick();

    // The sample is the file's own rows, verbatim — the point of showing it.
    expect(await screen.findByTestId("import-table")).toHaveTextContent("Coffee");
    expect(screen.getByTestId("import-table")).toHaveTextContent("-5.00");
    expect(screen.getByLabelText("Date")).toHaveValue("date");
    expect(screen.getByLabelText("Amount")).toHaveValue("amount");
    expect(screen.getByLabelText("Description")).toHaveValue("description");
    // The destination is a decision the importer must not make for the user.
    expect(screen.getByTestId("import-account")).toHaveValue("acct-1");
  });

  it("sends the mapping as the user left it, not as it was suggested", async () => {
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick();

    // A column the headers got wrong: ignore the bank's description entirely.
    await user.selectOptions(screen.getByLabelText("Description"), "");
    await user.selectOptions(screen.getByTestId("import-account"), "acct-2");
    await user.selectOptions(screen.getByTestId("import-default-category"), "cat-1");
    await user.click(screen.getByTestId("import-dayfirst"));
    await user.click(screen.getByTestId("import-commit"));

    expect(h.commit).toHaveBeenCalledWith(
      {
        file: FILE,
        accountId: "acct-2",
        mapping: { Date: "date", Description: null, Amount: "amount" },
        defaultCategoryId: "cat-1",
        dayfirst: true,
      },
      expect.anything(),
    );
  });

  it("will not import until a date and an amount column are mapped", async () => {
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick();
    expect(screen.getByTestId("import-commit")).toBeEnabled();

    await user.selectOptions(screen.getByLabelText("Date"), "");
    expect(screen.getByTestId("import-commit")).toBeDisabled();
    expect(screen.getByTestId("import-ready-hint")).toHaveTextContent("required");
  });

  it("reports what landed, what was already there, and what it refused", async () => {
    const result: CsvCommitResult = {
      inserted: 12,
      skipped: 3,
      suspects: 2,
      errors: [{ line: 4, message: "unrecognised amount 'n/a'" }],
    };
    h.commit.mockImplementation((_input: unknown, opts: { onSuccess: (r: CsvCommitResult) => void }) =>
      opts.onSuccess(result),
    );
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick();
    await user.click(screen.getByTestId("import-commit"));

    expect(screen.getByTestId("import-inserted")).toHaveTextContent("12");
    expect(screen.getByTestId("import-skipped")).toHaveTextContent("3");
    expect(screen.getByTestId("import-suspects")).toHaveTextContent("2");
    // A row error has to be fixable, which means naming the file line.
    expect(screen.getByTestId("import-errors")).toHaveTextContent("Line 4");
    expect(screen.getByTestId("import-errors")).toHaveTextContent("n/a");
  });

  it("shows why a file cannot be imported instead of a mapping UI", async () => {
    h.preview.mockImplementation(
      (_file: File, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("No column is mapped to a date — every imported row needs one.")),
    );
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick();

    expect(await screen.findByTestId("import-error")).toHaveTextContent("No column is mapped to a date");
    expect(screen.queryByTestId("import-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("import-commit")).not.toBeInTheDocument();
  });

  // ---- The OFX/QFX branch (ADR-0030) ---------------------------------------

  it("reads an OFX file that names its own fields, with nothing to map", async () => {
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);

    // What the bank printed on the file, so the human can confirm they grabbed
    // the right download — reported, never obeyed (§4).
    const summary = await screen.findByTestId("import-ofx-summary");
    expect(summary).toHaveTextContent("Harborline Credit Union");
    expect(summary).toHaveTextContent("000111222333");
    expect(summary).toHaveTextContent("2026-01-01 to 2026-01-31");
    expect(screen.getByTestId("import-ofx-count")).toHaveTextContent("4");
    // No mapping step: an OFX file has no columns to get wrong.
    expect(screen.queryByTestId("import-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("import-ready-hint")).not.toBeInTheDocument();
    // And the branch really is the OFX one — the CSV preview was never called.
    expect(h.preview).not.toHaveBeenCalled();
    expect(h.ofxPreview).toHaveBeenCalledWith(OFX_FILE, expect.anything());
    // The destination is still the human's decision.
    expect(screen.getByTestId("import-account")).toHaveValue("acct-1");
  });

  it("imports an OFX file into the chosen account without a mapping", async () => {
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);
    await user.selectOptions(screen.getByTestId("import-account"), "acct-2");
    await user.selectOptions(screen.getByTestId("import-default-category"), "cat-1");
    await user.click(screen.getByTestId("import-commit"));

    expect(h.ofxCommit).toHaveBeenCalledWith(
      { file: OFX_FILE, accountId: "acct-2", defaultCategoryId: "cat-1" },
      expect.anything(),
    );
    expect(h.commit).not.toHaveBeenCalled();
  });

  it("will not import an OFX file with no banking rows in it", async () => {
    h.ofxPreview.mockImplementation(
      (_file: File, opts: { onSuccess: (p: OfxPreview) => void }) =>
        opts.onSuccess({ ...OFX_PREVIEW, transaction_count: 0, investment_count: 5 }),
    );
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);

    expect(await screen.findByTestId("import-ofx-empty")).toHaveTextContent(
      "no banking transactions",
    );
    expect(screen.getByTestId("import-commit")).toBeDisabled();
  });

  it("counts investment rows as skipped, with the reason next to the number", async () => {
    const result: OfxCommitResult = {
      inserted: 0,
      skipped: 0,
      suspects: 0,
      investments_skipped: 5,
      errors: [],
    };
    h.ofxCommit.mockImplementation(
      (_input: unknown, opts: { onSuccess: (r: OfxCommitResult) => void }) => opts.onSuccess(result),
    );
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);
    await user.click(screen.getByTestId("import-commit"));

    // "0 imported, 5 skipped" reads as a bug on its own, so the count is not
    // reported without the sentence that explains it (§5).
    expect(screen.getByTestId("import-inserted")).toHaveTextContent("0");
    expect(screen.getByTestId("import-investments-skipped")).toHaveTextContent("5");
    expect(screen.getByTestId("import-investment-note")).toHaveTextContent("not a cash movement");
    // A skipped investment row is not one the ledger already has, so it must not
    // be counted as one.
    expect(screen.queryByTestId("import-skipped")).not.toBeInTheDocument();
  });

  it("names a refused OFX row by its position in the file", async () => {
    const result: OfxCommitResult = {
      inserted: 3,
      skipped: 0,
      suspects: 0,
      investments_skipped: 2,
      errors: [{ position: 3, message: "unreadable amount 'n/a'" }],
    };
    h.ofxCommit.mockImplementation(
      (_input: unknown, opts: { onSuccess: (r: OfxCommitResult) => void }) => opts.onSuccess(result),
    );
    const user = userEvent.setup();
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);
    await user.click(screen.getByTestId("import-commit"));

    // OFX has no line numbers, so "Line 3" would be a lie.
    expect(screen.getByTestId("import-errors")).toHaveTextContent("Transaction 3");
    expect(screen.getByTestId("import-errors")).toHaveTextContent("n/a");
  });

  it("shows a 1.x refusal as the message it is, not as a broken file", async () => {
    // The refusal is the entire mitigation (ADR-0030 §1), so the dialog's job is
    // to render it verbatim — including the way out.
    h.ofxPreview.mockImplementation((_file: File, opts: { onError: (e: Error) => void }) =>
      opts.onError(
        new Error(
          "This file is OFX 1.x (SGML), which this importer does not read. Export it as " +
            "OFX 2.x or QFX, or upload a CSV instead.",
        ),
      ),
    );
    render(
      <ImportDialog onClose={vi.fn()} accounts={ACCOUNTS} categories={CATEGORIES} />,
    );
    await pick(OFX_FILE);

    const alert = await screen.findByTestId("import-error");
    expect(alert).toHaveTextContent("OFX 1.x");
    expect(alert).toHaveTextContent("QFX");
    expect(alert).toHaveTextContent("CSV");
    expect(screen.queryByTestId("import-ofx-summary")).not.toBeInTheDocument();
    expect(screen.queryByTestId("import-commit")).not.toBeInTheDocument();
  });
});
