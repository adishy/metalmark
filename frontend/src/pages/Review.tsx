// Swipe review deck: right = reviewed, left = ignore/flag. framer-motion drag
// with optimistic advance; desktop keyboard (← / →). The split/edit sheet is a
// follow-up; this covers the core "swipe to sort" interaction from the spec.
import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useMotionValue, useTransform } from "framer-motion";
import { useCategories, useTransactions, useUpdateTransaction } from "@/api/hooks";
import type { Transaction } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";

export default function Review() {
  const queue = useTransactions({ review_status: "needs_review" });
  const categories = useCategories();
  const update = useUpdateTransaction();
  const [index, setIndex] = useState(0);

  const items = queue.data?.items ?? [];
  const current = items[index];

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  const decide = useCallback(
    (txn: Transaction, keep: boolean) => {
      update.mutate({
        id: txn.id,
        body: { review_status: keep ? "reviewed" : "ignored" },
      });
      setIndex((i) => i + 1);
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

  const remaining = items.length - index;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Review</h2>
        <span className="text-sm text-slate-500" data-testid="review-remaining">
          {remaining > 0 ? `${remaining} to review` : "All done"}
        </span>
      </div>

      {!current ? (
        <p
          className="rounded-2xl bg-slate-900 px-4 py-10 text-center text-sm text-slate-500"
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

          <div className="absolute inset-x-0 -bottom-14 flex justify-center gap-4">
            <button
              onClick={() => decide(current, false)}
              className="rounded-full bg-slate-800 px-6 py-2 text-sm text-red-300"
              data-testid="review-reject"
            >
              ← Ignore
            </button>
            <button
              onClick={() => decide(current, true)}
              className="rounded-full bg-brand px-6 py-2 text-sm font-medium text-slate-950"
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
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-12, 12]);
  const approveOpacity = useTransform(x, [40, 160], [0, 1]);
  const ignoreOpacity = useTransform(x, [-160, -40], [1, 0]);

  return (
    <motion.div
      className="absolute inset-0 flex cursor-grab flex-col justify-between rounded-2xl bg-slate-900 p-6 shadow-xl active:cursor-grabbing"
      style={{ x, rotate }}
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
          style={{ opacity: ignoreOpacity }}
          className="rounded-lg border border-red-400 px-2 py-1 text-xs text-red-300"
        >
          IGNORE
        </motion.span>
        <motion.span
          style={{ opacity: approveOpacity }}
          className="rounded-lg border border-brand px-2 py-1 text-xs text-brand"
        >
          REVIEWED
        </motion.span>
      </div>

      <div>
        <p className="text-xl font-semibold">{txn.merchant || txn.description || "(no description)"}</p>
        <p className="text-sm text-slate-500">
          {formatDate(txn.transacted_at)}
          {categoryName && ` · ${categoryName}`}
        </p>
      </div>

      <p className={`text-2xl font-semibold ${Number(txn.amount) < 0 ? "text-slate-100" : "text-emerald-400"}`}>
        {formatMoney(txn.amount, txn.currency)}
      </p>
    </motion.div>
  );
}
