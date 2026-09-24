import { useMemo, useState } from "react";
import { categoryLabel } from "@/components/CategoryPicker";
import {
  useAccounts,
  useCategories,
  useCreateTransaction,
  useInfiniteTransactions,
  useOwners,
  useTags,
  type TxnFilter,
} from "@/api/hooks";
import type { Account, Category, Owner, Transaction, TransactionCreate } from "@/api/types";
import { formatMoney } from "@/lib/format";
import { todayIso } from "@/lib/dates";
import AccountMark from "@/components/AccountMark";
import { Day } from "@/components/datetime";
import {
  Button,
  Field,
  Input,
  Select,
  Spinner,
  requiredText,
  useFieldId,
  validAmount,
} from "@/components/form";
import OwnerSelect from "@/components/OwnerSelect";
import OwnerFilterChips from "@/components/OwnerFilterChips";
import { FilterIcon, PlusIcon, SearchIcon, UploadIcon } from "@/components/icons";
import TxnPhoneList from "@/components/TxnPhoneList";
import TxnDetailSheet from "@/components/TxnDetailSheet";
import ImportDialog from "@/components/ImportDialog";

/**
 * Whether anything is actually narrowing the list. Used twice — once to offer
 * "Clear filters" in the bar, once to offer it in the empty state — and it has
 * to be one predicate, because the empty state uses it to decide whether the
 * ledger is empty or merely filtered, and those two deserve different sentences.
 *
 * `?.length` rather than the bare truthiness the call sites used to do. The
 * setters today clear to `undefined` rather than `[]`, so the two agree — but
 * an empty array is truthy, so the shorter form is one refactor away from
 * calling a cleared filter "active" and offering a Clear button for nothing.
 */
function isFiltered(f: TxnFilter): boolean {
  return Boolean(
    f.account_id?.length || f.category_id?.length || f.owner_id || f.start || f.end || f.search,
  );
}

/**
 * The ledger's columns at `lg:` (§9.4), shared by the header row and every
 * transaction row so the two cannot drift — one string, two call sites.
 *
 * Written out as a literal rather than built from parts because Tailwind reads
 * source *text*: `grid-cols-${n}` is never generated, and the failure is a
 * silent one (the columns collapse to a single column and the row still
 * renders, just wrong).
 *
 * The widths are chosen for the *narrower* of the two cases this row is used
 * at: §9.3 puts the list beside the detail pane, so the list gets roughly two
 * thirds of the page — about 805 px — and every fixed column is sized to hold
 * its own worst value there. The description takes what is left, which is the
 * one column that can still say something useful when it is 300 px wide.
 *
 * The two axes are separated on purpose. 12 px is the gap *between columns* —
 * the same step the phone row uses, so a value keeps the same distance from its
 * neighbour at both widths. It is not the leading between the rows of one
 * stacked cell: a badged row is three lines in one column, and 12 px of
 * line-gap turned an extra fact into an extra double-spaced one.
 */
const ROW_COLUMNS =
  "lg:grid-cols-[1.75rem_minmax(0,1fr)_5rem_7rem_5.5rem_7rem] lg:gap-x-3 lg:gap-y-1";

export default function Transactions() {
  const accounts = useAccounts();
  const categories = useCategories();
  const tags = useTags();
  const owners = useOwners();
  const [filter, setFilter] = useState<TxnFilter>({});
  const activeFilters = [
    filter.account_id?.length,
    filter.category_id?.length,
    filter.owner_id,
    filter.start,
    filter.end,
    filter.search,
  ].filter(Boolean).length;
  const txns = useInfiniteTransactions(filter);
  const create = useCreateTransaction();
  const [open, setOpen] = useState(false);
  // On a phone the filter panel folds behind a button: open, it is a whole
  // screen of chips and fields before the first transaction (§5 — the list is
  // what the page is for). From `sm:` up it is always shown.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Transaction | null>(null);

  const catById = useMemo(() => {
    const m = new Map<string, Category>();
    categories.data?.forEach((c) => m.set(c.id, c));
    return m;
  }, [categories.data]);

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, categoryLabel(c)));
    return m;
  }, [categories.data]);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  // Whole accounts, not just names: the mark needs the institution too (it picks
  // the hue), and the owner tooltip needs the name. One map, so the two cannot
  // disagree about which account a row belongs to.
  const acctFor = useMemo(() => {
    const m = new Map<string, Account>();
    accounts.data?.forEach((a) => m.set(a.id, a));
    return m;
  }, [accounts.data]);

  /** The account's name, for the sentences that only need to name it. */
  const acctName = (id: string) => acctFor.get(id)?.name ?? "the account";

  const tagName = useMemo(() => {
    const m = new Map<string, string>();
    tags.data?.forEach((t) => m.set(t.id, t.name));
    return m;
  }, [tags.data]);

  const items = txns.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = isFiltered(filter);

  return (
    // No width cap of its own: a ledger list is §9.1's widest case, so it takes
    // the whole shell column and `max-w-7xl` from `AppShell` is the cap.
    //
    // §9.3's shape: at `lg:` the page is two columns — the list on the left at
    // two thirds, the detail pane on the right. Below `lg:` the outer element is
    // a plain block and the second column contributes nothing, because the
    // selected row opens the bottom sheet *over* the list instead. Both are the
    // same `TxnDetailSheet`; it picks, and it picks once, so there is never a
    // second copy of a half-edited transaction.
    //
    // `gap-6` is §2.5's between-cards step, and `items-start` matters: without
    // it the list column stretches to the grid's height and its last card is
    // painted at the bottom of a column it does not fill.
    <div
      className="lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start lg:gap-6"
      data-testid="transactions-page"
    >
      <div className="space-y-4">
        {/* flex-wrap: the title plus two actions do not fit at 360px, and an
            unwrapped header is what pushes the page into horizontal scroll (§5). */}
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-xl font-semibold">Transactions</h1>
          <div className="flex items-center gap-2">
            {/* A whole statement at once — CSV, OFX or QFX, the dialog asks which
                by looking at the file — next to adding one row by hand. On a
                phone both are icon buttons, so the title keeps its line. */}
            <Button
              variant="secondary"
              onClick={() => setImporting(true)}
              aria-label="Import a statement"
              className="px-3 sm:px-4"
              data-testid="import-csv"
            >
              <UploadIcon className="size-5 sm:hidden" />
              <span className="hidden sm:inline">Import a statement</span>
            </Button>
            <Button
              onClick={() => setOpen((v) => !v)}
              aria-label="Add transaction"
              className="px-3 sm:px-4"
              data-testid="add-transaction"
            >
              <PlusIcon className="size-5 sm:hidden" />
              <span className="hidden sm:inline">Add transaction</span>
            </Button>
          </div>
        </div>

        {/* Phone: search first, as the mainstream apps do, with every other
            filter one tap away behind the button beside it. */}
        <div className="flex items-center gap-2 sm:hidden">
          <label className="relative block min-w-0 flex-1">
            <span className="sr-only">Search transactions</span>
            <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-5 -translate-y-1/2 text-fg-muted" />
            <Input
              type="search"
              placeholder="Search"
              value={filter.search ?? ""}
              onChange={(e) => setFilter({ ...filter, search: e.target.value || undefined })}
              className="pl-10"
              data-testid="txn-search-phone"
            />
          </label>
          <Button
            variant="secondary"
            className="relative px-3"
            aria-expanded={filtersOpen}
            aria-controls="txn-filters"
            onClick={() => setFiltersOpen((v) => !v)}
            data-testid="txn-filters-toggle"
          >
            <FilterIcon />
            <span className="sr-only">Filters</span>
            {activeFilters > 0 && (
              <span
                aria-hidden="true"
                className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-fg"
              >
                {activeFilters}
              </span>
            )}
            <span className="sr-only">{activeFilters > 0 ? `, ${activeFilters} active` : ""}</span>
          </Button>
        </div>

        {open && (
          <AddTxnForm
            accounts={accounts.data ?? []}
            categories={categories.data ?? []}
            owners={owners.data ?? []}
            pending={create.isPending}
            error={create.isError ? (create.error as Error).message : null}
            onSubmit={(b) => create.mutate(b, { onSuccess: () => setOpen(false) })}
          />
        )}

        <div id="txn-filters" className={filtersOpen ? "block" : "hidden sm:block"}>
          <FilterBar
            accounts={accounts.data ?? []}
            categories={categories.data ?? []}
            owners={owners.data ?? []}
            filter={filter}
            onChange={setFilter}
          />
        </div>

        {/* The header row and the list share a wrapper so they are one item in the
            page's `space-y-4` stack rather than two.

            Deliberately **no `overflow-hidden`** here, which is the usual way to
            hold a child's background inside a rounded parent. It would also clip
            the row's focus ring — `outline-offset: 2px` puts two pixels of outline
            outside the row on each side — and a clipped focus indicator is the one
            thing §7 item 3 cannot have. Nothing here needs clipping anyway: the
            rows paint no background of their own (§9.5's hover is on the row, and
            that is inside the `<ul>`'s own rounded box). */}
        {/* Phone: days and roomy rows (TxnPhoneList). */}
        <div className="sm:hidden">
          {items.length > 0 ? (
            <TxnPhoneList
              items={items}
              categories={catById}
              accounts={acctFor}
              onOpen={setSelected}
            />
          ) : (
            !txns.isLoading && (
              <p role="status" className="rounded-card bg-surface-raised px-4 py-8 text-center text-sm text-fg-muted">
                {filtered ? "No transactions match these filters." : "No transactions yet."}
              </p>
            )
          )}
        </div>

        <div className="hidden sm:block">
          {/* A real header row, and `lg:` only: below that the row is a phone row
              with no columns to name. `aria-hidden` because it is a *second*
              naming of values every row already carries as text — the visual
              reader needs to know which column is which, and a screen reader
              hearing "Description Date Category Owner Amount" before every row's
              own values would be read the same thing twice.

              The first cell is empty on purpose: the mark column holds a picture,
              and a picture with a printed caption above it is worse than the
              accessible name and `title` that `AccountMark` already carries. */}
          <div
            aria-hidden="true"
            className={`hidden px-4 pb-2 text-xs font-medium text-fg-muted lg:grid ${ROW_COLUMNS}`}
          >
            <span />
            <span>Description</span>
            <span>Date</span>
            <span>Category</span>
            <span>Owner</span>
            <span className="text-right">Amount</span>
          </div>
          <ul className="divide-y divide-border rounded-card bg-surface-raised" data-testid="txn-list">
            {items.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  // `flex` is the phone row; `lg:grid` is §9.4's row that gains
                  // columns. One element, one set of children, two layouts — the
                  // `min-h-12` at `lg:` is a floor of 48 px, and §5's 44 px target
                  // rule still applies above it, which is why the row gets shorter
                  // on a desktop but never short.
                  className={`flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-surface-inset/60 lg:min-h-12 lg:grid ${ROW_COLUMNS}`}
                  onClick={() => setSelected(t)}
                  data-testid={`txn-row-${t.id}`}
                >
                  {/* The mark leads the row because it answers the question the
                      description cannot: a ledger running several accounts shows
                      "Coffee" three times, and which one it came out of is the
                      difference between a personal and a business expense. At 20px
                      it costs the description 28px of width, and gives back the
                      account without a second line.

                      At `lg:` this is §9.4's account column — the one column with
                      no header above it, because it holds a picture and
                      `AccountMark` already carries the account's name twice over,
                      as its accessible name and as its `title` (§9.5). */}
                  <AccountMark
                    name={acctFor.get(t.account_id)?.name ?? "(unknown account)"}
                    institution={acctFor.get(t.account_id)?.institution}
                  />

                  {/* `lg:contents` is what lets one piece of JSX be both the phone
                      row and the desktop row rather than two trees that drift
                      (§9). The wrapper is layout at 360 px and stops existing at
                      1024, which promotes its three children — the description,
                      the meta line and the tags — to grid items of the row itself,
                      free to be given `col-start`s.

                      Nothing is hidden at either width, so the row's accessible
                      name is the same text at both. A duplicate-and-hide pair of
                      trees could not promise that, and the name a screen reader
                      hears changing with the viewport is a bug nobody would notice
                      writing. */}
                  <div className="flex min-w-0 flex-1 items-center gap-2 lg:contents">
                    <div className="flex min-w-0 items-center gap-2 lg:col-start-2 lg:row-start-1">
                      <p className="truncate font-medium">
                        {t.merchant || t.description || "(no description)"}
                      </p>
                      {/* A sibling of the truncating text, not part of it: a badge
                          clipped to "spl…" is not a label. */}
                      {t.is_split_parent && (
                        <span className="shrink-0 rounded bg-surface-inset px-1.5 py-0.5 text-xs text-fg">
                          split
                        </span>
                      )}
                    </div>

                    {/* The phone meta line, and at `lg:` the three middle columns.
                        `lg:contents` dissolves the paragraph so its children take
                        `col-start`s of their own — the meta line *becomes* the
                        columns instead of sitting beside them (§9.4).

                        The `·` separators are `lg:hidden` and `aria-hidden`: they
                        are phone punctuation, and at `lg:` the columns already say
                        where one value ends and the next begins. */}
                    <p className="truncate text-xs text-fg-muted lg:contents">
                      {/* `compact`: "Today"/"Yesterday" at a glance, the date once
                          it is older. The row is the densest surface in the app, so
                          it gets the shortest vocabulary — the ISO form is a hover
                          away either way, and `<Day>` carries it in both `title`
                          and `datetime` (§9.5). */}
                      <Day
                        value={t.transacted_at}
                        style="compact"
                        className="lg:col-start-3 lg:truncate"
                      />
                      <span className="lg:col-start-4 lg:truncate">
                        {t.category_id && (
                          <>
                            <span aria-hidden="true" className="lg:hidden">
                              {" · "}
                            </span>
                            {catName.get(t.category_id) ?? ""}
                          </>
                        )}
                        {/* An empty cell reads as "not loaded"; saying so is the
                            prompt to file it. Desktop only — on a phone the row's
                            "needs review" badge already says it. */}
                        {!t.category_id && !t.is_split_parent && (
                          <span className="hidden lg:inline">Uncategorized</span>
                        )}
                      </span>
                      {/* The effective owner is what reports actually bucket by, so
                          that is what the row shows; a muted style marks the ones
                          that only inherit it from their account. */}
                      <span
                        className={`lg:col-start-5 lg:truncate ${
                          t.owner_id ? "text-fg" : "italic text-fg-muted"
                        }`}
                        title={
                          t.owner_id
                            ? "Owner set on this transaction"
                            : `Inherited from ${acctName(t.account_id)}`
                        }
                        data-testid={`txn-owner-${t.id}`}
                      >
                        <span aria-hidden="true" className="lg:hidden">
                          {" · "}
                        </span>
                        {ownerName.get(t.effective_owner_id) ?? "Shared"}
                        {!t.owner_id && (
                          <>
                            {/* Inherited needs a marker that survives touch and
                                greyscale: the glyph is decorative, the sentence
                                behind it is the accessible name (§7.8). */}
                            <span aria-hidden="true"> ↳</span>
                            <span className="sr-only">
                              {` (inherited from ${acctName(t.account_id)})`}
                            </span>
                          </>
                        )}
                      </span>
                      {/* Last in the meta line at 360 px, exactly where it has
                          always been — a badge next to the description was tried
                          and it costs the merchant its name ("Corner Pharmacy"
                          truncated to "Cor…" at 360 px, because the badge is 110 px
                          of a 188 px cell).

                          At `lg:` it is a badge again, on its own row of the
                          description column: it is a state *of* this row, not a
                          column, so it has no column to sit in, and a row of its
                          own is the honest place for it. Row 3 rather than row 2
                          because the tags take row 2 when they exist — a flagged,
                          tagged row is three lines, and neither of the other two
                          cases pays for an empty track. */}
                      {t.review_status === "needs_review" && (
                        <span
                          className="lg:col-start-2 lg:row-start-3 lg:justify-self-start lg:rounded lg:bg-warning/15 lg:px-1.5 lg:py-0.5 lg:text-warning-ink"
                          data-testid={`txn-review-${t.id}`}
                        >
                          <span aria-hidden="true" className="lg:hidden">
                            {" · "}
                          </span>
                          needs review
                        </span>
                      )}
                    </p>

                    {/* Row two of the description column at `lg:`, under the name
                        it belongs to. `col-start-2` is what puts the tags under
                        the description rather than under the mark. */}
                    {t.tag_ids.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1 lg:col-start-2 lg:row-start-2 lg:mt-0">
                        {t.tag_ids.map((id) => (
                          <span key={id} className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent-ink">
                            {tagName.get(id) ?? "tag"}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* shrink-0 and text-right: the merchant gives way, the number
                      never does (§6.5). */}
                  <span
                    className={`shrink-0 text-right text-base font-semibold tabular-nums lg:col-start-6 lg:row-start-1 ${
                      Number(t.amount) < 0 ? "text-fg" : "text-positive"
                    }`}
                  >
                    {formatMoney(t.amount, t.currency)}
                  </span>
                </button>
              </li>
            ))}
            {items.length === 0 && !txns.isLoading && (
              <li className="px-4">
                {/* The message *about* the results, not the list itself: a filter
                    change that empties the ledger is otherwise silent (§7.6).

                    Two sentences, not one: "no transactions match" is a claim
                    about the filters, and on a genuinely empty ledger nothing was
                    filtered, so it would be describing a cause that isn't there. */}
                <p role="status" aria-atomic="true" className="pt-6 text-center text-sm text-fg-muted">
                  {filtered ? "No transactions match these filters." : "No transactions yet."}
                </p>
                {/* §4.10's escape hatch, at the point of failure. The filter bar
                    has its own Clear, but it is off-screen above by the time the
                    user has scrolled here to find out why the list is empty. */}
                {filtered && (
                  <div className="flex justify-center pb-6 pt-3">
                    <Button variant="secondary" onClick={() => setFilter({})} data-testid="empty-clear-filters">
                      Clear filters
                    </Button>
                  </div>
                )}
              </li>
            )}
          </ul>
        </div>

        {txns.hasNextPage && (
          <div className="flex justify-center">
            <Button
              variant="secondary"
              disabled={txns.isFetchingNextPage}
              aria-busy={txns.isFetchingNextPage}
              onClick={() => txns.fetchNextPage()}
              data-testid="txn-load-more"
            >
              {txns.isFetchingNextPage && <Spinner />}
              Load more
            </Button>
          </div>
        )}

        {importing && (
          <ImportDialog
            onClose={() => setImporting(false)}
            accounts={accounts.data ?? []}
            categories={categories.data ?? []}
          />
        )}
      </div>

      {/* The pane column. It exists at every width so the column has somewhere
          to be, and is `hidden` below `lg:` because at 360 px there is no second
          column to fill and a stray line under the list would be noise.

          The empty state is not an error and not a spinner: nothing is wrong
          and nothing is loading, the user simply has not picked a row. It says
          what the column is for, which is the one thing a blank two-thirds-of-
          a-page gap does not. */}
      <div>
        {!selected && (
          <p
            className="hidden rounded-card bg-surface-raised px-4 py-6 text-center text-sm text-fg-muted lg:block"
            data-testid="txn-detail-empty"
          >
            {items.length === 0
              ? "Nothing to show."
              : "Select a transaction to see and edit it here."}
          </p>
        )}
        {selected && (
          <TxnDetailSheet
            txn={selected}
            accounts={accounts.data ?? []}
            categories={categories.data ?? []}
            tags={tags.data ?? []}
            onClose={() => setSelected(null)}
            onReplaced={(updated) => setSelected(updated)}
          />
        )}
      </div>
    </div>
  );
}

function FilterBar({
  accounts,
  categories,
  owners,
  filter,
  onChange,
}: {
  accounts: Account[];
  categories: Category[];
  owners: Owner[];
  filter: TxnFilter;
  onChange: (f: TxnFilter) => void;
}) {
  const catId = useFieldId("filter-category");
  const startId = useFieldId("filter-start");
  const endId = useFieldId("filter-end");
  const searchId = useFieldId("filter-search");

  const selectedAccounts = new Set(filter.account_id ?? []);
  const toggleAccount = (id: string) => {
    const next = new Set(selectedAccounts);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange({ ...filter, account_id: next.size ? Array.from(next) : undefined });
  };

  return (
    <div className="space-y-3 rounded-card bg-surface-raised p-4" data-testid="txn-filter-bar">
      <OwnerFilterChips
        owners={owners}
        value={filter.owner_id ?? null}
        onChange={(id) => onChange({ ...filter, owner_id: id ?? undefined })}
      />

      <div>
        <p className="mb-1 text-xs font-medium text-fg-muted">Accounts</p>
        <div className="flex flex-wrap gap-2" data-testid="filter-accounts">
          {accounts.map((a) => {
            const on = selectedAccounts.has(a.id);
            // Same geometry as OwnerFilterChips: `px-3 py-1 text-xs` computes to
            // 24px, and a chip is a thumb target (§4.5).
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => toggleAccount(a.id)}
                aria-pressed={on}
                className={`inline-flex min-h-11 max-w-full items-center rounded-full border px-4 text-sm ${
                  on
                    ? "border-accent bg-accent/20 font-medium text-accent-ink"
                    : "border-border-strong text-fg-muted hover:text-fg"
                }`}
                data-testid={`filter-account-${a.id}`}
              >
                {a.name}
              </button>
            );
          })}
          {accounts.length === 0 && <span className="text-xs text-fg-muted">No accounts</span>}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Category" htmlFor={catId}>
          <Select
            id={catId}
            value={filter.category_id?.[0] ?? ""}
            onChange={(e) =>
              onChange({ ...filter, category_id: e.target.value ? [e.target.value] : undefined })
            }
            data-testid="filter-category"
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
            ))}
          </Select>
        </Field>
        <Field label="From" htmlFor={startId}>
          <Input
            id={startId}
            type="date"
            value={filter.start ? filter.start.slice(0, 10) : ""}
            onChange={(e) =>
              onChange({
                ...filter,
                start: e.target.value ? new Date(e.target.value + "T00:00:00Z").toISOString() : undefined,
              })
            }
            data-testid="filter-start"
          />
        </Field>
        <Field label="To" htmlFor={endId}>
          <Input
            id={endId}
            type="date"
            value={filter.end ? filter.end.slice(0, 10) : ""}
            onChange={(e) =>
              onChange({
                ...filter,
                end: e.target.value ? new Date(e.target.value + "T23:59:59Z").toISOString() : undefined,
              })
            }
            data-testid="filter-end"
          />
        </Field>
        <Field label="Search" htmlFor={searchId}>
          <Input
            id={searchId}
            type="search"
            placeholder="Merchant or notes"
            value={filter.search ?? ""}
            onChange={(e) => onChange({ ...filter, search: e.target.value || undefined })}
            data-testid="filter-search"
          />
        </Field>
      </div>

      {isFiltered(filter) && (
        <Button
          variant="ghost"
          onClick={() => onChange({})}
          data-testid="filter-clear"
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}

function AddTxnForm({
  accounts,
  categories,
  owners,
  pending,
  error,
  onSubmit,
}: {
  accounts: Account[];
  categories: Category[];
  owners: Owner[];
  pending: boolean;
  error: string | null;
  onSubmit: (b: TransactionCreate) => void;
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [amount, setAmount] = useState("-0.00");
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [owner, setOwner] = useState<string | null>(null);
  const [date, setDate] = useState(() => todayIso());
  const [errs, setErrs] = useState<Record<string, string | null>>({});

  // What an unset owner inherits: the owning account's own owner.
  const accountOwnerId = accounts.find((a) => a.id === accountId)?.owner_id;
  const inheritFrom = owners.find((o) => o.id === accountOwnerId)?.name;

  const ids = {
    account: useFieldId("txn-account"),
    date: useFieldId("txn-date"),
    merchant: useFieldId("txn-merchant"),
    amount: useFieldId("txn-amount"),
    category: useFieldId("txn-category"),
  };

  const validate = () => {
    const e = { account: requiredText(accountId), amount: validAmount(amount) };
    setErrs(e);
    return !e.account && !e.amount;
  };

  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-card bg-surface-raised p-4 sm:grid-cols-2"
      data-testid="add-txn-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validate()) return;
        onSubmit({
          account_id: accountId,
          amount,
          merchant: merchant || null,
          category_id: categoryId || null,
          owner_id: owner,
          transacted_at: new Date(date + "T12:00:00Z").toISOString(),
        });
      }}
    >
      <Field label="Account" htmlFor={ids.account} required error={errs.account}>
        <Select id={ids.account} value={accountId} onChange={(e) => setAccountId(e.target.value)} data-testid="txn-account">
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </Select>
      </Field>
      <Field label="Date" htmlFor={ids.date}>
        <Input id={ids.date} type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="txn-date" />
      </Field>
      <Field label="Merchant" htmlFor={ids.merchant}>
        <Input id={ids.merchant} value={merchant} onChange={(e) => setMerchant(e.target.value)} data-testid="txn-merchant" />
      </Field>
      <Field label="Amount" htmlFor={ids.amount} required error={errs.amount} hint="Negative = spend">
        <Input id={ids.amount} value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} data-testid="txn-amount" />
      </Field>
      <Field label="Category" htmlFor={ids.category}>
        <Select id={ids.category} value={categoryId} onChange={(e) => setCategoryId(e.target.value)} data-testid="txn-category">
          <option value="">Uncategorized</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
          ))}
        </Select>
      </Field>
      <OwnerSelect
        value={owner}
        onChange={setOwner}
        nullable
        inheritFrom={inheritFrom}
        testid="txn-owner"
      />
      {/* role="alert": a failed save is announced without moving focus (WCAG 4.1.3). */}
      {error && <p className="text-sm text-negative sm:col-span-2" role="alert">{error}</p>}
      <Button type="submit" disabled={pending} className="sm:col-span-2" data-testid="txn-save">
        Save
      </Button>
    </form>
  );
}
