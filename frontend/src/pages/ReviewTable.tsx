// The review queue at `lg:`: the whole queue as rows, worked from the keyboard
// (ADR-0057, DESIGN §9.3). Below `lg:` `/review` is the deck of §4.17 — a thumb
// throws a card off the screen, and a keyboard has nothing to throw.
//
// What this page is at `lg:` is a *triage* pass. The queue is a backlog, the job
// is to get through it, and a backlog needs two things a deck cannot give it: to
// be counted at a glance, and to be got through in order without waiting on an
// animation. Rows do both, and §9.4's row vocabulary — the same grammar as a
// ledger row, one page over — makes a queue row and a ledger row the same object
// seen twice, so learning one is learning the other.
//
// The keys are the deck's own (← ignore, → reviewed, `c` category, `e` open)
// with ↑/↓ added for the row. At `lg:` there is no throw to carry the decision,
// so three things say what happened instead: the count, a status line naming the
// row that was filed, and focus landing on the row that took its place.
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  useAccounts,
  useCategories,
  useCategoryGroups,
  useOwners,
  useTags,
  useTransactions,
  useUpdateTransaction,
} from "@/api/hooks";
import type { Account, Transaction } from "@/api/types";
import { formatMoney } from "@/lib/format";
import AccountMark from "@/components/AccountMark";
import { Day } from "@/components/datetime";
import TxnDetailSheet from "@/components/TxnDetailSheet";
import CategoryPicker, { UNCATEGORIZED_ICON, categoryLabel } from "@/components/CategoryPicker";
import { ReviewQueueBody, ReviewRemaining } from "@/pages/ReviewQueue";

/**
 * §9.4's row, plus the column the two verdicts sit in.
 *
 * Written out as a literal rather than built from parts because Tailwind reads
 * source *text*: `grid-cols-${n}` is never generated, and the failure is silent
 * (the row collapses to one column and still renders, just wrong).
 *
 * Every width here is the ledger's, column for column, so the two pages' rows
 * line up with each other: the merchant takes what is left, and each fixed
 * column is sized to hold its own worst value with the actions column last.
 * None of it carries a `lg:` variant — this component is only ever mounted at
 * `lg:` (see `Review`), so a variant would be a prefix that never applies.
 *
 * Every control in a row is `min-h-11`, so the row is 56 px and the row's own
 * `min-h-12` is a floor rather than the thing setting the height (§9.4).
 */
const ROW_COLUMNS = "grid-cols-[1.75rem_minmax(0,1fr)_5rem_7rem_5.5rem_7rem_10rem] gap-x-3";

export default function ReviewTable() {
  const queue = useTransactions({ review_status: "needs_review" });
  const categories = useCategories();
  const groups = useCategoryGroups();
  const accounts = useAccounts();
  const owners = useOwners();
  const tags = useTags();
  const update = useUpdateTransaction();
  // Its own mutation, so a failed verdict is never reported as a failed category.
  const setCategory = useUpdateTransaction();

  // Decided by id, never by position: the query refetches after each decision
  // and the decided row drops out of the list, so an index would skip the row
  // that slid into the vacated slot.
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set());
  // A pick is shown on the row at once — before the refetch lands — so the row
  // never says the old answer after the person has given a new one.
  const [picked, setPicked] = useState<ReadonlyMap<string, string | null>>(() => new Map());
  // The row the keyboard is on. Null means "nobody has been here yet", which the
  // fallback below reads as the first row.
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // The row a key acts on, and the row a click acts on, are the same row: this
  // is set again on every focus, so a click on one of a row's buttons makes that
  // row the current one.
  const [picking, setPicking] = useState<Transaction | null>(null);
  const [editing, setEditing] = useState<Transaction | null>(null);
  // What the last decision was, for the status line. The count says how many are
  // left; this says what the key just did, which is the part a table has no
  // throw to show.
  const [lastDecision, setLastDecision] = useState("");
  const [decideError, setDecideError] = useState("");

  const rows = useMemo(
    () => (queue.data?.items ?? []).filter((t) => !decided.has(t.id)),
    [queue.data, decided],
  );
  const byId = useMemo(() => new Map(rows.map((t) => [t.id, t])), [rows]);
  const remaining = rows.length;

  // The row that is current, falling back to the first so the table always has
  // exactly one tab stop in it (§4.14's roving model, one level down from a tab
  // strip: the list is one stop and the arrows move inside it).
  const currentId = focusedId !== null && byId.has(focusedId) ? focusedId : (rows[0]?.id ?? null);

  const listRef = useRef<HTMLUListElement | null>(null);
  // Focus has to move *after* React has re-rendered: the row focus is going to
  // is the one that replaces the decided row, and that element does not exist
  // until the decided one is gone. A ref rather than state because it is an
  // instruction to the effect below, not something the render reads.
  const pendingFocus = useRef(false);
  // Bumped by every requested move, so the effect runs even when the move lands
  // on the row that is already current — which is exactly what a failed
  // decision does when it puts the row back.
  const [focusTick, setFocusTick] = useState(0);

  useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    pendingFocus.current = false;
    if (currentId === null) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-testid="review-open-${currentId}"]`)
      ?.focus();
  }, [currentId, focusTick]);

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, categoryLabel(c)));
    return m;
  }, [categories.data]);

  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    owners.data?.forEach((o) => m.set(o.id, o.name));
    return m;
  }, [owners.data]);

  // Whole accounts, not just names: the mark needs the institution too (it picks
  // the hue) and the owner cell's sentence names the account.
  const acctFor = useMemo(() => {
    const m = new Map<string, Account>();
    accounts.data?.forEach((a) => m.set(a.id, a));
    return m;
  }, [accounts.data]);
  const acctName = (id: string) => acctFor.get(id)?.name ?? "the account";

  /** What the row's category cell shows: a pick not yet saved outranks what the
   *  server last said. */
  const categoryOf = (t: Transaction) =>
    picked.has(t.id) ? (picked.get(t.id) ?? null) : t.category_id;

  /** The row's identity — for the buttons that have to say which row they are
   *  in. A triage table is fifty rows of "Ignore", and "Ignore" on its own is
   *  not a name anyone can act on. */
  const label = (t: Transaction) => t.merchant || t.description || "(no description)";

  function focusRow(id: string) {
    pendingFocus.current = true;
    setFocusedId(id);
    setFocusTick((n) => n + 1);
  }

  function decide(t: Transaction, keep: boolean) {
    // Where focus goes: the row that takes the vacated place — the one after it,
    // or the new last row when the decided row was the last. Worked out before
    // the row is removed, because afterwards there is no index left to look at.
    const i = rows.findIndex((r) => r.id === t.id);
    const next = rows[i + 1] ?? rows[i - 1] ?? null;
    setDecided((prev) => new Set(prev).add(t.id));
    setDecideError("");
    setLastDecision(`${label(t)} ${keep ? "reviewed" : "ignored"}.`);
    if (next) focusRow(next.id);
    update.mutate(
      { id: t.id, body: { review_status: keep ? "reviewed" : "ignored" } },
      {
        // The deck can hide a failed decision behind the throw. A table cannot.
        // A row that vanished and was never filed is the one outcome a triage
        // pass must never produce, so the row comes back, focus goes with it,
        // and the alert says why.
        onError: (e) => {
          setDecided((prev) => {
            const n = new Set(prev);
            n.delete(t.id);
            return n;
          });
          setLastDecision("");
          setDecideError(
            `${label(t)} was not ${keep ? "reviewed" : "ignored"}: ${(e as Error).message}`,
          );
          focusRow(t.id);
        },
      },
    );
  }

  function pickCategory(categoryId: string | null) {
    if (!picking) return;
    const id = picking.id;
    setPicked((prev) => new Map(prev).set(id, categoryId));
    setCategory.mutate(
      { id, body: { category_id: categoryId } },
      {
        // A failed save must not leave the row claiming the new answer.
        onError: () =>
          setPicked((prev) => {
            const next = new Map(prev);
            next.delete(id);
            return next;
          }),
      },
    );
  }

  // The row the keys act on is read off the event's own target rather than from
  // state: the handler is on the `<ul>`, so the row is whichever one the key was
  // pressed in, and that is true even in the frame before React has re-rendered.
  function onListKeyDown(e: React.KeyboardEvent<HTMLUListElement>) {
    // A modifier belongs to a browser or an OS shortcut (⌘E, Ctrl+C), not to us.
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    // A key a form control is using is that control's. This table has no fields
    // of its own, so this is the line that matters the day it grows one — which
    // is exactly when nobody will remember to come back here.
    const target = e.target as HTMLElement | null;
    if (
      target?.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")
    ) {
      return;
    }
    const id = target?.closest<HTMLElement>("[data-row]")?.dataset.row;
    const txn = id ? byId.get(id) : undefined;
    if (!txn) return;
    const i = rows.findIndex((r) => r.id === txn.id);
    switch (e.key) {
      // Clamped rather than wrapped: a queue has a top and a bottom, and falling
      // off one end onto the other is how someone files a row they never saw.
      case "ArrowDown": {
        const next = rows[i + 1];
        if (next) {
          e.preventDefault();
          focusRow(next.id);
        }
        break;
      }
      case "ArrowUp": {
        const prev = rows[i - 1];
        if (prev) {
          e.preventDefault();
          focusRow(prev.id);
        }
        break;
      }
      // The deck's two verdicts, direction and all: ← and → mean the same thing
      // on both instruments, so the muscle memory crosses the breakpoint.
      case "ArrowRight":
        e.preventDefault();
        decide(txn, true);
        break;
      case "ArrowLeft":
        e.preventDefault();
        decide(txn, false);
        break;
      case "c":
        e.preventDefault();
        setPicking(txn);
        break;
      case "e":
        e.preventDefault();
        setEditing(txn);
        break;
    }
  }

  // The current row is the tab stop and the rest are reachable with ↑/↓. A queue
  // of fifty rows is fifty tab stops otherwise, which is not a keyboard route to
  // the fiftieth row, it is an obstacle course. Everything in a row is still
  // clickable and still in the accessibility tree; only the tab order narrows.
  const stopOf = (t: Transaction) => (t.id === currentId ? 0 : -1);

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Review</h1>
        <ReviewRemaining queue={queue} remaining={remaining} />
      </div>

      <ReviewQueueBody queue={queue} isEmpty={remaining === 0}>
        {/* The keys, in the open, above the table they work on. Not a tooltip and
            not a `title`: §9.5's rule runs the other way too — a keyboard route
            nobody can find is a keyboard route nobody uses. */}
        <p className="text-xs text-fg-muted" data-testid="review-keys">
          <Key>↑</Key>
          <Key>↓</Key> move between rows · <Key>←</Key> ignore · <Key>→</Key> reviewed ·{" "}
          <Key>c</Key> category · <Key>e</Key> open
        </p>

        <ul
          ref={listRef}
          // The queue is one composite: ↑/↓ move inside it and every other key
          // acts on the row it was pressed in, so the handler belongs to the list
          // rather than to each of its fifty rows.
          onKeyDown={onListKeyDown}
          aria-label="Review queue"
          className="divide-y divide-border rounded-card bg-surface-raised"
          data-testid="review-rows"
        >
          {/* A real header row (§9.4). `aria-hidden` because it is a *second*
              naming of values every row already carries as text: the sighted
              reader needs to know which column is which, and a screen reader
              hearing "Merchant Date Category Owner Amount" before every row's own
              values would be read the same thing twice. */}
          <li
            aria-hidden="true"
            className={`grid items-center px-4 pt-3 pb-2 text-xs font-medium text-fg-muted ${ROW_COLUMNS}`}
          >
            {/* Empty, because the mark column holds a picture and a caption over
                a picture is worse than the accessible name `AccountMark` already
                carries. */}
            <span />
            <span>Merchant</span>
            <span>Date</span>
            <span>Category</span>
            <span>Owner</span>
            <span className="text-right">Amount</span>
            <span className="text-right">Decide</span>
          </li>

          {rows.map((t) => {
            const cat = categoryOf(t);
            return (
              <li
                key={t.id}
                data-row={t.id}
                data-testid={`review-row-${t.id}`}
                // The row follows focus however focus got there, including a
                // click on one of its own buttons — which is what keeps the roving
                // tab stop under the pointer as well as under the keyboard.
                onFocus={() => setFocusedId(t.id)}
                className={`grid min-h-12 items-center px-4 py-1.5 hover:bg-surface-inset/40 ${ROW_COLUMNS}`}
              >
                <AccountMark
                  name={acctFor.get(t.account_id)?.name ?? "(unknown account)"}
                  institution={acctFor.get(t.account_id)?.institution}
                />

                {/* The row's identity, and the row's tab stop. It is a button and
                    not the whole row because the row carries three other
                    controls, and a control inside a control is not a thing HTML
                    has. Its description is the rest of the row, so the columns
                    are announced with the name instead of being left for a
                    reader to go hunting for. */}
                <button
                  type="button"
                  onClick={() => setEditing(t)}
                  tabIndex={stopOf(t)}
                  aria-describedby={`review-fields-${t.id}`}
                  className="flex min-h-11 min-w-0 items-center text-left font-medium"
                  data-testid={`review-open-${t.id}`}
                >
                  <span className="truncate">{label(t)}</span>
                </button>

                {/* `contents`, so its four children are the row's own grid cells:
                    one element that is both the thing the button above points at
                    and four columns of §9.4's row. */}
                <div id={`review-fields-${t.id}`} className="contents">
                  <Day value={t.transacted_at} style="compact" className="truncate" />

                  {/* Where the category is assigned: the same pill, opening the
                      same picker, as the deck's — see ADR-0057 on why this is
                      not a per-row `<select>`. */}
                  <button
                    type="button"
                    onClick={() => setPicking(t)}
                    tabIndex={-1}
                    aria-haspopup="dialog"
                    aria-label={
                      cat && catName.get(cat)
                        ? `${catName.get(cat)} — change category for ${label(t)}`
                        : `Uncategorized — choose a category for ${label(t)}`
                    }
                    className="inline-flex min-h-11 max-w-full items-center gap-1 truncate rounded-full border border-border-strong bg-surface-raised px-3 text-sm hover:bg-surface-inset"
                    data-testid={`review-category-${t.id}`}
                  >
                    {cat && catName.get(cat) ? (
                      <span className="truncate">{catName.get(cat)}</span>
                    ) : (
                      <span className="truncate text-fg-muted">
                        <span aria-hidden="true">{UNCATEGORIZED_ICON} </span>
                        Uncategorized
                      </span>
                    )}
                  </button>

                  {/* The effective owner is what reports bucket by, so that is
                      what the row shows; a muted style marks the ones that only
                      inherit it from their account — §9.4's owner column, drawn
                      exactly as the ledger draws it. */}
                  <span
                    className={`truncate ${t.owner_id ? "text-fg" : "italic text-fg-muted"}`}
                    title={
                      t.owner_id
                        ? "Owner set on this transaction"
                        : `Inherited from ${acctName(t.account_id)}`
                    }
                    data-testid={`review-owner-${t.id}`}
                  >
                    {ownerName.get(t.effective_owner_id) ?? "Shared"}
                    {!t.owner_id && (
                      <>
                        {/* Inherited needs a marker that survives greyscale: the
                            glyph is decorative, the sentence behind it is the
                            accessible text (§7.8). */}
                        <span aria-hidden="true"> ↳</span>
                        <span className="sr-only">
                          {` (inherited from ${acctName(t.account_id)})`}
                        </span>
                      </>
                    )}
                  </span>

                  {/* The number never gives way (§6.5), and a column of them has
                      to line up. */}
                  <span
                    className={`text-right text-base font-semibold tabular-nums ${
                      Number(t.amount) < 0 ? "text-fg" : "text-positive"
                    }`}
                  >
                    {formatMoney(t.amount, t.currency)}
                  </span>
                </div>

                <div className="flex items-center justify-end gap-1">
                  {/* The two verdicts, in the order the keys point: ignore is ←
                      and sits left, reviewed is → and sits right. Each carries
                      the row's name, because "Ignore" fifty times over is not a
                      name. */}
                  <button
                    type="button"
                    onClick={() => decide(t, false)}
                    tabIndex={-1}
                    aria-label={`Ignore ${label(t)}`}
                    className="inline-flex min-h-11 items-center justify-center rounded-full bg-surface-inset px-3 text-sm text-negative"
                    data-testid={`review-ignore-${t.id}`}
                  >
                    Ignore
                  </button>
                  <button
                    type="button"
                    onClick={() => decide(t, true)}
                    tabIndex={-1}
                    aria-label={`Mark ${label(t)} reviewed`}
                    className="inline-flex min-h-11 items-center justify-center rounded-full bg-accent px-3 text-sm font-medium text-accent-fg"
                    data-testid={`review-approve-${t.id}`}
                  >
                    Reviewed
                  </button>
                </div>
              </li>
            );
          })}
        </ul>

        {/* Mounted whether or not it has anything to say, so the region exists
            before its message does — a live region that arrives with its text is
            one a screen reader may never announce (§7.6). The count above says
            how many are left; this says what the last key did. */}
        <div className="min-h-5 space-y-1">
          <p
            role="status"
            aria-atomic="true"
            className="text-sm text-fg-muted"
            data-testid="review-last"
          >
            {lastDecision}
          </p>
          {decideError && (
            <p role="alert" className="text-sm text-negative" data-testid="review-decide-error">
              {decideError}
            </p>
          )}
          {setCategory.isError && (
            <p role="alert" className="text-sm text-negative" data-testid="review-category-error">
              Couldn&rsquo;t save the category: {(setCategory.error as Error).message}
            </p>
          )}
        </div>
      </ReviewQueueBody>

      {picking && (
        <CategoryPicker
          open
          onClose={() => setPicking(null)}
          categories={categories.data ?? []}
          groups={groups.data ?? []}
          value={categoryOf(picking)}
          amount={picking.amount}
          onPick={pickCategory}
          testid="review-category-picker"
        />
      )}

      {editing && (
        <TxnDetailSheet
          txn={editing}
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          tags={tags.data ?? []}
          // Over the table, never beside it: §9.3's pane is the Transactions
          // shape, where the list is narrow enough to leave a column for it. This
          // list is the whole page, and there is no column to put a pane in.
          presentation="overlay"
          onClose={() => setEditing(null)}
          // In place, not a navigation: the sheet re-keys on the id, so pointing
          // it at the row it already has keeps the form as it is.
          onReplaced={setEditing}
        />
      )}
    </div>
  );
}

/** A key, printed as a key. */
function Key({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-border-strong px-1 font-sans text-fg">{children}</kbd>
  );
}
