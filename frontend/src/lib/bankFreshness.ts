// Whether a connection has gone quiet: syncing "ok" while bringing nothing new (ADR-0050).
//
// A bank bridge can keep answering with the same cached payload for days, and
// every sync reports success (found on a live instance, session 06). Four quiet
// syncs *and* a day and a half without new data is the line: a household with
// no spending on a Sunday is not a stall, but two days of identical answers is.
import type { Connection } from "@/api/types";

export const QUIET_SYNCS = 4;
export const QUIET_HOURS = 36;

export function isStalled(c: Connection, now: Date = new Date()): boolean {
  if (!c.is_enabled || !c.last_new_data_at) return false;
  const hours = (now.getTime() - new Date(c.last_new_data_at).getTime()) / 36e5;
  return (c.quiet_syncs ?? 0) >= QUIET_SYNCS && hours >= QUIET_HOURS;
}

/** What to call a connection: the owner's local name first, then the bank's own
 *  name, then a generic fallback — the single place this chain lives, so every
 *  screen that names a connection (Settings, Admin, Accounts) agrees. */
export function connectionName(
  c: Pick<Connection, "display_name" | "org_name">,
  fallback = "Unnamed connection",
): string {
  return c.display_name ?? c.org_name ?? fallback;
}
