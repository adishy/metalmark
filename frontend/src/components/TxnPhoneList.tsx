// The ledger on a phone: days, and under each day one roomy row per transaction.
//
// Below `sm:` the desktop row's five facts in one line — date, category, owner,
// review state, amount — were a line of 12 px text squeezing the merchant down
// to "Corner Ph…". A phone row says three things at a size a thumb and an eye
// can use, the way the mainstream apps do:
//
//   [🛒]  Merchant                        −$54.20
//         Groceries · Everyday Checking   [Needs review]
//
// * The date moves out of the row into a **day header**, with the day's net, so
//   it is said once per day instead of once per row.
// * The **category** leads as its emoji in a disc, and is named again under the
//   merchant with the account, so both stay readable without the columns.
// * **Needs review** is a badge in the meta line, next to the amount it is about
//   — a word, tinted, with its own `aria-label` (§5). It was an 8 px amber dot
//   beside the amount until §7.8 caught it: a status is a word, and a dot is
//   neither a word nor an icon, and disappears entirely in greyscale or for a
//   reader who cannot see amber. The owner and tags are in the detail sheet, one
//   tap away.
//
// Income is green and signed "+"; spending is plain — an expense is a normal
// event, not an alarm (DESIGN §6.2).
import type { Account, Category, Transaction } from "@/api/types";
import { UNCATEGORIZED_ICON } from "@/components/CategoryPicker";
import { calendarDay, formatDay } from "@/lib/dates";
import { formatMoneySigned, fromMinorUnits, sumMinorUnits } from "@/lib/format";

interface Day {
  day: string;
  rows: Transaction[];
  /** The day's net in the rows' currency, counted in that currency's **integer
   *  minor units** (ADR-0005 — a day's rows are money, and money is not summed
   *  as a float), or null when the day mixes currencies. */
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
      last.net =
        t.currency === last.currency
          ? last.net + sumMinorUnits([t.amount], t.currency).minor
          : null;
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
                {formatMoneySigned(fromMinorUnits(net, currency), currency)}
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
                      {/* The meta line, and the needs-review marker is *in* it: §5
                          puts a row's markers here, as a text or icon badge with
                          an `aria-label`, and the amber dot this replaces was
                          neither — 8 px of colour beside the amount, which §7.8
                          rules out outright ("needs review" is a word, not a dot)
                          and which a greyscale screen or a reader who cannot see
                          amber did not get at all.

                          The badge is a sibling of the truncating text and never
                          truncates itself: the account name gives way first, the
                          same way the merchant gives way to the amount (§6.5). */}
                      <span className="flex min-w-0 items-center gap-2 text-sm text-fg-muted">
                        <span className="min-w-0 truncate">
                          {categoryText}
                          {account && <span aria-hidden="true"> · </span>}
                          {account && <span className="sr-only">, from </span>}
                          {account?.name}
                          {t.is_pending && " · Pending"}
                        </span>
                        {t.review_status === "needs_review" && (
                          // The same badge the row wears at `sm:` and up, and the
                          // same one the account card wears for a stale balance:
                          // the warning tint, the `-ink` token that is legible on
                          // it (§2.1), and the words.
                          <span
                            className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 text-xs text-warning-ink"
                            aria-label="Needs review"
                            data-testid={`txn-card-review-${t.id}`}
                          >
                            Needs review
                          </span>
                        )}
                      </span>
                    </span>
                    <span
                      className={`shrink-0 text-base font-semibold tabular-nums ${
                        income ? "text-positive" : "text-fg"
                      }`}
                    >
                      {formatMoneySigned(t.amount, t.currency)}
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
