// The sync control panel — the "operate it from the UI" half of the project's
// standing ask, and the Jellyfin split made concrete: Settings holds the
// *credential lifecycle* (paste a token, pause, disconnect), and this page holds
// *operations* (what is running, what ran, stop it, retune it).
//
// Deliberately not a nav destination. `docs/DESIGN.md` §4.13 caps the tab bar at
// five items, and this page is reached from Settings → Connections, so it
// registers in `AppShell`'s `EXTRA_TITLES` instead. That is a constraint on
// where it is linked from, not on how much room it gets.
//
// Everything here is owner-only, enforced by the server (`require_owner` on
// every connection route); the page is reachable only by an admin in the client
// too, because a connection names the household's banks and its failure messages
// name them as well.

import { useState } from "react";

import {
  useCancelJob,
  useConnections,
  useConnectionDefaults,
  useSyncJobs,
  useSyncRunDetail,
  useSyncRuns,
  useTriggerSync,
  useUpdateConnection,
} from "@/api/sync";
import type { RunStatus, SyncJob, SyncRun } from "@/api/types";
import ConnectionBadge from "@/components/ConnectionBadge";
import { Instant, Time } from "@/components/datetime";
import { Button, Select, Spinner } from "@/components/form";
import { formatDuration } from "@/lib/format";

// ---- presentational bits ---------------------------------------------------

type Tone = "ok" | "warn" | "bad" | "busy" | "idle";

/*
 * A badge is a text span with padding AND a radius — that pair is the documented
 * exception to the "no bg-* on an inline text element" design rule (§8 rule 6),
 * because the padding is what makes it a *chip* rather than a coloured word.
 */
const TONES: Record<Tone, string> = {
  ok: "bg-positive/15 text-positive",
  warn: "bg-warning/20 text-warning",
  bad: "bg-negative/20 text-negative",
  busy: "bg-accent/15 text-accent",
  idle: "bg-surface-inset text-fg-muted",
};

function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ${TONES[tone]}`}>
      {children}
    </span>
  );
}

/**
 * A sync run's start or finish, as a day and a clock time.
 *
 * Two `<time>` elements rather than one string, because they are two different
 * precisions and the run log needs both — "which day did this happen" and "how
 * long did it take" are answered by different parts of the same timestamp.
 * Either element carries the full ISO on hover, so nothing is lost to the
 * split. Local, because a person asking when a sync ran means their own clock.
 */
function Stamp({ value }: { value: string }) {
  return (
    <>
      <Instant value={value} style="long" /> <Time value={value} seconds={false} />
    </>
  );
}

function Card({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-card bg-surface-raised p-4">
      <div>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        {note && <p className="mt-1 text-xs text-fg-muted">{note}</p>}
      </div>
      {children}
    </section>
  );
}

const RUN_TONES: Record<RunStatus, Tone> = {
  running: "busy",
  ok: "ok",
  partial: "warn",
  error: "bad",
  cancelled: "idle",
};

/** The counters worth a line, with the units spelled out. A run that moved
 *  nothing is a *successful* run, so the "0 new" case has to read as calm
 *  rather than as an absence of information. */
function runSummary(run: SyncRun): string {
  const parts = [
    `${run.txns_inserted} new`,
    `${run.txns_updated} updated`,
    `${run.txns_reconciled} reconciled`,
  ];
  if (run.txns_rekeyed > 0) parts.push(`${run.txns_rekeyed} re-keyed`);
  if (run.accounts_remapped > 0) parts.push(`${run.accounts_remapped} remapped`);
  if (run.pendings_expired > 0) parts.push(`${run.pendings_expired} expired`);
  if (run.transfers_matched > 0) parts.push(`${run.transfers_matched} transfers`);
  return parts.join(" · ");
}

// ---- the page -------------------------------------------------------------

export default function Admin() {
  // Polled, unlike in Settings: the numbers in this card are written by the
  // *worker*, so nothing the client does invalidates them.
  const connections = useConnections(true);
  const defaults = useConnectionDefaults();
  const jobs = useSyncJobs(true);
  const trigger = useTriggerSync();
  const cancel = useCancelJob();
  const update = useUpdateConnection();

  const [runFilter, setRunFilter] = useState<string>("");
  const runs = useSyncRuns(runFilter || null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);

  const connectionList = connections.data ?? [];
  const byId = new Map(connectionList.map((c) => [c.id, c]));

  function label(connectionId: string | null): string {
    if (connectionId === null) return "unknown";
    return byId.get(connectionId)?.org_name ?? "a removed connection";
  }

  return (
    <div className="space-y-4">
      <header>
        {/* "Admin" is the eyebrow rather than the heading on purpose: the page
            is one screen of sync operations, and calling *it* "Admin" would
            promise a broader console. The word is here because it is the one a
            reader scans for — this page shipped with no occurrence of it
            anywhere, which is a large part of why it could not be found. */}
        <p className="text-xs font-semibold tracking-wide text-fg-muted uppercase">Admin</p>
        <h1 className="text-lg font-medium text-fg">Sync activity</h1>
        <p className="mt-1 text-sm text-fg-muted">
          What is running, what ran, and what to do about it. Adding or removing a bank's
          credentials is in Settings → Connections; operating one is here.
        </p>
      </header>

      {/* A failed action is reported once, here, rather than per card: the
          buttons that can fail are spread across three cards and the message is
          about the action, not the card. */}
      {actionError && (
        <p className="text-sm text-negative" role="alert" data-testid="admin-error">
          {actionError}
        </p>
      )}

      <Card
        title="Connections"
        note={
          defaults.data
            ? `Cadence is per connection, between ${formatInterval(defaults.data.sync_interval_min_minutes)} and ${formatInterval(defaults.data.sync_interval_max_minutes)}.`
            : undefined
        }
      >
        {connections.isPending && <Spinner />}
        {connections.isError && (
          <p className="text-sm text-negative" role="alert">
            {(connections.error as Error).message}
          </p>
        )}
        {connections.isSuccess && connectionList.length === 0 && (
          <p className="text-sm text-fg-muted" data-testid="no-connections">
            No connections yet. Connect one from Settings → Connections.
          </p>
        )}
        {connectionList.length > 0 && (
          <ul
            className="divide-y divide-border rounded-control bg-surface-inset/40"
            data-testid="connection-list"
          >
            {connectionList.map((c) => (
              <li key={c.id} className="space-y-2 p-3" data-testid={`conn-row-${c.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-fg">
                    {c.org_name ?? "Unnamed connection"}
                  </span>
                  <ConnectionBadge connection={c} />
                </div>

                <p className="text-xs text-fg-muted">
                  {c.last_synced_at ? (
                    <>
                      Last synced <Instant value={c.last_synced_at} style="relative" />
                    </>
                  ) : (
                    "Never synced"
                  )}
                  {c.next_sync_at && (
                    <>
                      {" · next "}
                      <Instant value={c.next_sync_at} style="relative" />
                    </>
                  )}
                  {` · every ${formatInterval(c.sync_interval_minutes)}`}
                </p>

                {c.last_error && (
                  <p className="text-xs text-negative" data-testid={`conn-error-${c.id}`}>
                    {c.last_error}
                  </p>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={() =>
                      trigger.mutate(c.id, {
                        // A connection that needs reconnecting may have been
                        // re-authed at the bridge a moment ago, so this is
                        // allowed even when the status is bad; a *paused* one is
                        // refused by the server, which is the one case where the
                        // user's own instruction outranks the button.
                        onError: (e) => setActionError((e as Error).message),
                        onSuccess: () => setActionError(null),
                      })
                    }
                    disabled={trigger.isPending || !c.is_enabled}
                    data-testid={`sync-now-${c.id}`}
                  >
                    Sync now
                  </Button>

                  <Button
                    variant="secondary"
                    onClick={() =>
                      update.mutate(
                        { id: c.id, body: { is_enabled: !c.is_enabled } },
                        {
                          onError: (e) => setActionError((e as Error).message),
                          onSuccess: () => setActionError(null),
                        },
                      )
                    }
                    disabled={update.isPending}
                    data-testid={`pause-${c.id}`}
                  >
                    {c.is_enabled ? "Pause" : "Resume"}
                  </Button>

                  <label className="flex items-center gap-2 text-xs text-fg-muted">
                    Every
                    <Select
                      value={String(c.sync_interval_minutes)}
                      onChange={(e) =>
                        update.mutate(
                          { id: c.id, body: { sync_interval_minutes: Number(e.target.value) } },
                          { onError: (err) => setActionError((err as Error).message) },
                        )
                      }
                      disabled={update.isPending || !defaults.data}
                      aria-label={`Sync interval for ${c.org_name ?? "this connection"}`}
                      className="w-auto"
                      data-testid={`interval-${c.id}`}
                    >
                      {intervalOptions(c.sync_interval_minutes, defaults.data).map((m) => (
                        <option key={m} value={m}>
                          {formatInterval(m)}
                        </option>
                      ))}
                    </Select>
                  </label>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Running now"
        note="Jobs in flight. A job whose heartbeat has stopped advancing is one the worker has stopped reporting on — the server reclaims it and runs it again."
      >
        {jobs.isPending && <Spinner />}
        {jobs.isError && (
          <p className="text-sm text-negative" role="alert">
            {(jobs.error as Error).message}
          </p>
        )}
        {jobs.isSuccess && (jobs.data?.length ?? 0) === 0 && (
          <p className="text-sm text-fg-muted" data-testid="no-jobs">
            Nothing queued or running.
          </p>
        )}
        {(jobs.data?.length ?? 0) > 0 && (
          <div
            className="overflow-x-auto"
            role="region"
            aria-label="Jobs in flight"
            tabIndex={0}
          >
            <table className="w-full text-sm">
              <caption className="sr-only">
                Sync jobs that are queued or running, with their liveness and a control to cancel.
              </caption>
              <thead>
                <tr className="text-left text-xs text-fg-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">Connection</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Trigger</th>
                  <th scope="col" className="py-2 pr-3 font-medium">State</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Started</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Heartbeat</th>
                  <th scope="col" className="py-2 font-medium">Attempt</th>
                  <th scope="col" className="py-2 font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.data?.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    label={label(job.connection_id)}
                    onCancel={() =>
                      cancel.mutate(job.id, {
                        // The 409 — "it had already finished" — is shown as-is.
                        // It is a real answer to the click and the operator is
                        // owed it; replacing it with "cancelled" would be a lie
                        // about what happened to their data.
                        onError: (e) => setActionError((e as Error).message),
                        onSuccess: () => setActionError(null),
                      })
                    }
                    busy={cancel.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent runs">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-fg-muted" htmlFor="run-filter">
            Show
          </label>
          <Select
            id="run-filter"
            value={runFilter}
            onChange={(e) => setRunFilter(e.target.value)}
            className="w-auto"
            data-testid="run-filter"
          >
            <option value="">All connections</option>
            {connectionList.map((c) => (
              <option key={c.id} value={c.id}>
                {c.org_name ?? c.id}
              </option>
            ))}
          </Select>
        </div>

        {runs.isPending && <Spinner />}
        {runs.isError && (
          <p className="text-sm text-negative" role="alert">
            {(runs.error as Error).message}
          </p>
        )}
        {runs.isSuccess && (runs.data?.length ?? 0) === 0 && (
          <p className="text-sm text-fg-muted" data-testid="no-runs">
            No runs yet.
          </p>
        )}
        {(runs.data?.length ?? 0) > 0 && (
          <ul className="divide-y divide-border rounded-control bg-surface-inset/40" data-testid="run-list">
            {runs.data?.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                expanded={expanded === run.id}
                onToggle={() => setExpanded(expanded === run.id ? null : run.id)}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// ---- rows -----------------------------------------------------------------

function JobRow({
  job,
  label,
  onCancel,
  busy,
}: {
  job: SyncJob;
  label: string;
  onCancel: () => void;
  busy: boolean;
}) {
  const started = job.claimed_at ?? job.created_at;
  return (
    <tr data-testid={`job-row-${job.id}`}>
      <td className="py-2 pr-3">{label}</td>
      <td className="py-2 pr-3 text-fg-muted">{job.trigger}</td>
      <td className="py-2 pr-3">
        <Badge tone={job.status === "running" ? "busy" : "idle"}>{job.status}</Badge>
      </td>
      <td className="py-2 pr-3 text-fg-muted">
        <Instant value={started} style="relative" />
      </td>
      <td className="py-2 pr-3 text-fg-muted">
        {job.heartbeat_at ? <Instant value={job.heartbeat_at} style="relative" /> : "—"}
      </td>
      <td className="py-2 pr-3 text-fg-muted">
        {job.attempts > 1 ? `${job.attempts} (retried)` : String(job.attempts)}
      </td>
      <td className="py-2 text-right">
        <Button
          variant="secondary"
          onClick={onCancel}
          disabled={busy}
          data-testid={`cancel-${job.id}`}
        >
          Cancel
        </Button>
      </td>
    </tr>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
}: {
  run: SyncRun;
  expanded: boolean;
  onToggle: () => void;
}) {
  const detail = useSyncRunDetail(expanded ? run.id : null);
  const where = run.connection_label ?? "a removed connection";

  return (
    <li className="p-3" data-testid={`run-row-${run.id}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge tone={RUN_TONES[run.status]}>{run.status}</Badge>
        <span className="text-sm text-fg">{where}</span>
        <span className="text-xs text-fg-muted">
          <Instant value={run.started_at} style="relative" />
          {run.duration_ms !== null && ` · took ${formatDuration(run.duration_ms)}`}
          {run.http_status !== null && ` · HTTP ${run.http_status}`}
        </span>
        <Button
          variant="ghost"
          onClick={onToggle}
          aria-expanded={expanded}
          className="ml-auto"
          data-testid={`run-toggle-${run.id}`}
        >
          {expanded ? "Hide log" : "Show log"}
        </Button>
      </div>

      <p className="mt-1 text-xs text-fg-muted">{runSummary(run)}</p>

      {run.error && (
        <p className="mt-1 text-xs text-negative" data-testid={`run-error-${run.id}`}>
          {run.error}
        </p>
      )}

      {expanded && (
        <div className="mt-3 space-y-3 border-l-2 border-border pl-3" data-testid={`run-detail-${run.id}`}>
          {detail.isPending && <Spinner />}
          {detail.isError && (
            <p className="text-sm text-negative" role="alert">
              {(detail.error as Error).message}
            </p>
          )}
          {detail.isSuccess && (
            <>
              <CounterGrid run={detail.data.run} />
              {detail.data.events.length === 0 ? (
                <p className="text-xs text-fg-muted">This run logged nothing.</p>
              ) : (
                <ol className="space-y-1" data-testid={`run-events-${run.id}`}>
                  {/* Ordered by `seq`, not `ts`: every event written inside one
                      ingest transaction shares the same transaction timestamp. */}
                  {detail.data.events.map((event) => (
                    <li key={event.id} className="flex flex-wrap gap-x-2 text-xs">
                      <span className="text-fg-muted tabular-nums">
                        {/* Fixed 24-hour, so this column lines up and two events
                            in the same second stay distinguishable — which is
                            why seconds are on here and off in CounterGrid. */}
                        <Time value={event.ts} />
                      </span>
                      <span className={event.level === "error" ? "text-negative" : "text-fg"}>
                        {event.event}
                      </span>
                      {Object.keys(event.detail).length > 0 && (
                        <span className="text-fg-muted">{formatDetail(event.detail)}</span>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** Every counter a run records. Shown in full on expansion rather than in the
 *  row: the row answers "did it work", and this answers "what exactly did it
 *  touch", which is the question someone asks only when something looks wrong. */
function CounterGrid({ run }: { run: SyncRun }) {
  const cells: [string, React.ReactNode][] = [
    ["Accounts seen", String(run.accounts_seen)],
    ["Accounts created", String(run.accounts_created)],
    ["Accounts remapped", String(run.accounts_remapped)],
    ["Transactions inserted", String(run.txns_inserted)],
    ["Transactions updated", String(run.txns_updated)],
    ["Transactions re-keyed", String(run.txns_rekeyed)],
    ["Pending reconciled", String(run.txns_reconciled)],
    ["Pending expired", String(run.pendings_expired)],
    ["Transfers matched", String(run.transfers_matched)],
    ["Rules applied", String(run.rules_applied)],
    ["Fetch time", run.http_ms === null ? "—" : formatDuration(run.http_ms)],
    ["Fetched", run.bytes_fetched === null ? "—" : formatBytes(run.bytes_fetched)],
    // The day and the clock time are two different precisions, and a sync run
    // needs both: "which day did this happen" and "how long did it take" are
    // answered by different parts of the same timestamp. Each element still
    // carries the full ISO on hover.
    ["Started", <Stamp value={run.started_at} />],
    ["Finished", run.finished_at ? <Stamp value={run.finished_at} /> : "—"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
      {cells.map(([term, value]) => (
        <div key={term} className="flex justify-between gap-2">
          <dt className="text-fg-muted">{term}</dt>
          <dd className="text-fg tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---- small formatting helpers, local to the panel -------------------------

const PRESET_MINUTES = [120, 360, 720, 1440, 4320, 10080];

/** The presets, plus the connection's own value if it is not one of them —
 *  otherwise a connection set to something unusual would silently rewrite its
 *  cadence the first time this control rendered. */
function intervalOptions(current: number, defaults?: { sync_interval_min_minutes: number; sync_interval_max_minutes: number }): number[] {
  const lo = defaults?.sync_interval_min_minutes ?? 0;
  const hi = defaults?.sync_interval_max_minutes ?? Number.MAX_SAFE_INTEGER;
  const within = PRESET_MINUTES.filter((m) => m >= lo && m <= hi);
  if (!within.includes(current) && current >= lo && current <= hi) within.push(current);
  return within.sort((a, b) => a - b);
}

function formatInterval(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours % 1 === 0 ? hours : hours.toFixed(1)} h`;
  const days = hours / 24;
  return `${days % 1 === 0 ? days : days.toFixed(1)} d`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} kB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** The event's detail object as one line. It is sanitized at write time, so the
 *  only thing standing between it and the DOM is making it readable. */
function formatDetail(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
}
