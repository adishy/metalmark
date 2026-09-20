// The transfer vertical's own API surface: propose a counterpart leg, link it,
// unlink it, and read a linked pair back.
//
// Kept out of api/hooks.ts (shared, and pinned by other workstreams) while
// following its conventions — including reusing the shared invalidation list, so
// a link or an unlink refreshes the reports exactly like any other ledger write.
// That matters more here than elsewhere: linking changes what cash-flow and
// spending contain, not just how one row looks.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import type { Money, Transaction, UUID } from "@/api/types";

/** One row that could be the other leg, priced before the user commits. */
export interface TransferCandidate {
  transaction: Transaction;
  /** Whole days between the legs; the window is symmetric, so never negative. */
  days_apart: number;
  /**
   * What the pair's `fx_cost_base` would become: the real FX spread for a
   * cross-currency transfer, null when the legs cancel exactly (or when a rate
   * is missing). Shown *before* linking — the whole point of ADR-0018.
   */
  fx_cost_base: Money | null;
  /** False is not a refusal: explicit linking is the documented override. */
  within_tolerance: boolean;
}

export interface TransferCandidates {
  items: TransferCandidate[];
}

export interface TransferLink {
  from_txn_id: UUID;
  to_txn_id: UUID;
}

export interface Transfer {
  transfer_group_id: UUID;
  matched_by: string;
  fx_cost_base: Money | null;
  txn_ids: UUID[];
}

export interface TransferDetail {
  transfer_group_id: UUID;
  matched_by: string;
  fx_cost_base: Money | null;
  /** Ordered by date then id; pick the side you want by the amount's sign. */
  legs: Transaction[];
}

/** Candidate legs for one transaction. Disabled until a transaction is named. */
export function useTransferCandidates(txnId: string | null, days = 5) {
  return useQuery({
    queryKey: ["transfer-candidates", txnId, days],
    queryFn: () =>
      api.get<TransferCandidates>(
        `/transactions/transfer-candidates?txn_id=${encodeURIComponent(txnId!)}&days=${days}`,
      ),
    enabled: !!txnId,
  });
}

/**
 * A linked pair, read whole. A leg carries only its group id, so this is how the
 * detail sheet finds the *other* leg — and the residual the pair cost.
 */
export function useTransfer(groupId: string | null) {
  return useQuery({
    queryKey: ["transfer", groupId],
    queryFn: () => api.get<TransferDetail>(`/transactions/transfers/${groupId}`),
    enabled: !!groupId,
  });
}

/** Both a link and an unlink change which rows are candidates for everything. */
function useInvalidateTransfers() {
  const invalidate = useInvalidateLedger();
  const qc = useQueryClient();
  return () => {
    invalidate();
    qc.invalidateQueries({ queryKey: ["transfer"] });
    qc.invalidateQueries({ queryKey: ["transfer-candidates"] });
  };
}

export function useLinkTransfer() {
  const invalidate = useInvalidateTransfers();
  return useMutation({
    mutationFn: (body: TransferLink) => api.post<Transfer>("/transactions/transfers", body),
    onSuccess: invalidate,
  });
}

export function useUnlinkTransfer() {
  const invalidate = useInvalidateTransfers();
  return useMutation({
    mutationFn: (groupId: string) => api.del<void>(`/transactions/transfers/${groupId}`),
    onSuccess: invalidate,
  });
}
