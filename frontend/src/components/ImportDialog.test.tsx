import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account, Category } from "@/api/types";
import type { CsvCommitResult, CsvPreview } from "@/api/import";
import ImportDialog from "@/components/ImportDialog";

// The dialog owns its two calls, so stub them rather than standing up an
// AuthProvider, a QueryClient and a fetch mock for a component test. The mocked
// mutate invokes the caller's own callback, which is how the real hook reports
// success too.
const h = vi.hoisted(() => ({
  preview: vi.fn(),
  commit: vi.fn(),
  previewPending: false,
  commitPending: false,
}));

vi.mock("@/api/import", () => ({
  MAPPABLE_FIELDS: ["date", "amount", "debit", "credit", "description", "category", "owner", "notes"],
  useCsvPreview: () => ({ mutate: h.preview, isPending: h.previewPending }),
  useCsvCommit: () => ({ mutate: h.commit, isPending: h.commitPending }),
}));

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

function pick() {
  return userEvent.upload(screen.getByTestId("import-file"), FILE);
}

beforeEach(() => {
  h.preview.mockReset();
  h.commit.mockReset();
  h.previewPending = false;
  h.commitPending = false;
  h.preview.mockImplementation((_file: File, opts: { onSuccess: (p: CsvPreview) => void }) =>
    opts.onSuccess(PREVIEW),
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
});
