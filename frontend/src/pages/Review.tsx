// Swipe review deck: right = reviewed, left = ignore/flag, up = open the
// transaction (ARCHITECTURE §"Transaction review"). framer-motion drag with
// optimistic advance; desktop keyboard (← / → to decide, `e` to open).
//
// **It is a deck, and it has to look like one** (§4.17). Two things were missing
// and they are the same defect seen twice: there was nothing behind the card, so
// the fortieth card of a queue looked exactly like the first and nothing on
// screen said how much was left; and a decision produced no motion in the
// direction it was made, so "Reviewed" and "Ignore" were indistinguishable
// unless you re-read the button you had just pressed. Both are fixed here: two
// cards sit behind the live one, offset and inset so their edges show, and the
// decision throws the card off the side it was sent to.
//
// **The cards behind are empty.** A stack shows you the *edges* of what is under
// the top card — the depth is the whole message, and an 8 px strip of a card
// cannot be read. Rendering their contents would put every merchant name in the
// queue into the accessibility tree three times over and give every e2e selector
// that says `swipe-card` three matches. An empty rounded rectangle at the right
// offset is not a shortcut around those problems; it is what a stack looks like.
//
// **The card on its way out is the only card on screen.** The decided
// transaction is held in `leaving` — the whole row, not just its id — for the
// length of the throw, and the deck draws that card and nothing else until it
// lands. It has to be the whole row because the queue stops returning the
// transaction the moment the mutation is accepted, and the throw has to outlive
// that; it has to be the only card because the next one arriving behind a card
// mid-throw both spoils the throw and puts two `swipe-card` elements in the DOM
// at exactly the moment the tests are asserting the advance.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from "framer-motion";
import {
  useAccounts,
  useCategories,
  useTags,
  useTransactions,
  useUpdateTransaction,
} from "@/api/hooks";
import type { Account, Transaction } from "@/api/types";
import { formatMoney } from "@/lib/format";
import AccountMark from "@/components/AccountMark";
import { Day } from "@/components/datetime";
import { Button } from "@/components/form";
import TxnDetailSheet from "@/components/TxnDetailSheet";

/** How long a decided card stays in the DOM, flying to the side. Long enough to
 *  read as a throw, short enough that a fast reviewer is never waiting on it.
 *  The exit is the only thing this delay exists for. */
const FLY_MS = 240;
const FLY_X = 420;

/** How far sideways a drag has to go to mean a decision. */
const SWIPE_X = 120;

/** How far up the card can be pulled, and how far it has to be pulled to open
 *  the transaction. Constrained rather than thrown: the card is not going
 *  anywhere — the sheet opening is what says the gesture landed. */
const LIFT_PX = 140;
const LIFT_TRIGGER = 80;

export default function Review() {
  const queue = useTransactions({ review_status: "needs_review" });
  const categories = useCategories();
  const accounts = useAccounts();
  const tags = useTags();
  const update = useUpdateTransaction();
  // The card the detail sheet is open on, or null. Held as the whole row for the
  // same reason `leaving` is: the sheet is keyed on the id and edits the row it
  // was handed, so a query that refetches underneath it must not be what the
  // form is reading from.
  const [editing, setEditing] = useState<Transaction | null>(null);
  // Track the cards we have decided by id, never by position: the query
  // refetches after each decision and the decided txn drops out of the list, so
  // an index would skip the card that slid into the vacated slot.
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set());
  // §2.8: no flight at all under `prefers-reduced-motion` — not a shorter one.
  // The card still has to leave the deck, so the hold below goes to zero and the
  // advance is the cut it was before; keeping the 240 ms would make reduced
  // motion the *slower* path, which is the one thing it must never be.
  const reduce = useReducedMotion();

  const items = useMemo(
    () => (queue.data?.items ?? []).filter((t) => !decided.has(t.id)),
    [queue.data, decided],
  );

  const catName = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [categories.data]);

  // The account, because the card has to answer "which account is this credit or
  // debit from?" — the mark carries the hue (from the institution) and the
  // initials (from the name), so the card needs both, not just the name.
  const accountFor = useMemo(() => {
    const m = new Map<string, Account>();
    accounts.data?.forEach((a) => m.set(a.id, a));
    return m;
  }, [accounts.data]);

  // The card on its way out, and which way it went. Held here rather than left
  // to `AnimatePresence` because the card this renders is the card the deck
  // draws: an exit animation the tree owns would have to keep a second copy of
  // the row alive somewhere, and the queue has already stopped returning it.
  const [leaving, setLeaving] = useState<{ txn: Transaction; right: boolean } | null>(null);
  // A ref, not the state: `decide` is memoised on `update` alone, so reading the
  // state here would capture a stale value and the gate would never close.
  const inFlight = useRef(false);

  const decide = useCallback(
    (txn: Transaction, keep: boolean) => {
      // One card in the air at a time. Without this a second ArrowRight during
      // the 240 ms throw re-decides the card already leaving — which is not the
      // card the user is looking at any more, and holding the key would file the
      // queue sight unseen.
      if (inFlight.current) return;
      inFlight.current = true;
      setLeaving({ txn, right: keep });
      // Optimistic, as before: the mutation and the animation start together and
      // neither waits for the other.
      update.mutate({
        id: txn.id,
        body: { review_status: keep ? "reviewed" : "ignored" },
      });
    },
    [update],
  );

  useEffect(() => {
    if (!leaving) return;
    const id = window.setTimeout(() => {
      // The card leaves the deck only now. `decided` is what keeps it gone: the
      // refetch has usually dropped it already, but when it has not, clearing
      // `leaving` alone would bring the card the user just filed straight back.
      setDecided((prev) => {
        const next = new Set(prev);
        next.add(leaving.txn.id);
        return next;
      });
      setLeaving(null);
      inFlight.current = false;
    }, reduce ? 0 : FLY_MS);
    return () => window.clearTimeout(id);
  }, [leaving, reduce]);

  // The card being thrown outranks the head of the queue: the queue no longer
  // contains it, and until the throw lands it is still the card on screen.
  const current = leaving ? leaving.txn : items[0];

  // Opening a card is not deciding it, and the two must not race: while one is
  // in the air `current` is the card already on its way out, so the sheet would
  // open on a transaction the user has stopped looking at.
  const openEditor = useCallback(() => {
    if (inFlight.current || !current) return;
    setEditing(current);
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // The sheet is a modal form over the deck. An arrow key pressed while it
      // is open belongs to the field the user is in — without this, pressing →
      // in the amount box files the card the sheet is open on, behind the sheet,
      // where it cannot be seen to happen.
      if (editing) return;
      // And a key a form control is using is that control's. Review has no
      // fields of its own, so this is the line that matters the day it grows
      // one — which is exactly when nobody will remember to come back here.
      const target = e.target as HTMLElement | null;
      if (target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")) {
        return;
      }
      if (!current) return;
      if (e.key === "ArrowRight") decide(current, true);
      else if (e.key === "ArrowLeft") decide(current, false);
      else if (e.key === "e") openEditor();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, decide, editing, openEditor]);

  const remaining = items.length;

  return (
    // A deck is one card at a time, so it takes §9.1's narrowest width rather
    // than the shell's 1280. At 864 px the card's `justify-between` put the
    // merchant and the amount ~400 px apart with nothing between them; the cap
    // is what stops the shell's new width from making that worse.
    <div className="mx-auto max-w-2xl space-y-6">
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
        <>
          {/* `h-72` (288 px) is measured, not chosen: at 360 px with a merchant
              long enough to wrap three times the card's content comes to 190 px,
              and at 320 px (§8 rule 9's floor) four lines make it ~218. With
              `p-6` that leaves 240 px of box, so the worst realistic card fits
              with slack and none of it is clipped. Nothing here wraps at `lg:`,
              so the same height is what the card keeps — a deck that grew with
              the window would just be the same card with a longer shadow. */}
          <div className="relative h-72" data-testid="review-deck">
            {/* The stack (§4.17): two cards behind the live one, inset and
                pushed down so their bottom edges show. Empty on purpose — see
                the note at the top of the file.

                Painted in reverse order so the nearest card is last and lands
                directly under the live one; the live card is `absolute inset-0`
                and covers all but those edges. `-bottom-4` reaches 16 px into
                the `space-y-6` below, which is where the room for it is. */}
            <div
              aria-hidden="true"
              className="absolute inset-x-4 top-4 -bottom-4 rounded-card border border-border bg-surface-raised"
            />
            <div
              aria-hidden="true"
              className="absolute inset-x-2 top-2 -bottom-2 rounded-card border border-border bg-surface-raised shadow-sm"
            />

            <SwipeCard
              // Keyed on the id so a new card is a new element: the motion value
              // that carries the drag belongs to the card, and reusing the node
              // would hand the next transaction the last one's position.
              key={current.id}
              txn={current}
              account={accountFor.get(current.account_id)}
              categoryName={current.category_id ? catName.get(current.category_id) : undefined}
              onDecide={(keep) => decide(current, keep)}
              onEdit={openEditor}
              // Which way it is on its way out, if it is. Only ever set on the
              // card the deck is actually drawing — see `current` above.
              fly={leaving ? (leaving.right ? "right" : "left") : null}
            />
          </div>

          {/* In flow, not `absolute -bottom-14` as they were. The old buttons
              were positioned outside the deck's own box, so the box reserved no
              space for them and the distance to the card grew with the card;
              worse, they were the primary tap target on the phone and nothing in
              the layout knew they existed. `min-h-11` on both, because these are
              the primary phone interaction and `py-2 text-sm` alone computed to
              36 px (§4.1, §5). */}
          <div className="flex flex-wrap justify-center gap-2 sm:gap-4">
            <button
              onClick={() => decide(current, false)}
              className="inline-flex min-h-11 items-center justify-center rounded-full bg-surface-inset px-4 text-sm text-negative sm:px-6"
              data-testid="review-reject"
            >
              ← Ignore
            </button>
            {/* Between the two decisions and styled as neither: it is the way
                *out* of the row, not a third answer to it. Outline rather than
                a fill, because a filled third button would be read as a third
                verdict — and the two verdicts are the two directions. */}
            <button
              onClick={openEditor}
              className="inline-flex min-h-11 items-center justify-center rounded-full border border-border-strong px-4 text-sm text-fg-muted sm:px-5"
              data-testid="review-edit"
            >
              Edit
            </button>
            <button
              onClick={() => decide(current, true)}
              className="inline-flex min-h-11 items-center justify-center rounded-full bg-accent px-4 text-sm font-medium text-accent-fg sm:px-6"
              data-testid="review-approve"
            >
              Reviewed →
            </button>
          </div>
        </>
      )}

      {/* Outside the deck's own branch, because the sheet is about a card that
          may no longer be the head of the queue: deciding one, or a refetch that
          drops it, must not close a form the user is still typing in.

          `presentation="overlay"` and not the pane — §9.3's Review shape is one
          centred card, and a pane needs a second column to sit in. */}
      {editing && (
        <TxnDetailSheet
          txn={editing}
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          tags={tags.data ?? []}
          presentation="overlay"
          onClose={() => setEditing(null)}
          // In place, not a navigation: the sheet's own re-keying is on the id,
          // so pointing it at the row it already has keeps the form as it is.
          onReplaced={setEditing}
        />
      )}
    </div>
  );
}

function SwipeCard({
  txn,
  account,
  categoryName,
  onDecide,
  onEdit,
  fly,
}: {
  txn: Transaction;
  account?: Account;
  categoryName?: string;
  onDecide: (keep: boolean) => void;
  /** Pull the card up (or press `e`) to open it rather than decide it. */
  onEdit: () => void;
  /** Which way the card is being thrown, or null while it is still the deck. */
  fly: "left" | "right" | null;
}) {
  // The CSS rule in index.css does not reach framer-motion's JS-driven
  // transforms (§2.8). Drag itself stays either way: the card following the
  // finger is the user's own motion, and it is a route to dismissing a card.
  // What goes is the flourish — the rotation, the throw, and the IGNORE/
  // REVIEWED labels fading in with distance. The two buttons below carry the
  // same two words.
  const reduce = useReducedMotion();
  const x = useMotionValue(0);
  // The drag owns both axes, so the vertical one needs a value here too — and
  // it is the same value the drag writes, not a second copy of it, for the same
  // reason the throw rides on `x` (below).
  const y = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-12, 12]);
  const approveOpacity = useTransform(x, [40, 160], [0, 1]);
  const ignoreOpacity = useTransform(x, [-160, -40], [1, 0]);
  // The third gesture's badge, faded by how far up the card has come.
  const liftOpacity = useTransform(y, [-LIFT_TRIGGER, -20], [1, 0]);
  // Opaque for all but the last of the throw, so the card is *moving* right up
  // to the moment it stops being there — a fade that started earlier would read
  // as the card dissolving rather than as the card being sent somewhere.
  const fade = useTransform(x, [-FLY_X, -260, 260, FLY_X], [0, 1, 1, 0]);

  // The throw is driven through the same motion value the drag owns, and that is
  // the whole reason it works: on a wrapper element the drag's own spring would
  // be snapping the card back to centre while the exit carried it off, and for
  // 240 ms the card would visibly fight itself. One value means the second
  // animation simply takes the first one's place.
  useEffect(() => {
    if (!fly || reduce) return;
    animate(x, fly === "right" ? FLY_X : -FLY_X, { duration: FLY_MS / 1000, ease: "easeIn" });
  }, [fly, x, reduce]);

  return (
    <motion.div
      className="absolute inset-0 flex cursor-grab flex-col rounded-card bg-surface-raised p-6 shadow-lg active:cursor-grabbing"
      style={{ x, y, rotate: reduce ? 0 : rotate, opacity: fade }}
      // Not draggable once it is on its way out: catching the card mid-throw
      // would only be a fight with the animation that is throwing it.
      drag={fly ? false : true}
      // Both axes, and each pinned in the direction that means nothing: a card
      // cannot be pushed right-to-left out of the deck's own box, and it cannot
      // be pushed *down* at all. `top` is how far the lift gesture can travel.
      dragConstraints={{ left: 0, right: 0, top: -LIFT_PX, bottom: 0 }}
      dragElastic={0.6}
      onDragEnd={(_e, info) => {
        const { x: dx, y: dy } = info.offset;
        // Up is tested first and has to *dominate*, not merely be present: a
        // diagonal drag is ambiguous, and the gesture that cannot be taken back
        // is the one to be conservative about.
        if (dy < -LIFT_TRIGGER && Math.abs(dy) > Math.abs(dx)) onEdit();
        else if (dx > SWIPE_X) onDecide(true);
        else if (dx < -SWIPE_X) onDecide(false);
      }}
      data-testid="swipe-card"
    >
      {/* The two verdicts are overlays in the corners, not a row of their own
          above the merchant. That row reserved 26 px of height, on every card
          and at all times, for two labels that are invisible until the card is
          dragged — and it did it by pushing the identity block down, so the one
          thing the card is for sat off-centre behind a gap. */}
      <motion.span
        style={{ opacity: reduce ? 0 : ignoreOpacity }}
        className="absolute left-6 top-6 rounded-control border border-negative px-2 py-1 text-xs text-negative"
      >
        IGNORE
      </motion.span>
      <motion.span
        style={{ opacity: reduce ? 0 : approveOpacity }}
        className="absolute right-6 top-6 rounded-control border border-accent px-2 py-1 text-xs text-accent"
      >
        REVIEWED
      </motion.span>
      {/* The third gesture, centred between the two verdicts and only ever
          visible when neither of them is: a card being pulled up is at x≈0, so
          the three badges cannot be on screen together. */}
      <motion.span
        style={{ opacity: reduce ? 0 : liftOpacity }}
        className="absolute left-1/2 top-6 -translate-x-1/2 rounded-control border border-border-strong px-2 py-1 text-xs text-fg-muted"
      >
        OPEN
      </motion.span>

      {/* `flex-1 justify-center`: the identity block takes the room the amount
          leaves and centres itself in it, so a one-line merchant sits on the
          card's optical centre instead of clinging to the top of a 288 px box
          with 200 px of nothing under it. */}
      <div className="flex min-h-0 flex-1 flex-col justify-center">
        <p className="text-xl font-semibold">{txn.merchant || txn.description || "(no description)"}</p>
        {/* Which account it came out of, above the date: on a decision card that
            is often the thing that decides it, and it is the one question the
            name alone cannot answer ("Chase Checking" or "Chase Savings"?). */}
        {account && (
          <p className="mt-1 flex min-w-0 items-center gap-2">
            <AccountMark name={account.name} institution={account.institution} size="md" />
            <span className="truncate text-sm text-fg-muted">{account.name}</span>
          </p>
        )}
        <p className="text-sm text-fg-muted">
          {/* `medium` rather than the row's `compact`: the card is not competing
              for width, and on a card that is about to be filed under a month,
              "Sep 20" says more than "Yesterday". */}
          <Day value={txn.transacted_at} />
          {categoryName && ` · ${categoryName}`}
        </p>
      </div>

      <p className={`text-2xl font-semibold ${Number(txn.amount) < 0 ? "text-fg" : "text-positive"}`}>
        {formatMoney(txn.amount, txn.currency)}
      </p>
    </motion.div>
  );
}
