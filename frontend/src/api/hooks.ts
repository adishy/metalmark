import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  Account,
  AccountCreate,
  AccountUpdate,
  Category,
  CategoryGroup,
  FxRate,
  Household,
  Invite,
  Member,
  NetWorth,
  NetWorthSeries,
  SpendingReport,
  SplitIn,
  Tag,
  Transaction,
  TransactionCreate,
  TransactionPage,
  TransactionUpdate,
} from "@/api/types";

export function useAccounts() {
  return useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/accounts") });
}

export function useNetWorth() {
  return useQuery({ queryKey: ["net-worth"], queryFn: () => api.get<NetWorth>("/accounts/net-worth") });
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

export function useNetWorthSeries(start: string, end: string) {
  return useQuery({
    queryKey: ["report-net-worth", start, end],
    queryFn: () => api.get<NetWorthSeries>(`/reports/net-worth?start=${start}&end=${end}`),
  });
}

export function useSpending(start: string, end: string) {
  return useQuery({
    queryKey: ["report-spending", start, end],
    queryFn: () => api.get<SpendingReport>(`/reports/spending?start=${start}&end=${end}`),
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
    qc.invalidateQueries({ queryKey: ["report-spending"] });
  };
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

// -- invites --

export function useCreateInvite() {
  return useMutation({
    mutationFn: (body: { email: string; role: string }) =>
      api.post<Invite>("/auth/invites", body),
  });
}
