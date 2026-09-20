// Calendar days on the wire. The API's date fields (`?start=`, `?end=`, the
// `YYYY-MM-DD` half of `transacted_at`) are calendar days, not instants, so they
// must be built from the viewer's local components.

/**
 * A Date's **local** calendar day as `YYYY-MM-DD`.
 *
 * Deliberately not `toISOString().slice(0, 10)`: that converts to UTC first, so
 * `new Date(2026, 11, 31)` — local midnight on New Year's Eve — round-trips as
 * `2026-12-30` for anyone east of UTC. As a report range end that silently drops
 * the last day; as a form default it shows yesterday until the local clock
 * catches up with UTC. Both are invisible in a UTC browser, which is most of
 * them, which is why this lives in one place instead of at each call site.
 */
export function isoDay(d: Date): string {
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

/** Today, as the viewer's local calendar day. The default for date inputs. */
export function todayIso(): string {
  return isoDay(new Date());
}
