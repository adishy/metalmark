import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  Account,
  Category,
  CategoryGroup,
  NetWorth,
  NetWorthSeries,
  SpendingReport,
  Tag,
  Transaction,
  TransactionPage,
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

export interface TxnFilter {
  account_id?: string[];
  category_id?: string[];
  start?: string;
  end?: string;
  review_status?: string;
  search?: string;
}

function txnQuery(f: TxnFilter, cursor?: string): string {
  const q = new URLSearchParams();
  f.account_id?.forEach((a) => q.append("account_id", a));
  f.category_id?.forEach((c) => q.append("category_id", c));
  if (f.start) q.set("start", f.start);
  if (f.end) q.set("end", f.end);
  if (f.review_status) q.set("review_status", f.review_status);
  if (f.search) q.set("search", f.search);
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

export function useUpdateTransaction() {
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (v: { id: string; body: Partial<Transaction> & { tag_ids?: string[] } }) =>
      api.patch<Transaction>(`/transactions/${v.id}`, v.body),
    onSuccess: invalidate,
  });
}
