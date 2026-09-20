import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Account } from "@/api/types";
import type { ImportResult } from "@/api/portability";
import Settings from "@/pages/Settings";

// The three portability entry points, stubbed at the module: two are downloads
// (an anchor and a blob, neither of which jsdom can do) and the third is a
// mutation whose *result* is what this page has to render. `ImportResult`
// itself is the real type — the fixture below is checked against the shape the
// server sends.
const h = vi.hoisted(() => ({
  downloadExport: vi.fn(),
  downloadAccountCsv: vi.fn(),
  import: vi.fn(),
  importState: {} as Record<string, unknown>,
}));

vi.mock("@/api/portability", async () => {
  const actual = await vi.importActual<typeof import("@/api/portability")>("@/api/portability");
  return {
    ...actual,
    downloadExport: h.downloadExport,
    downloadAccountCsv: h.downloadAccountCsv,
    useImportDocument: () => ({ ...h.importState, mutate: h.import, reset: vi.fn() }),
  };
});

const hh = vi.hoisted(() => ({ role: "owner" }));

// Every hook this page reaches, stubbed — not just the two the Data tab reads.
// The page renders its *first* tab before any click can move it, so a real hook
// left unstubbed is a real query against a QueryClient that does not exist here.
// The loop is deliberate: the alternative is a list that has to be extended
// every time a section gains a read, and the failure it produces points at the
// QueryClient rather than at the hook that was forgotten.
vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>("@/api/hooks");
  const settled = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isSuccess: true,
  });
  const mock: Record<string, unknown> = { ...actual };
  for (const key of Object.keys(actual)) {
    if (key.startsWith("use")) mock[key] = () => settled(undefined);
  }
  mock.useHousehold = () =>
    settled({ name: "Home", base_currency: "USD", timezone: "UTC", role: hh.role });
  mock.useAccounts = () => settled(ACCOUNTS);
  return mock;
});

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    me: { user: { display_name: "Alex", email: "alex@example.com", is_admin: true, csrf_token: "t" } },
    logout: vi.fn(),
  }),
}));

const ACCOUNTS = [{ id: "acct-1", name: "Checking" }] as unknown as Account[];

const FILE = new File(["{}"], "metalmark-export-2026-09-20.json", { type: "application/json" });

/** Render the page and open the Data tab.
 *
 * The provider is real because the downloads are: the component drives them
 * through `useMutation`, and stubbing that would leave the button's own wiring —
 * the part a user actually touches — untested. What the mutations *call* is
 * stubbed, so nothing leaves the process.
 */
async function openData() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByTestId("settings-tab-data"));
}

beforeEach(() => {
  h.downloadExport.mockReset().mockResolvedValue(undefined);
  h.downloadAccountCsv.mockReset().mockResolvedValue(undefined);
  h.import.mockReset();
  h.importState = {};
  hh.role = "owner";
});

describe("Settings → Data", () => {
  it("downloads the household document", async () => {
    await openData();

    await userEvent.click(screen.getByTestId("export-download"));

    expect(h.downloadExport).toHaveBeenCalledTimes(1);
  });

  it("downloads one account's CSV, and asks for the account it shows", async () => {
    await openData();

    await userEvent.click(screen.getByTestId("export-csv-download"));

    // `expect.anything()` is TanStack's second argument to a mutation function
    // (its own mutation context), not something this component passes.
    expect(h.downloadAccountCsv).toHaveBeenCalledWith("acct-1", expect.anything());
  });

  it("hands the picked file to the import", async () => {
    await openData();

    await userEvent.upload(screen.getByTestId("import-document-file"), FILE);
    await userEvent.click(screen.getByTestId("import-document-submit"));

    expect(h.import).toHaveBeenCalledWith(FILE);
  });

  it("does not import with nothing picked", async () => {
    await openData();

    // The submit is disabled rather than firing a request with no body, which
    // the server would answer 422 — an error about the app, not the user.
    expect(screen.getByTestId("import-document-submit")).toBeDisabled();
  });

  it("says a member may not import instead of showing a form that would 403", async () => {
    hh.role = "member";
    await openData();

    expect(screen.getByTestId("import-owner-only")).toBeInTheDocument();
    expect(screen.queryByTestId("import-form")).not.toBeInTheDocument();
    // Export stays: reading the household's ledger is what being a member is.
    expect(screen.getByTestId("export-download")).toBeInTheDocument();
  });
});

describe("Settings → Data → what the import did", () => {
  const RESULT: ImportResult = {
    created: { accounts: 2, transactions: 3, rules: 1 },
    matched: { owners: 1, category_groups: 1 },
    warnings: ["Rule “Coffee” points at a category that is not in this household."],
  };

  it("lists what was created, what was left alone, and what it could not place", async () => {
    h.importState = { data: RESULT, isPending: false, isError: false, error: null };
    await openData();

    const created = screen.getByTestId("import-created");
    expect(created).toHaveTextContent("accounts: 2");
    expect(created).toHaveTextContent("transactions: 3");
    // The server's key, as words — `category_groups` is not what anyone calls it.
    expect(screen.getByTestId("import-matched")).toHaveTextContent("category groups: 1");
    expect(screen.getByTestId("import-warnings")).toHaveTextContent("points at a category");
  });

  it("reads an all-matched import as nothing new", async () => {
    h.importState = {
      data: { created: {}, matched: { transactions: 780 }, warnings: [] },
      isPending: false,
      isError: false,
      error: null,
    };
    await openData();

    // The result of importing your own export back: the reassuring answer, said
    // as one sentence rather than as a list of zeros.
    expect(screen.getByTestId("import-nothing-new")).toBeInTheDocument();
    expect(screen.queryByTestId("import-created")).not.toBeInTheDocument();
    expect(screen.getByTestId("import-matched")).toHaveTextContent("transactions: 780");
  });
});
