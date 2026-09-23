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

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useInvalidateLedger } from "@/api/hooks";
import * as notify from "@/lib/notify";
import type {
  Checks,
  Connection,
  ConnectionClaim,
  ConnectionDefaults,
  ConnectionUpdate,
  SyncJob,
  SyncNotice,
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
const NOTICES = ["sync-notices"] as const;

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

/** How often the notice feed is re-read (ADR-0037).
 *
 *  Slower than anything else here, and by a wide margin, because of what it
 *  carries: a bank connection that broke is not a race, and the panel a few
 *  hundred pixels away already polls the same run history at 15 s. This is the
 *  feed that has to keep running on *every* page, which is the other half of the
 *  reason it is a minute — a poll on every route of the app is a cost the
 *  five-second one could not justify. */
const NOTICE_POLL_MS = 60_000;

/** The notice feed, and the thing that shows them.
 *
 *  Mounted once, in `AppShell`, because a notification is not a page's business:
 *  the tab can be anywhere in the app when a connection breaks. It is
 *  deliberately *not* gated on the user being on Admin — that would make the
 *  feature work only for someone already looking at the answer.
 *
 *  The cursor is read inside `queryFn` rather than folded into the query key.
 *  In the key it would be a new query after every delivery, so the poll would
 *  restart and remount each time it succeeded; read at call time, one query
 *  polls for the life of the tab.
 *
 *  Enabled when the *browser* can do this at all, not when permission has been
 *  granted: `deliver` declines to advance the cursor without permission, so a
 *  denied browser re-reads the same window harmlessly — and starts showing
 *  notices the moment permission is granted, without a reload. */
export function useSyncNotices() {
  const query = useQuery({
    queryKey: NOTICES,
    queryFn: () => {
      const since = notify.readCursor();
      const path = since ? `/connections/notifications?since=${since}` : "/connections/notifications";
      return api.get<SyncNotice[]>(path);
    },
    enabled: notify.support() !== "unsupported",
    refetchInterval: NOTICE_POLL_MS,
    // A notice is worth exactly one showing, so a refetch on focus would only
    // race the interval for the same rows.
    refetchOnWindowFocus: false,
  });

  const notices = query.data;
  useEffect(() => {
    if (notices && notices.length > 0) void notify.deliver(notices);
  }, [notices]);

  return query;
}

/** Re-read the feed now, rather than at the next tick.
 *
 *  For the moment permission is granted: the poll has been running all along and
 *  declining to show anything (the cursor does not advance without permission),
 *  so without this the notices that arrived while it was denied wait out the
 *  rest of the minute before appearing. */
export function useRefreshNotices() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: NOTICES });
}

/** The cadence bounds, from the CHECK constraint's own constants — the slider
 *  never offers an interval the database would refuse (see ConnectionDefaults). */
/** The data checks, run live on each read (ADR-0047). Not polled: the answer
 *  changes when data does, and the page is re-read on focus anyway. */
export function useChecks() {
  return useQuery({
    queryKey: ["checks"],
    queryFn: () => api.get<Checks>("/checks"),
  });
}

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
