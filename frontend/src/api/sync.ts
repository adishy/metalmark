// The sync vertical's API surface: connections, the job queue, and the run log.
//
// Kept out of api/hooks.ts (shared, and pinned by other workstreams) while
// following its conventions. The types already live in api/types.ts — they were
// written with the schema — so this module is the hooks and nothing else.
//
// **This module polls, deliberately, and it is the only one that does.** Every
// other query in the app is invalidated by the mutation that changes it, which is
// right for a ledger: nothing moves unless the user moved it. A sync is the
// opposite — it is started by the server, finishes on the server, and the panel
// that is open while it happens has no way to learn about it otherwise. Polling
// is the honest mechanism. React Query stops the interval when the window loses
// focus, so a panel left open in a background tab is quiet.
//
// The intervals differ by what they are watching: a job in flight can change
// state in seconds and is the thing Cancel acts on, so 5 s; a run log is
// append-only history, so 15 s is plenty.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import type {
  Connection,
  ConnectionClaim,
  ConnectionDefaults,
  ConnectionUpdate,
  SyncJob,
  SyncRun,
  SyncRunDetail,
  UUID,
} from "@/api/types";

/** How often the live queue is re-read. See the module header. */
const JOB_POLL_MS = 5_000;

/** How often the run history is re-read. */
const RUN_POLL_MS = 15_000;

/** How often the *panel's* connection summary is re-read.
 *
 *  A sync changes the connection row itself — `last_synced_at`, `next_sync_at`,
 *  and on the first run `org_name`, which is the institution's name and the only
 *  place it comes from. None of that is caused by a mutation the client made, so
 *  nothing invalidates it: a panel left open would say "Never synced" forever
 *  while the run list underneath it filled up. Same interval as the run history,
 *  because they are two halves of one answer. */
const CONNECTION_POLL_MS = 15_000;

/*
 * One key per resource, with the parameters that change the answer folded in.
 * `sync-runs` keys on the connection filter rather than filtering client-side,
 * so switching the filter is a different query and does not briefly render the
 * previous connection's runs.
 */
const CONNECTIONS = ["connections"] as const;
const DEFAULTS = ["connection-defaults"] as const;
const JOBS = ["sync-jobs"] as const;
const RUNS = ["sync-runs"] as const;

/** The household's connections.
 *
 *  `poll` is for the control panel, where the connection summary is a live reading
 *  rather than a form's initial state — see `CONNECTION_POLL_MS`. Settings asks for
 *  it once, because there the list changes only in response to something the user
 *  just did, and every one of those mutations invalidates it. */
export function useConnections(poll = false) {
  return useQuery({
    queryKey: CONNECTIONS,
    queryFn: () => api.get<Connection[]>("/connections"),
    refetchInterval: poll ? CONNECTION_POLL_MS : false,
  });
}

/** The cadence bounds, from the CHECK constraint's own constants — the slider
 *  never offers an interval the database would refuse (see ConnectionDefaults). */
export function useConnectionDefaults() {
  return useQuery({
    queryKey: DEFAULTS,
    queryFn: () => api.get<ConnectionDefaults>("/connections/defaults"),
    // Schema constants. They change when the server is redeployed, not while a
    // page is open, and a stale copy of them is harmless.
    staleTime: Infinity,
  });
}

/** Exchange a setup token for a connection. The token is never echoed back and
 *  never stored client-side; on failure the server's message is what the user
 *  needs to see, so it is surfaced rather than replaced. */
export function useClaimConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConnectionClaim) => api.post<Connection>("/connections/claim", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS }),
  });
}

/** Pause/resume and the cadence. Sends only what changed — neither field has a
 *  "clear it" meaning, so an absent one is not a request to unset. */
export function useUpdateConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: UUID; body: ConnectionUpdate }) =>
      api.patch<Connection>(`/connections/${v.id}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: CONNECTIONS }),
  });
}

export function useDeleteConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.del<void>(`/connections/${id}`),
    // The connection list changes, and the run history's `connection_id` goes
    // null on the same rows — the runs keep their `connection_label`, so the
    // history stays readable, but it is now a different answer.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CONNECTIONS });
      qc.invalidateQueries({ queryKey: RUNS });
    },
  });
}

/** Queue a run. Returns the job, not the outcome — the work happens in the
 *  worker, and the queue table is where it becomes visible. */
export function useTriggerSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: UUID) => api.post<SyncJob>(`/connections/${connectionId}/sync`),
    onSuccess: () => qc.invalidateQueries({ queryKey: JOBS }),
  });
}

/** The queue. `activeOnly` is what the live table asks for; the unfiltered list
 *  includes the terminal jobs and is what "was it cancelled or did it die?"
 *  needs, so it does not poll. */
export function useSyncJobs(activeOnly = false) {
  return useQuery({
    queryKey: [...JOBS, { activeOnly }],
    queryFn: () =>
      api.get<SyncJob[]>(`/connections/jobs${activeOnly ? "?active_only=true" : ""}`),
    refetchInterval: activeOnly ? JOB_POLL_MS : false,
  });
}

export function useSyncRuns(connectionId?: UUID | null, limit?: number) {
  const params = new URLSearchParams();
  if (connectionId) params.set("connection_id", connectionId);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return useQuery({
    queryKey: [...RUNS, { connectionId: connectionId ?? null, limit: limit ?? null }],
    queryFn: () => api.get<SyncRun[]>(`/connections/runs${qs ? `?${qs}` : ""}`),
    refetchInterval: RUN_POLL_MS,
  });
}

/** One run and its log together, fetched only once a row is expanded. */
export function useSyncRunDetail(runId: UUID | null) {
  return useQuery({
    queryKey: ["sync-run", runId],
    queryFn: () => api.get<SyncRunDetail>(`/connections/runs/${runId}`),
    enabled: runId !== null,
    // A run's log only grows while it is running, and the detail is opened by
    // hand on a row that is usually already finished.
    staleTime: 30_000,
  });
}

/** Cancel a queued or running job.
 *
 *  A 409 means it had already finished by the time the click landed — a real
 *  outcome, not a failure, and the server says so in its `detail`. The panel
 *  shows it verbatim rather than pretending the cancel worked: ingest is
 *  idempotent, so the worst case is a cancel that arrived after the commit.
 */
export function useCancelJob() {
  const qc = useQueryClient();
  const invalidate = useInvalidateLedger();
  return useMutation({
    mutationFn: (jobId: UUID) => api.post<SyncJob>(`/connections/jobs/${jobId}/cancel`),
    // `onSettled`, not `onSuccess`: the 409 is the interesting case, and the job
    // it refused to cancel has finished — which is exactly when the ledger and
    // the run history have changed and the panel's copy is stale. A cancel that
    // arrives after the commit did not undo the ingest.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: JOBS });
      qc.invalidateQueries({ queryKey: RUNS });
      invalidate();
    },
  });
}
