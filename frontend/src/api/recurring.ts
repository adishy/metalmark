// Recurring series (ADR-0053): the Insights → Recurring tab's own calls.
//
// Kept out of api/hooks.ts for the same reason api/income.ts and api/sync.ts
// are — a self-contained vertical follows that file's conventions rather than
// growing it.
//
// One thing here is not a plain CRUD convention: a *suggestion* is derived from
// the ledger on every read, and a series is what suppresses it. So every write
// invalidates the suggestions as well as the list — accepting one has to make
// it disappear, and pausing a series must not make it come back.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  RecurringCreate,
  RecurringFilter,
  RecurringList,
  RecurringSeries,
  RecurringSuggestion,
  RecurringUpdate,
  UUID,
} from "@/api/types";

export const RECURRING_KEY = ["recurring"] as const;
export const SUGGESTIONS_KEY = ["recurring-suggestions"] as const;

function query(f: RecurringFilter = {}): string {
  const q = new URLSearchParams();
  if (f.account_id) q.set("account_id", f.account_id);
  if (f.category_id) q.set("category_id", f.category_id);
  // `is_active=false` is a filter ("paused only"), so the value is not optional
  // here — it is only absent when nothing is filtered, which is `undefined`.
  if (f.is_active != null) q.set("is_active", String(f.is_active));
  if (f.direction) q.set("direction", f.direction);
  if (f.q) q.set("q", f.q);
  const s = q.toString();
  return s ? `/recurring?${s}` : "/recurring";
}

export function useRecurring(filter: RecurringFilter = {}) {
  return useQuery({
    queryKey: [...RECURRING_KEY, filter] as const,
    queryFn: () => api.get<RecurringList>(query(filter)),
  });
}

export function useRecurringSuggestions() {
  return useQuery({
    queryKey: SUGGESTIONS_KEY,
    queryFn: () => api.get<RecurringSuggestion[]>("/recurring/suggestions"),
  });
}

/** Both halves of the tab are invalidated by every write: the list because it
 *  changed, the suggestions because a series is exactly what stops one being
 *  offered. */
function useInvalidateRecurring() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: RECURRING_KEY });
    qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY });
  };
}

export function useCreateRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (body: RecurringCreate) => api.post<RecurringSeries>("/recurring", body),
    onSuccess: invalidate,
  });
}

export function useUpdateRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (v: { id: UUID; body: RecurringUpdate }) =>
      api.patch<RecurringSeries>(`/recurring/${v.id}`, v.body),
    onSuccess: invalidate,
  });
}

export function useDeleteRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (id: UUID) => api.del<void>(`/recurring/${id}`),
    onSuccess: invalidate,
  });
}
