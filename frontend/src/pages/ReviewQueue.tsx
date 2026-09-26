// The two pieces of `/review` that both of its instruments share: the count
// beside the `<h1>`, and the three states a queue can be in before there is
// anything to review (§4.10/§4.11).
//
// `/review` has two trees — the deck of §4.17 below `lg:`, the triage table of
// §9.3 at `lg:` (ADR-0057) — and §9's breakpoint rule is that two trees drift.
// The *instrument* is different on purpose; what must not differ is what the
// page says about the household's data. So the sentence, the three states and
// their testids live here, in one place, and both trees render them: "nothing to
// review" is one string rather than two that agree today and get edited apart in
// six months.
import type { ReactNode } from "react";
import { Button } from "@/components/form";

/** Only the four fields these two components read, so either tree can pass its
 *  own `useTransactions(...)` result without this module knowing which query the
 *  queue came from — or having to name a react-query type to say so. */
export interface ReviewQueue {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

/** How much is left, beside the heading.
 *
 * The count changes on every decision, and nothing moves focus to it — the whole
 * string is the status, not the numeral in it (§7.6).
 *
 * Deliberately empty while loading or errored. "All done" is a claim about the
 * household's data and we do not yet know it is true — announcing it, then
 * correcting it, is worse than saying nothing. The error state below carries the
 * failure. */
export function ReviewRemaining({
  queue,
  remaining,
}: {
  queue: ReviewQueue;
  remaining: number;
}) {
  return (
    <span
      role="status"
      aria-atomic="true"
      className="text-sm text-fg-muted"
      data-testid="review-remaining"
    >
      {queue.isPending || queue.isError
        ? ""
        : remaining > 0
          ? `${remaining} to review`
          : "All done"}
    </span>
  );
}

/** The three states, in the order they have to be tested, and `children` — the
 *  instrument — only when there is something to review.
 *
 * Three distinct states, never conflated (§4.10/§4.11). Before they were one
 * branch, a failed fetch and a slow one both rendered "Nothing to review. 🎉",
 * which tells the person their queue is clear when in fact it never loaded. */
export function ReviewQueueBody({
  queue,
  isEmpty,
  children,
}: {
  queue: ReviewQueue;
  /** No card, no row: the queue itself is empty. */
  isEmpty: boolean;
  children: ReactNode;
}) {
  if (queue.isError) {
    return (
      <div
        className="rounded-card bg-surface-raised px-4 py-10 text-center"
        data-testid="review-error"
      >
        <p role="alert" className="text-sm text-negative">
          <span aria-hidden="true">⚠ </span>Couldn&rsquo;t load your review queue.
        </p>
        <p className="mt-1 text-sm text-fg-muted">
          {queue.error instanceof Error ? queue.error.message : "The request failed."}
        </p>
        <Button variant="secondary" className="mt-3" onClick={() => queue.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (queue.isPending) {
    return (
      <p
        className="rounded-card bg-surface-raised px-4 py-10 text-center text-sm text-fg-muted"
        data-testid="review-loading"
      >
        Loading your review queue…
      </p>
    );
  }
  if (isEmpty) {
    return (
      <p
        className="rounded-card bg-surface-raised px-4 py-10 text-center text-sm text-fg-muted"
        data-testid="review-empty"
      >
        Nothing to review. 🎉
      </p>
    );
  }
  return <>{children}</>;
}
