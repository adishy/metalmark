// The ledger on a phone: days, and under each day one roomy row per transaction.
//
// Below `sm:` the desktop row's five facts in one line — date, category, owner,
// review state, amount — were a line of 12 px text squeezing the merchant down
// to "Corner Ph…". A phone row says three things at a size a thumb and an eye
// can use, the way the mainstream apps do:
//
//   [🛒]  Merchant                        −$54.20
//         Groceries · Everyday Checking        •
//
// * The date moves out of the row into a **day header**, with the day's net, so
//   it is said once per day instead of once per row.
// * The **category** leads as its emoji in a disc, and is named again under the
//   merchant with the account, so both stay readable without the columns.
// * **Needs review** is a dot beside the amount, spelled out for a screen
//   reader. The owner and tags are in the detail sheet, one tap away.
//
// Income is green and signed "+"; spending is plain — an expense is a normal
// event, not an alarm (DESIGN §6.2).
import type { Account, Category, Transaction } from "@/api/types";
import { UNCATEGORIZED_ICON } from "@/components/CategoryPicker";
import { calendarDay, formatDay } from "@/lib/dates";
import { formatMoney } from "@/lib/format";

interface Day {
  day: string;
  rows: Transaction[];
  /** The day's net in the rows' currency, or null when the day mixes currencies. */
  net: number | null;
  currency: string;
}

function byDay(items: Transaction[]): Day[] {
  const days: Day[] = [];
  for (const t of items) {
    const day = calendarDay(t.transacted_at);
    let last = days[days.length - 1];
    if (!last || last.day !== day) {
      last = { day, rows: [], net: 0, currency: t.currency };
      days.push(last);
    }
    last.rows.push(t);
    if (last.net !== null) {
      last.net = t.currency === last.currency ? last.net + Number(t.amount) : null;
    }
  }
  return days;
}

function dayLabel(day: string): string {
  const compact = formatDay(day, "compact");
  const medium = formatDay(day, "medium");
  // "Today" and "Yesterday" say it best; any other day is its weekday and date.
  return compact !== medium ? `${compact}, ${medium}` : `${formatDay(day, "weekday")}, ${medium}`;
}

function signed(amount: string, currency: string): string {
  const n = Number(amount);
  return n > 0 ? `+${formatMoney(amount, currency)}` : formatMoney(amount, currency);
}

export default function TxnPhoneList({
  items,
  categories,
  accounts,
  onOpen,
}: {
  items: Transaction[];
  categories: Map<string, Category>;
  accounts: Map<string, Account>;
  onOpen: (t: Transaction) => void;
}) {
  return (
    <div className="space-y-4" data-testid="txn-list-phone">
      {byDay(items).map(({ day, rows, net, currency }) => (
        <section key={day} aria-label={dayLabel(day)}>
          <div className="flex items-baseline justify-between px-1 pb-2">
            <h2 className="text-sm font-semibold text-fg">{dayLabel(day)}</h2>
            {net !== null && (
              <span className={`text-sm tabular-nums ${net > 0 ? "text-positive" : "text-fg-muted"}`}>
                {signed(String(net), currency)}
              </span>
            )}
          </div>
          <ul className="divide-y divide-border overflow-hidden rounded-card bg-surface-raised">
            {rows.map((t) => {
              const cat = t.category_id ? categories.get(t.category_id) : undefined;
              const account = accounts.get(t.account_id);
              const categoryText = t.is_split_parent ? "Split" : (cat?.name ?? "Uncategorized");
              const income = Number(t.amount) > 0;
              return (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(t)}
                    className="flex min-h-16 w-full items-center gap-3 px-4 py-3 text-left active:bg-surface-inset"
                    data-testid={`txn-card-${t.id}`}
                  >
                    <span
                      aria-hidden="true"
                      className="flex size-10 shrink-0 items-center justify-center rounded-full bg-surface-inset text-xl leading-none"
                    >
                      {t.is_split_parent ? "✂️" : (cat?.icon ?? UNCATEGORIZED_ICON)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base font-medium text-fg">
                        {t.merchant || t.description || "(no description)"}
                      </span>
                      <span className="block truncate text-sm text-fg-muted">
                        {categoryText}
                        {account && <span aria-hidden="true"> · </span>}
                        {account && <span className="sr-only">, from </span>}
                        {account?.name}
                        {t.is_pending && " · Pending"}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {t.review_status === "needs_review" && (
                        <>
                          <svg viewBox="0 0 8 8" aria-hidden="true" className="size-2 text-warning">
                            <circle cx="4" cy="4" r="4" fill="currentColor" />
                          </svg>
                          <span className="sr-only">Needs review.</span>
                        </>
                      )}
                      <span
                        className={`text-base font-semibold tabular-nums ${
                          income ? "text-positive" : "text-fg"
                        }`}
                      >
                        {signed(t.amount, t.currency)}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
