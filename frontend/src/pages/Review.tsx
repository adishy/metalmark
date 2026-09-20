// Swipe review deck: right = reviewed, left = ignore/flag. framer-motion drag
// with optimistic advance; desktop keyboard (← / →). The split/edit sheet is a
// follow-up; this covers the core "swipe to sort" interaction from the spec.
import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useMotionValue, useReducedMotion, useTransform } from "framer-motion";
import { useCategories, useTransactions, useUpdateTransaction } from "@/api/hooks";
import type { Transaction } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import { Button } from "@/components/form";

export default function Review() {
  const queue = useTransactions({ review_status: "needs_review" });
  const categories = useCategories();
  const update = useUpdateTransaction();
  // Track the cards we have decided by id, never by position: the query
  // refetches after each decision and the decided txn drops out of the list, so
  // an index would skip the card that slid into the vacated slot.
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set());

  const items = useMemo(
    () => (queue.data?.items ?? []).filter((t) => !decided.has(t.id)),
    [queue.data, decided],
  );
  const current = items[0];

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  const decide = useCallback(
    (txn: Transaction, keep: boolean) => {
      setDecided((prev) => {
        const next = new Set(prev);
        next.add(txn.id);
        return next;
      });
      update.mutate({
        id: txn.id,
        body: { review_status: keep ? "reviewed" : "ignored" },
      });
    },
    [update],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!current) return;
      if (e.key === "ArrowRight") decide(current, true);
      if (e.key === "ArrowLeft") decide(current, false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, decide]);

  const remaining = items.length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-medium">Review</h1>
        {/* The queue count changes on every decision, and nothing moves focus to
            it — the whole string is the status, not the numeral in it (§7.6). */}
        <span
          role="status"
          aria-atomic="true"
          className="text-sm text-fg-muted"
          data-testid="review-remaining"
        >
          {/* Deliberately empty while loading or errored. "All done" is a claim
              about the household's data and we do not yet know it is true —
              announcing it, then correcting it, is worse than saying nothing.
              The alert below carries the error. */}
          {queue.isPending || queue.isError
            ? ""
            : remaining > 0
              ? `${remaining} to review`
              : "All done"}
        </span>
      </div>

      {/* Three distinct states, never conflated (§4.10/§4.11). Before this, a
          failed fetch and a slow one both rendered "Nothing to review. 🎉" —
          which tells the user their queue is clear when in fact it never
          loaded. */}
      {queue.isError ? (
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
      ) : queue.isPending ? (
        <p
          className="rounded-card bg-surface-raised px-4 py-10 text-center text-sm text-fg-muted"
          data-testid="review-loading"
        >
          Loading your review queue…
        </p>
      ) : !current ? (
        <p
          className="rounded-card bg-surface-raised px-4 py-10 text-center text-sm text-fg-muted"
          data-testid="review-empty"
        >
          Nothing to review. 🎉
        </p>
      ) : (
        <div className="relative h-64" data-testid="review-deck">
          <AnimatePresence>
            <SwipeCard
              key={current.id}
              txn={current}
              categoryName={current.category_id ? catName.get(current.category_id) : undefined}
              onDecide={(keep) => decide(current, keep)}
            />
          </AnimatePresence>

          {/* min-h-11 on both: these are the primary phone interaction, and
              `py-2 text-sm` alone computed to 36 px (§4.1, §5). */}
          <div className="absolute inset-x-0 -bottom-14 flex justify-center gap-4">
            <button
              onClick={() => decide(current, false)}
              className="inline-flex min-h-11 items-center justify-center rounded-full bg-surface-inset px-6 text-sm text-negative"
              data-testid="review-reject"
            >
              ← Ignore
            </button>
            <button
              onClick={() => decide(current, true)}
              className="inline-flex min-h-11 items-center justify-center rounded-full bg-accent px-6 text-sm font-medium text-accent-fg"
              data-testid="review-approve"
            >
              Reviewed →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SwipeCard({
  txn,
  categoryName,
  onDecide,
}: {
  txn: Transaction;
  categoryName?: string;
  onDecide: (keep: boolean) => void;
}) {
  // The CSS rule in index.css does not reach framer-motion's JS-driven
  // transforms (§2.8). Drag itself stays either way: the card following the
  // finger is the user's own motion, and it is a route to dismissing a card.
  // What goes is the flourish — the rotation, and the IGNORE/REVIEWED labels
  // fading in with distance. The two buttons below carry the same two words.
  const reduce = useReducedMotion();
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-12, 12]);
  const approveOpacity = useTransform(x, [40, 160], [0, 1]);
  const ignoreOpacity = useTransform(x, [-160, -40], [1, 0]);

  return (
    <motion.div
      className="absolute inset-0 flex cursor-grab flex-col justify-between rounded-card bg-surface-raised p-6 shadow-lg active:cursor-grabbing"
      style={{ x, rotate: reduce ? 0 : rotate }}
      drag="x"
      dragConstraints={{ left: 0, right: 0 }}
      dragElastic={0.6}
      onDragEnd={(_e, info) => {
        if (info.offset.x > 120) onDecide(true);
        else if (info.offset.x < -120) onDecide(false);
      }}
      data-testid="swipe-card"
    >
      <div className="flex justify-between">
        <motion.span
          style={{ opacity: reduce ? 0 : ignoreOpacity }}
          className="rounded-control border border-negative px-2 py-1 text-xs text-negative"
        >
          IGNORE
        </motion.span>
        <motion.span
          style={{ opacity: reduce ? 0 : approveOpacity }}
          className="rounded-control border border-accent px-2 py-1 text-xs text-accent"
        >
          REVIEWED
        </motion.span>
      </div>

      <div>
        <p className="text-xl font-semibold">{txn.merchant || txn.description || "(no description)"}</p>
        <p className="text-sm text-fg-muted">
          {formatDate(txn.transacted_at)}
          {categoryName && ` · ${categoryName}`}
        </p>
      </div>

      <p className={`text-2xl font-semibold ${Number(txn.amount) < 0 ? "text-fg" : "text-positive"}`}>
        {formatMoney(txn.amount, txn.currency)}
      </p>
    </motion.div>
  );
}
