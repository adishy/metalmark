// The §4.10/§4.11 loading and error states, as components because they are the
// two states most easily conflated with the empty one — a failed fetch that
// renders "No holdings yet." tells the user their data is gone, and a loading
// state that renders as an empty list tells them the same thing a second later.
//
// Both are shared rather than per-view: §4.11 makes the error-plus-retry shape
// universal, and the skeleton is the same skeleton on every list.

import { Button } from "@/components/form";

/**
 * Reserved space for rows that have not arrived — a skeleton, not a spinner.
 * A spinner collapses to nothing and then shoves the page when the data lands
 * (§4.11).
 *
 * The bars are `aria-hidden`: the accessible half of this state is the live
 * region beside it, because a screen reader has nothing to make of six grey
 * rectangles.
 */
export function SkeletonRows({
  rows = 3,
  what,
  testid,
}: {
  rows?: number;
  /** Named in the live region: "Loading holdings…". */
  what: string;
  testid: string;
}) {
  return (
    <>
      <div
        className="space-y-3 rounded-card bg-surface-raised p-4"
        aria-hidden="true"
        data-testid={testid}
      >
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="h-5 animate-pulse rounded bg-surface-inset" />
        ))}
      </div>
      <p className="sr-only" role="status">
        Loading {what}…
      </p>
    </>
  );
}

/**
 * A failed read: the API's own message, in `text-negative`, with a retry. The
 * message is rendered rather than replaced because the API's errors say what
 * went wrong and a generic "something failed" throws that away (§4.11).
 */
export function QueryError({
  what,
  error,
  onRetry,
  testid,
}: {
  /** Named without a sentence: "your holdings". */
  what: string;
  error: unknown;
  onRetry: () => void;
  testid: string;
}) {
  return (
    <div className="rounded-card bg-surface-raised px-4 py-6 text-center" data-testid={testid}>
      {/* role="alert": a failed load is announced without moving focus. */}
      <p role="alert" className="text-sm text-negative">
        <span aria-hidden="true">⚠ </span>
        Couldn&rsquo;t load {what}.
      </p>
      <p className="mt-1 text-sm text-fg-muted">
        {error instanceof Error ? error.message : "The request failed."}
      </p>
      <Button variant="secondary" className="mt-3" onClick={onRetry} data-testid={`${testid}-retry`}>
        Try again
      </Button>
    </div>
  );
}
