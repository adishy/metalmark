import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  Account,
  AccountCreate,
  AccountUpdate,
  CashFlowSeries,
  Category,
  CategoryGroup,
  FxRate,
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

/** Same owner scope, but for the report GETs that already carry start/end. */
export function reportUrl(
  path: string,
  start: string,
  end: string,
  ownerId?: string | null,
): string {
  const q = new URLSearchParams({ start, end });
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

export function useNetWorthSeries(start: string, end: string, ownerId?: string | null) {
  return useQuery({
    queryKey: ["report-net-worth", start, end, ownerId ?? null],
    queryFn: () => api.get<NetWorthSeries>(reportUrl("/reports/net-worth", start, end, ownerId)),
  });
}

export function useCashFlow(start: string, end: string, ownerId?: string | null) {
  return useQuery({
    queryKey: ["report-cash-flow", start, end, ownerId ?? null],
    queryFn: () => api.get<CashFlowSeries>(reportUrl("/reports/cash-flow", start, end, ownerId)),
  });
}

export function useSpending(start: string, end: string, ownerId?: string | null) {
  return useQuery({
    queryKey: ["report-spending", start, end, ownerId ?? null],
    queryFn: () => api.get<SpendingReport>(reportUrl("/reports/spending", start, end, ownerId)),
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
  };
}

export function useCreateCategory() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (body: { group_id: string; name: string; color?: string | null }) =>
      api.post<Category>("/categories", body),
    onSuccess: invalidate,
  });
}

export function useDeleteCategory() {
  const invalidate = useInvalidateTaxonomy();
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/categories/${id}`),
    onSuccess: invalidate,
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

