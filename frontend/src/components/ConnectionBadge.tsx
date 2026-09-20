// A connection's health, and separately whether it is paused.
//
// Two chips rather than one, because they are two different facts: `status` says
// what the *bank* is doing and `is_enabled` says what the *user* did. A paused
// connection that also needs reconnecting is the case that collapses into a
// misleading single label — "Paused" would hide that the credential is dead,
// and "Reconnect needed" would hide that the user turned it off.
//
// Shared rather than re-declared per page: this mapping from wire value to
// wording is the kind of thing that drifts into two surfaces disagreeing about
// what "auth_error" is called, and the user has no way to tell which is right.

import type { Connection, ConnectionStatus } from "@/api/types";

const TONES: Record<ConnectionStatus, string> = {
  ok: "bg-positive/15 text-positive",
  auth_error: "bg-negative/20 text-negative",
  error: "bg-warning/20 text-warning",
};

/**
 * "Reconnect needed" rather than the raw `auth_error`, and never "Login failed":
 * the credential is a bank access URL, so the action a person can actually take
 * is to go and authorise again at the bridge, not to try a password.
 */
const LABELS: Record<ConnectionStatus, string> = {
  ok: "Working",
  auth_error: "Reconnect needed",
  error: "Error",
};

export default function ConnectionBadge({ connection }: { connection: Connection }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span
        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ${TONES[connection.status]}`}
        data-testid={`conn-status-${connection.id}`}
      >
        {LABELS[connection.status]}
      </span>
      {!connection.is_enabled && (
        <span
          className="inline-flex items-center rounded-full bg-surface-inset px-2 py-0.5 text-xs text-fg-muted"
          data-testid={`conn-paused-${connection.id}`}
        >
          Paused
        </span>
      )}
    </span>
  );
}
