// Owner income profile and paystubs (ADR-0052). Settings → Owners only for
// now — not read by Insights yet. Kept out of api/hooks.ts for the same
// reason api/sync.ts is: a self-contained vertical, following hooks.ts's
// conventions rather than growing its file.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  OwnerIncomeProfile,
  OwnerIncomeProfileUpdate,
  Paystub,
  PaystubCreate,
  PaystubPatch,
  UUID,
} from "@/api/types";

const profileKey = (ownerId: UUID) => ["income-profile", ownerId] as const;
const paystubsKey = (ownerId: UUID) => ["paystubs", ownerId] as const;

export function useIncomeProfile(ownerId: UUID | null) {
  return useQuery({
    queryKey: profileKey(ownerId ?? "_"),
    queryFn: () => api.get<OwnerIncomeProfile>(`/owners/${ownerId}/income-profile`),
    enabled: ownerId != null,
  });
}

export function useUpdateIncomeProfile(ownerId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OwnerIncomeProfileUpdate) =>
      api.put<OwnerIncomeProfile>(`/owners/${ownerId}/income-profile`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: profileKey(ownerId) }),
  });
}

export function usePaystubs(ownerId: UUID | null) {
  return useQuery({
    queryKey: paystubsKey(ownerId ?? "_"),
    queryFn: () => api.get<Paystub[]>(`/owners/${ownerId}/paystubs`),
    enabled: ownerId != null,
  });
}

/** A paystub's own summary (YTD, effective rate) is read off the profile, not
 *  the paystub list — so every write here also invalidates it. */
export function useCreatePaystub(ownerId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PaystubCreate) => api.post<Paystub>(`/owners/${ownerId}/paystubs`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: paystubsKey(ownerId) });
      qc.invalidateQueries({ queryKey: profileKey(ownerId) });
    },
  });
}

export function useUpdatePaystub(ownerId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: UUID; body: PaystubPatch }) =>
      api.patch<Paystub>(`/owners/${ownerId}/paystubs/${v.id}`, v.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: paystubsKey(ownerId) });
      qc.invalidateQueries({ queryKey: profileKey(ownerId) });
    },
  });
}

export function useDeletePaystub(ownerId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.del<void>(`/owners/${ownerId}/paystubs/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: paystubsKey(ownerId) });
      qc.invalidateQueries({ queryKey: profileKey(ownerId) });
    },
  });
}
