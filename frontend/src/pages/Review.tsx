// Basic review queue. The swipe deck (framer-motion + @use-gesture) and the
// split/edit sheet are layered on top of this by the UT workstream.
import { useTransactions, useUpdateTransaction } from "@/api/hooks";
import { formatDate, formatMoney } from "@/lib/format";

export default function Review() {
  const queue = useTransactions({ review_status: "needs_review" });
  const update = useUpdateTransaction();

  const items = queue.data?.items ?? [];

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Review</h2>
      {items.length === 0 && (
        <p className="rounded-2xl bg-slate-900 px-4 py-6 text-center text-sm text-slate-500" data-testid="review-empty">
          Nothing to review. 🎉
        </p>
      )}
      <ul className="space-y-2" data-testid="review-list">
        {items.map((t) => (
          <li
            key={t.id}
            className="flex items-center justify-between rounded-2xl bg-slate-900 px-4 py-3"
          >
            <div>
              <p className="font-medium">{t.merchant || t.description || "(no description)"}</p>
              <p className="text-xs text-slate-500">{formatDate(t.transacted_at)}</p>
            </div>
            <div className="flex items-center gap-3">
              <span>{formatMoney(t.amount, t.currency)}</span>
              <button
                className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-slate-950"
                data-testid={`review-approve-${t.id}`}
                onClick={() => update.mutate({ id: t.id, body: { review_status: "reviewed" } })}
              >
                Reviewed
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
