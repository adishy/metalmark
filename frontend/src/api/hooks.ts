import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  Account,
  AccountCreate,
  AccountUpdate,
  AutoCategorizeResult,
  BalancePoint,
  BudgetOut,
  BudgetReport,
  CashFlowSankey,
  CashFlowSeries,
  Category,
  CategoryDeleteResult,
  CategoryGroup,
  CategoryUpdate,
  CategoryUsage,
  FxRate,
  GranularityParam,
  Household,
  HouseholdUpdate,
  Me,
  Member,
  NetWorth,
  NetWorthSeries,
  Owner,
  OwnerCreate,
  OwnerReassignment,
  OwnerUpdate,
  SignupCreate,
  SpendingReport,
  SplitIn,
  Tag,
  Transaction,
  TransactionCreate,
  TransactionPage,
  TransactionUpdate,
} from "@/api/types";

/** `?owner_id=…` for the owner-filterable GETs; unfiltered leaves the path bare. */
export function accountsUrl(path: string, ownerId?: string | null): string {
  return ownerId ? `${path}?owner_id=${encodeURIComponent(ownerId)}` : path;
}

/**
 * The report GETs: one window, one owner scope and one granularity.
 *
 * `start` may be `null`, and then the param is **omitted** rather than sent
 * empty — a missing `start` means "from the beginning", which only the server
 * can resolve, and it answers with the day it chose. `end` is never optional:
 * the browser is the side that knows what day it is here, and a server in UTC
 * would be a day off for a household in UTC+13.
 *
 * `granularity` goes on the wire even when it is `auto`, because `auto` is a real
 * answer the server echoes back resolved — the response's `granularity` is what
 * the chart labels come from, never the control's.
 */
export function reportUrl(
  path: string,
  start: string | null,
  end: string,
  ownerId?: string | null,
  granularity?: GranularityParam,
): string {
  const q = new URLSearchParams();
  if (start) q.set("start", start);
  q.set("end", end);
  if (granularity) q.set("granularity", granularity);
  if (ownerId) q.set("owner_id", ownerId);
  return `${path}?${q}`;
}

// The owner filter belongs in the key: an owner-scoped net worth is a different
// series from the household total, and both must stay cached side by side.
export function useAccounts(ownerId?: string | null) {
  return useQuery({
    queryKey: ["accounts", ownerId ?? null],
    queryFn: () => api.get<Account[]>(accountsUrl("/accounts", ownerId)),
  });
}

export function useNetWorth(ownerId?: string | null) {
  return useQuery({
    queryKey: ["net-worth", ownerId ?? null],
    queryFn: () => api.get<NetWorth>(accountsUrl("/accounts/net-worth", ownerId)),
  });
}

export function useOwners() {
  return useQuery({ queryKey: ["owners"], queryFn: () => api.get<Owner[]>("/owners") });
}

export function useCategories() {
  return useQuery({ queryKey: ["categories"], queryFn: () => api.get<Category[]>("/categories") });
}

export function useCategoryGroups() {
  return useQuery({
    queryKey: ["category-groups"],
    queryFn: () => api.get<CategoryGroup[]>("/category-groups"),
  });
}

export function useTags() {
  return useQuery({ queryKey: ["tags"], queryFn: () => api.get<Tag[]>("/tags") });
}

export function useMembers() {
  return useQuery({ queryKey: ["members"], queryFn: () => api.get<Member[]>("/household/members") });
}

export function useHousehold() {
  return useQuery({ queryKey: ["household"], queryFn: () => api.get<Household>("/household") });
}

export function useFxRates() {
  return useQuery({ queryKey: ["fx-rates"], queryFn: () => api.get<FxRate[]>("/fx-rates") });
}

export interface TxnFilter {
  account_id?: string[];
  category_id?: string[];
  /** Matches the effective owner, so inherited transactions are included. */
  owner_id?: string;
  start?: string;
  end?: string;
  review_status?: string;
  search?: string;
  include_hidden?: boolean;
  limit?: number;
}

export function txnQuery(f: TxnFilter, cursor?: string): string {
  const q = new URLSearchParams();
  f.account_id?.forEach((a) => q.append("account_id", a));
  f.category_id?.forEach((c) => q.append("category_id", c));
  if (f.owner_id) q.set("owner_id", f.owner_id);
  if (f.start) q.set("start", f.start);
  if (f.end) q.set("end", f.end);
  if (f.review_status) q.set("review_status", f.review_status);
  if (f.search) q.set("search", f.search);
  if (f.include_hidden) q.set("include_hidden", "true");
  if (f.limit) q.set("limit", String(f.limit));
  if (cursor) q.set("cursor", cursor);
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function useTransactions(filter: TxnFilter = {}) {
  return useQuery({
    queryKey: ["transactions", filter],
    queryFn: () => api.get<TransactionPage>(`/transactions${txnQuery(filter)}`),
  });
}

export function useInfiniteTransactions(filter: TxnFilter = {}) {
  return useInfiniteQuery({
    queryKey: ["transactions", "infinite", filter],
    queryFn: ({ pageParam }) =>
      api.get<TransactionPage>(`/transactions${txnQuery(filter, pageParam)}`),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

// The window, the owner scope and the cut are all in the key: each combination
// is a different report, and stepping back through ranges should be instant
// rather than a refetch of a series that was on screen a moment ago.
export function useNetWorthSeries(
  start: string | null,
  end: string,
  ownerId?: string | null,
  granularity: GranularityParam = "auto",
) {
  return useQuery({
    queryKey: ["report-net-worth", start, end, ownerId ?? null, granularity],
    queryFn: () =>
      api.get<NetWorthSeries>(reportUrl("/reports/net-worth", start, end, ownerId, granularity)),
  });
}

export function useCashFlow(
  start: string | null,
  end: string,
  ownerId?: string | null,
  granularity: GranularityParam = "auto",
) {
  return useQuery({
    queryKey: ["report-cash-flow", start, end, ownerId ?? null, granularity],
    queryFn: () =>
      api.get<CashFlowSeries>(reportUrl("/reports/cash-flow", start, end, ownerId, granularity)),
  });
}

/** No granularity: a Sankey is one graph over the window, so a cut would promise
 *  a *sequence* of graphs rather than the one picture it draws. */
export function useCashFlowSankey(start: string | null, end: string, ownerId?: string | null) {
  return useQuery({
    queryKey: ["report-cash-flow-sankey", start, end, ownerId ?? null],
    queryFn: () =>
      api.get<CashFlowSankey>(reportUrl("/reports/cash-flow/sankey", start, end, ownerId)),
  });
}

/** No granularity: `/reports/spending` is one total over the window and takes
 *  none, so offering a cut here would be a control that changes nothing. */
export function useSpending(start: string | null, end: string, ownerId?: string | null) {
  return useQuery({
    queryKey: ["report-spending", start, end, ownerId ?? null],
    queryFn: () => api.get<SpendingReport>(reportUrl("/reports/spending", start, end, ownerId)),
  });
}

// -- budgets (ADR-0058) --

/**
 * The budget report for one period: the plans, and the spending report's own
 * figures for the same window.
 *
 * `period` is **any day in the month** and may be null, which asks the server
 * for the current one — it resolves "now" in the household's timezone and echoes
 * the month it picked, so this side never has to work out what month it is. The
 * key is null-not-undefined so the default period has exactly one cache entry.
 *
 * No `owner_id`: the spend this is read against is the household's, and
 * narrowing one side of the comparison would compare two populations.
 */
export function useBudgetReport(period: string | null = null) {
  return useQuery({
    queryKey: ["budgets", period],
    queryFn: () =>
      api.get<BudgetReport>(period ? `/budgets?period=${period}` : "/budgets"),
  });
}

/**
 * Set one category's plan for one period — an upsert on `(category, period)`, so
 * this is also the edit path.
 *
 * Both mutations invalidate the report rather than patching the cached row: the
 * plan changes what `total_budget` and `budgeted_spent` are, and a client that
 * recomputed those itself would be the second opinion on spend that ADR-0058
 * exists to avoid.
 */
export function useSetBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { categoryId: string; period: string; amount: string }) =>
      api.put<BudgetOut>(
        `/budgets/${v.categoryId}?period=${encodeURIComponent(v.period)}`,
        { amount: v.amount },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["budgets"] }),
  });
}

/** Clear one category's plan for one period. Idempotent server-side: clearing a
 *  plan that was never set is the state the caller asked for, not a 404. */
export function useClearBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { categoryId: string; period: string }) =>
      api.del<void>(`/budgets/${v.categoryId}?period=${encodeURIComponent(v.period)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["budgets"] }),
  });
}

// ---- mutations ----

export function useInvalidateLedger() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["transactions"] });
    qc.invalidateQueries({ queryKey: ["accounts"] });
    qc.invalidateQueries({ queryKey: ["net-worth"] });
    qc.invalidateQueries({ queryKey: ["report-net-worth"] });
    qc.invalidateQueries({ queryKey: ["report-cash-flow"] });
    qc.invalidateQueries({ queryKey: ["report-spending"] });
    // Budgets are a *report on* the ledger, not a fact about it: every `spent`
    // figure on the page is a sum over transactions, so recording, editing,
    // deleting or re-categorizing one moves the number beside a plan.
    qc.invalidateQueries({ queryKey: ["budgets"] });
    // The investments read side. These are in this list for the same reason the
    // reports are: a sync or an import can create an investment account, bring in
    // trades, or land a price — and the panel stays mounted while it does, so
    // without this it keeps serving what it fetched when the page was opened and
    // there is no refetch-on-focus to rescue it (`main.tsx` turns that off).
    qc.invalidateQueries({ queryKey: ["portfolio"] });
    qc.invalidateQueries({ queryKey: ["allocation"] });
    qc.invalidateQueries({ queryKey: ["holdings"] });
  };
}

// -- owners --

export function useCreateOwner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OwnerCreate) => api.post<Owner>("/owners", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["owners"] }),
  });
}

export function useUpdateOwner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; body: OwnerUpdate }) => api.patch<Owner>(`/owners/${v.id}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["owners"] }),
  });
}

/** Deleting an owner reassigns its accounts, transactions and splits, so every
 * owner-scoped view (ledger + reports, filtered or not) is stale afterwards. */
export function useDeleteOwner() {
  const qc = useQueryClient();
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; reassignTo?: string }) =>
      api.del<OwnerReassignment>(
        `/owners/${v.id}${v.reassignTo ? `?reassign_to=${encodeURIComponent(v.reassignTo)}` : ""}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["owners"] });
      invalidate();
    },
  });
}

// -- household / auth --

export function useUpdateHousehold() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HouseholdUpdate) => api.patch<Household>("/household", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["household"] }),
  });
}

/** Open signup. Returns the new session; the auth context adopts it. */
export function useSignup() {
  return useMutation({
    mutationFn: (body: SignupCreate) => api.post<Me>("/auth/signup", body),
  });
}

// -- accounts --

export function useCreateAccount() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (body: AccountCreate) => api.post<Account>("/accounts", body),
    onSuccess: invalidate,
  });
}

export function useUpdateAccount() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; body: AccountUpdate }) =>
      api.patch<Account>(`/accounts/${v.id}`, v.body),
    onSuccess: invalidate,
  });
}

export function useDeleteAccount() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/accounts/${id}`),
    onSuccess: invalidate,
  });
}

// -- transactions --

export function useCreateTransaction() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (body: TransactionCreate) => api.post<Transaction>("/transactions", body),
    onSuccess: invalidate,
  });
}

export function useUpdateTransaction() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; body: TransactionUpdate }) =>
      api.patch<Transaction>(`/transactions/${v.id}`, v.body),
    onSuccess: invalidate,
  });
}

export function useDeleteTransaction() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/transactions/${id}`),
    onSuccess: invalidate,
  });
}

export function useReplaceSplits() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; splits: SplitIn[] }) =>
      api.put<Transaction>(`/transactions/${v.id}/splits`, v.splits),
    onSuccess: invalidate,
  });
}

// -- categories / groups / tags --

function useInvalidateTaxonomy() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["categories"] });
    qc.invalidateQueries({ queryKey: ["category-groups"] });
    qc.invalidateQueries({ queryKey: ["tags"] });
    // A budget row carries the category's *name* and its emoji, and deleting a
    // category takes its plans with it (the FK is ON DELETE CASCADE), so the
    // report is stale after any of these.
    qc.invalidateQueries({ queryKey: ["budgets"] });
  };
}

export function useCreateCategory() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (body: {
      group_id: string;
      name: string;
      color?: string | null;
      icon?: string | null;
    }) => api.post<Category>("/categories", body),
    onSuccess: invalidate,
  });
}

/** What a delete would move. Fetched only when a confirmation is about to be
 *  shown, so the counts are read at the moment they are promised — and read from
 *  the server, which is the only side that can count them without pulling the
 *  transactions into the browser. */
export function useCategoryUsage(id: string | null) {
  return useQuery({
    queryKey: ["category-usage", id],
    queryFn: () => api.get<CategoryUsage>(`/categories/${id}/usage`),
    enabled: id !== null,
  });
}

/** Delete a category, filing its entries under `reassignTo` — or, when that is
 *  absent, under nothing at all: "Uncategorized" is the *absence* of a category,
 *  so the request carries no target and the column comes back null. It is a
 *  change to rows, not to the taxonomy, so the ledger is invalidated with the
 *  taxonomy — otherwise the sankey and the spending donut would keep counting
 *  entries the delete just moved. */
export function useDeleteCategory() {
  const invalidate = useInvalidateTaxonomy();
  const invalidateLedger = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; reassignTo?: string | null }) =>
      api.del<CategoryDeleteResult>(
        `/categories/${v.id}${
          v.reassignTo ? `?reassign_to=${encodeURIComponent(v.reassignTo)}` : ""
        }`,
      ),
    onSuccess: () => {
      invalidate();
      invalidateLedger();
    },
  });
}

export function useCreateCategoryGroup() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (body: { name: string; type: string }) =>
      api.post<CategoryGroup>("/category-groups", body),
    onSuccess: invalidate,
  });
}

export function useDeleteCategoryGroup() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/category-groups/${id}`),
    onSuccess: invalidate,
  });
}

export function useCreateTag() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (body: { name: string; color?: string | null }) => api.post<Tag>("/tags", body),
    onSuccess: invalidate,
  });
}

export function useDeleteTag() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/tags/${id}`),
    onSuccess: invalidate,
  });
}

// -- fx --

export function useUpsertFxRate() {
  const qc = useQueryClient();
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (body: {
      base_currency: string;
      quote_currency: string;
      rate_date: string;
      rate: string;
    }) => api.post<FxRate>("/fx-rates", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fx-rates"] });
      invalidate();
    },
  });
}


export function useUpdateCategory() {
  const invalidate = useInvalidateTaxonomy();
  // And the ledger with it. A rename is not cosmetic: every report that groups
  // by category groups by the *name* a person reads, so the cash-flow sankey and
  // the spending donut relabel themselves — and a delete moves the transactions
  // that pointed at the category somewhere else, which is a change to rows, not
  // to the taxonomy. Invalidating only the taxonomy left both serving what they
  // fetched when the page opened, and `main.tsx` turns refetch-on-focus off.
  const invalidateLedger = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; body: CategoryUpdate }) =>
      api.patch<Category>(`/categories/${v.id}`, v.body),
    onSuccess: () => {
      invalidate();
      invalidateLedger();
    },
  });
}

/** Admin: link transfers, then re-file every transaction. Overwrites categories. */
export function useAutoCategorizeAll() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: () => api.post<AutoCategorizeResult>("/categories/auto-categorize-all", {}),
    onSuccess: invalidate,
  });
}

// -- balance history --

export function useBalances(accountId: string | null) {
  return useQuery({
    queryKey: ["balances", accountId],
    queryFn: () => api.get<BalancePoint[]>(`/accounts/${accountId}/balances`),
    enabled: accountId !== null,
  });
}

export function usePutBalance() {
  const invalidate = useInvalidateLedger();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { accountId: string; date: string; balance: string }) =>
      api.put<BalancePoint>(`/accounts/${v.accountId}/balances/${v.date}`, {
        balance: v.balance,
      }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["balances", v.accountId] });
      invalidate();
    },
  });
}

export function useDeleteBalance() {
  const invalidate = useInvalidateLedger();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { accountId: string; date: string }) =>
      api.del<void>(`/accounts/${v.accountId}/balances/${v.date}`),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["balances", v.accountId] });
      invalidate();
    },
  });
}
