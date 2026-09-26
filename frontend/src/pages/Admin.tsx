// The sync control panel — the "operate it from the UI" half of the project's
// standing ask, and the Jellyfin split made concrete: Settings holds the
// *credential lifecycle* (paste a token, pause, disconnect), and this page holds
// *operations* (what is running, what ran, stop it, retune it).
//
// A nav destination for administrators — the desktop nav has room for a sixth
// item even though the phone tab bar's five-item cap (§4.13) keeps it off the
// bottom bar, where an admin reaches it from Settings. It shipped reachable only
// from a muted aside in Settings, which turned out to mean unreachable: the page
// was complete and nobody could find it.
//
// Everything here is owner-only, enforced by the server (`require_owner` on
// every connection route); the page is reachable only by an admin in the client
// too, because a connection names the household's banks and its failure messages
// name them as well.

import { useState } from "react";

import { useAgentTokens, useCreateAgentToken, useRevokeAgentToken } from "@/api/agent";
import { useAutoCategorizeAll } from "@/api/hooks";
import { connectionName, isStalled } from "@/lib/bankFreshness";
import {
  useCancelJob,
  useChecks,
  useConnections,
  useConnectionDefaults,
  useSyncJobs,
  useSyncRunDetail,
  useSyncRuns,
  useTriggerSync,
  useUpdateConnection,
} from "@/api/sync";
import type {
  AgentScope,
  AgentTokenCreated,
  AgentTokenStatus,
  Check,
  CheckStatus,
  RunStatus,
  SyncJob,
  SyncRun,
} from "@/api/types";
import ConnectionBadge from "@/components/ConnectionBadge";
import { Instant, Time } from "@/components/datetime";
import {
  Button,
  Checkbox,
  Field,
  Input,
  Select,
  Spinner,
  useFieldId,
} from "@/components/form";
import NoticePermission from "@/components/NoticePermission";
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
  warn: "bg-warning/20 text-warning-ink",
  bad: "bg-negative/20 text-negative-ink",
  busy: "bg-accent/15 text-accent-ink",
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
    const c = byId.get(connectionId);
    return c ? connectionName(c, "a removed connection") : "a removed connection";
  }

  return (
    // Admin is tables and counters — §9.1's widest case, so no cap of its own
    // and the shell's `max-w-7xl` is the cap.
    //
    // §9.3's card grid, and the three cards are exactly the three columns: what
    // is configured, what is running, what ran. Read across rather than down,
    // they answer the one question this page exists for — is sync healthy — in
    // a single glance instead of three scroll positions. The header and the
    // error line span all three because neither is a card.
    <div
      className="space-y-4 lg:grid lg:grid-cols-3 lg:items-start lg:gap-6 lg:space-y-0"
      data-testid="admin-page"
    >
      <header className="lg:col-span-3">
        {/* "Admin" is the eyebrow rather than the heading on purpose: the page
            is one screen of sync operations, and calling *it* "Admin" would
            promise a broader console. The word is here because it is the one a
            reader scans for — this page shipped with no occurrence of it
            anywhere, which is a large part of why it could not be found. */}
        <p className="text-sm font-medium text-fg-muted">Admin</p>
        <h1 className="text-xl font-semibold text-fg">Sync activity</h1>
        <p className="mt-1 text-sm text-fg-muted">
          What is running, what ran, and what to do about it. Adding or removing a bank's
          credentials is in Settings → Connections; operating one is here.
        </p>
        <NoticePermission />
      </header>

      {/* A failed action is reported once, here, rather than per card: the
          buttons that can fail are spread across three cards and the message is
          about the action, not the card. */}
      {actionError && (
        <p className="text-sm text-negative lg:col-span-3" role="alert" data-testid="admin-error">
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
                    {connectionName(c)}
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
                {isStalled(c) && c.last_new_data_at && (
                  <p
                    className="rounded-control bg-warning/15 px-2 py-1 text-xs text-warning-ink"
                    role="status"
                    data-testid={`conn-stalled-${c.id}`}
                  >
                    No new transactions since <Instant value={c.last_new_data_at} style="long" /> —
                    the last {c.quiet_syncs} syncs succeeded but the bank sent nothing new. The
                    bank&rsquo;s link at SimpleFIN Bridge may need a refresh or a new sign-in.
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
                      aria-label={`Sync interval for ${connectionName(c, "this connection")}`}
                      inline
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
                {connectionName(c, c.id)}
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

      <AutoCategorize />
      <DataChecks />
      <AgentAccess />
    </div>
  );
}

// ---- auto-categorize ------------------------------------------------------

/**
 * One pass over every transaction by the local categorizer (ADR-0049). It
 * overwrites, including categories set by hand, so it asks first and says so in
 * words — the one thing a person must know before pressing it. Nothing leaves
 * the server: the categorizer has no network.
 */
export function AutoCategorize() {
  const run = useAutoCategorizeAll();
  const [confirming, setConfirming] = useState(false);
  const r = run.data;

  return (
    <Card
      title="Auto-categorize"
      note="Files every transaction into its likely category from its merchant, its amount and how you have filed that merchant before. Runs on this server; nothing is sent anywhere."
    >
      {confirming ? (
        <div
          className="space-y-3 rounded-control border border-negative/40 bg-negative/10 p-3"
          data-testid="auto-categorize-confirm"
        >
          <p className="text-sm text-fg">
            This replaces the category on every transaction it has a guess for, including ones you chose
            yourself. Transactions it has no guess for keep what they have. Split transactions are left alone.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="danger"
              disabled={run.isPending}
              aria-busy={run.isPending}
              onClick={() => run.mutate(undefined, { onSettled: () => setConfirming(false) })}
              data-testid="auto-categorize-run"
            >
              {run.isPending && <Spinner />}
              Overwrite and categorize all
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" onClick={() => setConfirming(true)} data-testid="auto-categorize">
          Auto-categorize all transactions
        </Button>
      )}
      {r && (
        <p className="text-sm text-fg" role="status" data-testid="auto-categorize-result">
          Looked at {r.examined.toLocaleString()} transactions: changed {r.changed.toLocaleString()},{" "}
          {r.left_blank.toLocaleString()} still uncategorized
          {r.transfers_linked > 0 && `, and linked ${r.transfers_linked.toLocaleString()} transfers`}.
        </p>
      )}
      {run.isError && (
        <p className="text-sm text-negative" role="alert">
          {(run.error as Error).message}
        </p>
      )}
    </Card>
  );
}

// ---- agent access ---------------------------------------------------------

const SCOPE_LABELS: Record<AgentScope, { label: string; hint: string }> = {
  "agent:read": {
    label: "Agent API",
    hint: "/api/agent — every screen's data, as structured, anonymized JSON.",
  },
  "debug:read": {
    label: "Debug views",
    hint: "/api/anon_debug — what a page shows, and why a row or a balance is what it is.",
  },
};

const EXPIRY_DAYS = [7, 30, 90, 365];

const TOKEN_TONES: Record<AgentTokenStatus, Tone> = {
  active: "ok",
  expired: "idle",
  revoked: "idle",
};

/**
 * Tokens that let an agent read the household, anonymized and read-only
 * (ADR-0048). The token is shown once, when it is issued: the server keeps only
 * its hash, so "copy it now" is not a suggestion.
 */
export function AgentAccess() {
  const tokens = useAgentTokens();
  const create = useCreateAgentToken();
  const revoke = useRevokeAgentToken();

  const nameId = useFieldId("agent-token-name");
  const expiryId = useFieldId("agent-token-expiry");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<AgentScope[]>(["agent:read", "debug:read"]);
  const [days, setDays] = useState(90);
  const [formError, setFormError] = useState<string | null>(null);
  const [issued, setIssued] = useState<AgentTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  function toggle(scope: AgentScope) {
    setScopes((s) => (s.includes(scope) ? s.filter((x) => x !== scope) : [...s, scope]));
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setFormError("Name the token after the agent that will hold it.");
    if (scopes.length === 0) return setFormError("Choose at least one thing it can read.");
    setFormError(null);
    create.mutate(
      { name: name.trim(), scopes, expires_in_days: days },
      {
        onSuccess: (t) => {
          setIssued(t);
          setCopied(false);
          setName("");
        },
        onError: (err) => setFormError((err as Error).message),
      },
    );
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // A browser that refuses the clipboard (an insecure origin) still shows the
      // token selected in the field, which is the fallback.
      setCopied(false);
    }
  }

  return (
    <Card
      title="Agent access"
      note="Tokens for AI agents: read-only, and names, descriptions, notes and account numbers are replaced on the server before anything leaves."
    >
      <form className="space-y-3" onSubmit={submit} data-testid="agent-token-form">
        <Field label="Name" htmlFor={nameId} required>
          <Input
            id={nameId}
            value={name}
            maxLength={80}
            placeholder="Claude, debugging reports"
            onChange={(e) => setName(e.target.value)}
            data-testid="agent-token-name"
          />
        </Field>
        <fieldset className="space-y-1">
          <legend className="text-xs font-medium text-fg-muted">Can read</legend>
          {(Object.keys(SCOPE_LABELS) as AgentScope[]).map((scope) => (
            <Checkbox
              key={scope}
              label={SCOPE_LABELS[scope].label}
              hint={SCOPE_LABELS[scope].hint}
              checked={scopes.includes(scope)}
              onChange={() => toggle(scope)}
              data-testid={`agent-scope-${scope}`}
            />
          ))}
        </fieldset>
        <Field label="Expires after" htmlFor={expiryId}>
          <Select
            id={expiryId}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            data-testid="agent-token-expiry"
          >
            {EXPIRY_DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 365 ? "1 year" : `${d} days`}
              </option>
            ))}
          </Select>
        </Field>
        {formError && (
          <p className="text-sm text-negative" role="alert" data-testid="agent-token-error">
            {formError}
          </p>
        )}
        <Button type="submit" disabled={create.isPending} aria-busy={create.isPending}>
          {create.isPending && <Spinner />}
          Issue token
        </Button>
      </form>

      {issued && (
        <div className="space-y-2 rounded-control bg-surface-inset p-3" data-testid="agent-token-issued">
          <p className="text-sm text-fg">
            Copy this token now — it is not shown again. Give it to the agent as{" "}
            <code className="text-xs">Authorization: Bearer …</code> and point it at{" "}
            <code className="text-xs">/api/agent</code>, which lists every route it can call.
          </p>
          <div className="flex gap-2">
            <Input
              readOnly
              value={issued.token}
              aria-label="The new agent token"
              onFocus={(e) => e.currentTarget.select()}
              className="font-mono text-xs"
              data-testid="agent-token-value"
            />
            <Button type="button" variant="secondary" onClick={() => copy(issued.token)}>
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <Button type="button" variant="ghost" onClick={() => setIssued(null)}>
            Done
          </Button>
        </div>
      )}

      {tokens.isPending && <Spinner />}
      {tokens.isError && (
        <p className="text-sm text-negative" role="alert">
          {(tokens.error as Error).message}
        </p>
      )}
      {tokens.data && tokens.data.length === 0 && (
        <p className="text-sm text-fg-muted">No tokens yet.</p>
      )}
      {tokens.data && tokens.data.length > 0 && (
        <ul className="divide-y divide-border" data-testid="agent-tokens">
          {tokens.data.map((t) => (
            <li key={t.id} className="space-y-1 py-2" data-testid={`agent-token-${t.id}`}>
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 text-sm break-words text-fg">{t.name}</span>
                <Badge tone={TOKEN_TONES[t.status]}>{t.status}</Badge>
              </div>
              <p className="text-xs text-fg-muted">
                <code>{t.prefix}…</code> ·{" "}
                {t.scopes.map((s) => SCOPE_LABELS[s]?.label ?? s).join(", ")} · by {t.created_by}
              </p>
              <p className="text-xs text-fg-muted">
                {t.last_used_at ? (
                  <>
                    Last used <Instant value={t.last_used_at} style="long" />
                  </>
                ) : (
                  "Never used"
                )}
                {t.expires_at && t.status === "active" && (
                  <>
                    {" "}
                    · expires <Instant value={t.expires_at} style="long" />
                  </>
                )}
              </p>
              {t.status === "active" &&
                (confirming === t.id ? (
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="danger"
                      disabled={revoke.isPending}
                      onClick={() =>
                        revoke.mutate(t.id, { onSettled: () => setConfirming(null) })
                      }
                      data-testid={`agent-token-confirm-${t.id}`}
                    >
                      Revoke now
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => setConfirming(null)}>
                      Keep
                    </Button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setConfirming(t.id)}
                    data-testid={`agent-token-revoke-${t.id}`}
                  >
                    Revoke
                  </Button>
                ))}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---- data checks ----------------------------------------------------------

const CHECK_TONES: Record<CheckStatus, Tone> = {
  ok: "ok",
  warn: "warn",
  fail: "bad",
  info: "idle",
};

const CHECK_WORDS: Record<CheckStatus, string> = {
  ok: "ok",
  warn: "look",
  fail: "failed",
  info: "info",
};

/** The checks' names, for a person. An id the server adds before this list
 *  learns it still shows, as its id. */
const CHECK_TITLES: Record<string, string> = {
  headline_matches_chart: "Accounts total matches the chart",
  synced_investments_valued: "Synced investments are valued",
  liabilities_signed: "Synced cards and loans hold debt as negative",
  manual_liabilities_positive: "Hand-entered cards and loans",
  accounts_without_rate: "Every currency has a rate",
  stale_accounts: "Synced accounts still reported",
  migrations_applied: "Balance model",
};

/**
 * What the worker verified on start, read live (ADR-0047). An upgrade is a pull
 * and a restart, with no script to run — this card is where its result is read,
 * with the accounts behind each finding named. The worker's own log has only the
 * statuses and counts, so the names are here and nowhere else.
 */
export function DataChecks() {
  const checks = useChecks();
  return (
    <Card
      title="Data checks"
      note="Facts the reports rely on, checked each time the worker starts and again whenever this page loads."
    >
      {checks.isPending && <Spinner />}
      {checks.isError && (
        <p className="text-sm text-negative" role="alert">
          {(checks.error as Error).message}
        </p>
      )}
      {checks.data && (
        <>
          <ul className="divide-y divide-border" data-testid="data-checks">
            {checks.data.checks.map((c) => (
              <CheckRow key={c.id} check={c} />
            ))}
          </ul>
          <p className="text-xs text-fg-muted" data-testid="schema-version">
            Schema {checks.data.schema_version ?? "unknown"}
          </p>
        </>
      )}
    </Card>
  );
}

function CheckRow({ check }: { check: Check }) {
  return (
    <li className="space-y-1 py-2" data-testid={`check-${check.id}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm text-fg">{CHECK_TITLES[check.id] ?? check.id}</span>
        <Badge tone={CHECK_TONES[check.status]}>{CHECK_WORDS[check.status]}</Badge>
      </div>
      <p className="text-xs text-fg-muted">{check.summary}</p>
      {check.items.length > 0 && (
        <ul className="list-inside list-disc text-xs text-fg-muted">
          {check.items.map((item) => (
            <li key={item.account_id}>{item.name}</li>
          ))}
        </ul>
      )}
    </li>
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
