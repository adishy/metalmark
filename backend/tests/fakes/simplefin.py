"""Constructed SimpleFIN scenarios, built by transforming the real capture.

The distinction this module exists to preserve
(``tests/fixtures/simplefin/README.md``): ``demo_capture.json`` is **captured**
and proves the wire format; everything here is **constructed** and encodes our
*behavioural* assumptions about it. Each builder says which assumption it
encodes, because a fixture whose purpose is unstated is a fixture nobody dares
change.

Three things the capture does *not* contain, and so cannot prove, which is why
they are constructed:

* **No pending transactions.** The demo bridge never emits ``posted == 0``, so
  every pending scenario is built here.
* **No reconnects.** The demo's ids are stable across a re-claim, so the
  id-instability case is simulated rather than observed.
* **No auth failures.** ``con.auth`` is a documented code, never a captured one.

Builders return ``(first_fetch, second_fetch)`` pairs where the scenario is
about change over time, so a test can script them directly:

    provider = FakeProvider(script=list(pending_then_posted_same_id()))
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from app.services.aggregator import (
    AccountSet,
    ProviderAccount,
    ProviderTransaction,
)
from app.services.fake_simplefin import load_demo_capture

#: The demo account with transactions in it. "Demo Empty Account" has none, and
#: is the case that proves an account with no rows is still created.
DEMO_SAVINGS = "SimpleFIN Savings"
DEMO_CHECKING = "SimpleFIN Checking"
DEMO_EMPTY = "SimpleFIN Empty Account"

# --- errlist samples -------------------------------------------------------
#
# The first two are CAPTURED: the bridge answers a too-long date range with HTTP
# 200 and one of these, verbatim, in `errlist`. They are reproduced here in
# already-flattened form (`"code: msg"`), which is what `parse_accounts_payload`
# produces.
#
# The auth one is CONSTRUCTED. The `con.auth` code prefix is from the published
# spec; the message text is invented, because a real revoked credential is not
# something the demo bridge will produce on request. Do not read its wording as
# normative — only its code.
WARN_WINDOW_CAPPED = (
    "gen.api: Requested date range exceeds limit of 90 days and was capped."
)
WARN_WINDOW_RECOMMENDED = (
    "gen.api: Requested date range exceeds recommended range of 45 days. "
    "In the future, this may be capped."
)
ERR_AUTH = "con.auth: Access to this connection has been revoked."


def demo() -> AccountSet:
    """The committed capture, parsed by the production parser."""
    return load_demo_capture()


def scenario(*sets: AccountSet) -> list[AccountSet]:
    """A ``FakeProvider`` script from a sequence of fetches, in order."""
    return list(sets)


# --- low-level transforms --------------------------------------------------


def account_named(account_set: AccountSet, name: str) -> ProviderAccount:
    for account in account_set.accounts:
        if account.name == name:
            return account
    raise AssertionError(f"no account named {name!r} in this set")


def replace_account(
    account_set: AccountSet, updated: ProviderAccount
) -> AccountSet:
    """Swap one account out, matched by name, keeping everything else."""
    accounts = tuple(
        updated if a.name == updated.name else a for a in account_set.accounts
    )
    return dataclasses.replace(account_set, accounts=accounts)


def with_transactions(
    account_set: AccountSet, name: str, transactions: list[ProviderTransaction]
) -> AccountSet:
    account = account_named(account_set, name)
    return replace_account(
        account_set, dataclasses.replace(account, transactions=tuple(transactions))
    )


def with_errlist(account_set: AccountSet, *entries: str) -> AccountSet:
    """Attach ``errlist`` entries — how the bridge reports a warning on a 200."""
    return dataclasses.replace(account_set, errlist=tuple(entries))


def with_org_name(account_set: AccountSet, name: str) -> AccountSet:
    """The institution renamed, at the level ``AccountSet.org_name`` reads.

    The parser flattens ``org_name`` onto each account as well, from the
    connection its ``conn_id`` points at. This changes **only** the connection, so
    ``AccountSet.org_name`` moves while each account's ``external_key`` input does
    not — which is what makes it useful for testing what the *connection* records.
    A real rename moves both, and that is a separate question (the accounts would
    rekey) deliberately not entangled with this one.
    """
    connections = tuple(
        dataclasses.replace(c, org_name=name) for c in account_set.connections
    )
    return dataclasses.replace(account_set, connections=connections)


def reidentified(account_set: AccountSet, *, salt: str = "b") -> AccountSet:
    """Every transaction gets a new id; amounts, payees and dates are untouched.

    This is the demo bridge's **observed** behaviour, not a hypothetical: two
    fetches moments apart returned identical amounts and descriptions shifted
    one day forward, with every id re-minted (fixture README, correction 6).

    A connection pointed at the live demo bridge therefore accumulates
    duplicates, because the ids genuinely change and ``(account_id,
    external_id)`` is the dedupe key. That is a property of the demo, and it is
    exactly why CI must sync against frozen fixtures instead of the live bridge.

    Use this to prove the opposite of a dup: that a *reconnect* — where the
    account keeps its name and institution — loses zero transactions and creates
    zero dupes even though every id moved.
    """
    accounts = []
    for account in account_set.accounts:
        transactions = tuple(
            dataclasses.replace(t, external_id=f"{salt}-{t.external_id}")
            for t in account.transactions
        )
        accounts.append(dataclasses.replace(account, transactions=transactions))
    return dataclasses.replace(account_set, accounts=tuple(accounts))


def shifted(account_set: AccountSet, days: int) -> AccountSet:
    """Every transaction moved ``days`` forward, ids preserved.

    The other half of the demo's behaviour: the data tracks *now*, so a fetch
    tomorrow returns the same rows dated a day later. With ids preserved this
    must update in place; with ids re-minted it must not duplicate.
    """
    delta = timedelta(days=days)
    accounts = []
    for account in account_set.accounts:
        transactions = tuple(
            dataclasses.replace(
                t,
                transacted_at=t.transacted_at + delta,
                posted_at=(t.posted_at + delta) if t.posted_at else None,
            )
            for t in account.transactions
        )
        accounts.append(dataclasses.replace(account, transactions=transactions))
    return dataclasses.replace(account_set, accounts=tuple(accounts))


# --- pending scenarios -----------------------------------------------------


def make_pending(txn: ProviderTransaction) -> ProviderTransaction:
    """Mark a transaction pending — i.e. clear ``posted_at``.

    ``posted_at is None`` is the *only* pending signal; the wire format has no
    ``pending`` field, and the failing value is ``posted: 0``.
    """
    return dataclasses.replace(txn, posted_at=None)


def _first_txn(account_set: AccountSet, name: str = DEMO_SAVINGS) -> ProviderTransaction:
    return account_named(account_set, name).transactions[0]


def _drop(account_set: AccountSet, txn_id: str, name: str = DEMO_SAVINGS) -> AccountSet:
    account = account_named(account_set, name)
    remaining = [t for t in account.transactions if t.external_id != txn_id]
    return with_transactions(account_set, name, remaining)


def pending_then_posted_same_id() -> tuple[AccountSet, AccountSet]:
    """The common case: a pending charge posts, keeping its provider id.

    Assumption: the bridge keeps the id across posting. Sync must recognise it
    and update the existing row in place — not insert a second one, and not
    lose a category the human already set on the pending row.
    """
    first = demo()
    pending = make_pending(_first_txn(first))
    return with_transactions(first, DEMO_SAVINGS, [pending, *_rest(first)]), demo()


def pending_then_posted_new_id() -> tuple[AccountSet, AccountSet]:
    """A pending charge posts under a **different** id.

    Assumption: this is what banks do, and ADR-0019 exists for it. Sync must
    match on amount + a date window + a loose description, adopt the new id, and
    leave the human's edits on the row intact.
    """
    first, second = pending_then_posted_same_id()
    original = _first_txn(first)
    reposted = dataclasses.replace(
        original, external_id=f"new-{original.external_id}", posted_at=original.transacted_at
    )
    return first, with_transactions(second, DEMO_SAVINGS, [reposted, *_rest(second)])


def pending_then_posted_late(*, now: datetime) -> tuple[AccountSet, AccountSet]:
    """A pending charge that settles a few days after it was first reported.

    The scenario that makes the fetch window load-bearing — and the reason the
    *clock* is what moves, not the transaction. A bank posts a pending charge on
    or before the date it first showed it; nothing about the row travels forward
    in time. What happens is that sync goes quiet and comes back later, so the
    caller must sync the second fetch at ``now + LATE_DAYS``. If it syncs again at
    ``now`` instead, the pending row is still inside any window at all and the
    test proves nothing about the window — which is why the return value is the
    pair and the caller owns the clock.

    **The dates are placed relative to ``now`` rather than taken from the
    capture.** This scenario is about a window that reaches back a *known* number
    of days, so a row dated from whenever the capture happened to be taken drifts
    out of that reach as the pinned clock and the frozen capture diverge — and the
    test then asserts a reconciliation the bridge's own cap makes impossible. That
    is not hypothetical: dated from the capture, the posting here sat 95 days back
    and only reconciled because the lookback ceiling used to be a year.

    The posting carries a **new id**, two days after the pending date, so it has
    to be matched on amount, date and description rather than recognised: a fetch
    window anchored to a fixed ``now - 3d`` would miss it entirely, and a matcher
    that only looked at the exact date would refuse it.
    """
    first, second = pending_then_posted_same_id()
    original = _first_txn(first)
    settled_at = now + timedelta(days=2)

    first = with_transactions(
        first,
        DEMO_SAVINGS,
        [dataclasses.replace(original, transacted_at=now), *_rest(first)],
    )
    reposted = dataclasses.replace(
        original,
        external_id=f"new-{original.external_id}",
        transacted_at=settled_at,
        posted_at=settled_at,
    )
    return first, with_transactions(second, DEMO_SAVINGS, [reposted, *_rest(second)])


def pending_then_two_lookalikes() -> tuple[AccountSet, AccountSet]:
    """The pending charge posts, and a *second* new row looks identical to it.

    Two identical coffees on the same day, both arriving in one payload. What sync
    actually does here is worth stating precisely, because the interesting part is
    not that it refuses:

    The rows are examined in order, and the first one resolves against the pending
    row before the second exists in the ledger — so the first **adopts** it and the
    second inserts. The tally comes out right (two charges in, two rows out), the
    pending row is gone rather than stranded, and a human's category on the pending
    charge lands on one of the two real ones. Since the two are identical in
    amount, date and description, which of them inherits it is not a distinction
    anybody can observe — and the alternative, refusing to adopt at all, strands a
    pending row that then expires and leaves *three* rows for two charges.

    So this is the case for adopting, not against it. The case *against* — two
    ledger rows that both match one incoming transaction — is
    ``two_pendings_one_posting``.
    """
    first, second = pending_then_posted_new_id()
    original = _first_txn(first)
    reposted = dataclasses.replace(
        original, external_id=f"new-{original.external_id}", posted_at=original.transacted_at
    )
    twin = dataclasses.replace(reposted, external_id=f"twin-{original.external_id}")
    return first, with_transactions(second, DEMO_SAVINGS, [reposted, twin, *_rest(second)])


def two_pendings_one_posting() -> tuple[AccountSet, AccountSet]:
    """**Two** unsettled rows that one incoming transaction could equally be.

    The literal "exactly one candidate, or none" rule — and this time the ambiguity
    is genuinely visible to the matcher, because both rows are already in the
    ledger when the posting arrives. Neither may be adopted: picking one would
    rewrite an arbitrary purchase into another, and no fact available distinguishes
    them.

    Both pendings therefore stay unsettled and expire on their own TTL, which is
    the visible failure ADR-0019 prefers over the silent one.
    """
    first = demo()
    base = _first_txn(first)
    twins = (
        make_pending(base),
        make_pending(dataclasses.replace(base, external_id=f"twin-{base.external_id}")),
    )
    rest = _rest(first)
    posted = dataclasses.replace(base, external_id=f"new-{base.external_id}")
    return (
        with_transactions(first, DEMO_SAVINGS, [*twins, *rest]),
        with_transactions(first, DEMO_SAVINGS, [posted, *rest]),
    )


def pending_then_gone() -> tuple[AccountSet, AccountSet]:
    """A pending charge that never posts and never returns.

    The phantom. After the TTL sync expires it — deleting the row if it is
    untouched, but keeping it and flagging it for review if a human has since
    categorised it. Deleting a row a human worked on is the data loss ADR-0007
    exists to prevent.
    """
    first = demo()
    pending = make_pending(_first_txn(first))
    return with_transactions(first, DEMO_SAVINGS, [pending, *_rest(first)]), _drop(
        first, pending.external_id
    )


def _rest(account_set: AccountSet, name: str = DEMO_SAVINGS) -> list[ProviderTransaction]:
    return list(account_named(account_set, name).transactions[1:])


def empty_fetch() -> AccountSet:
    """A successful fetch that returned no accounts at all.

    Distinct from a *failure*: this is a 200 with nothing in it, which must not
    be read as "every account disappeared".
    """
    return AccountSet()


def at(moment: datetime) -> datetime:
    """Pin a datetime to aware UTC, for constructing windows in tests."""
    return moment.replace(tzinfo=UTC)


__all__ = [
    "DEMO_CHECKING",
    "DEMO_EMPTY",
    "DEMO_SAVINGS",
    "ERR_AUTH",
    "WARN_WINDOW_CAPPED",
    "WARN_WINDOW_RECOMMENDED",
    "account_named",
    "at",
    "demo",
    "empty_fetch",
    "make_pending",
    "pending_then_gone",
    "pending_then_posted_late",
    "pending_then_posted_new_id",
    "pending_then_posted_same_id",
    "pending_then_two_lookalikes",
    "reidentified",
    "replace_account",
    "scenario",
    "shifted",
    "two_pendings_one_posting",
    "with_errlist",
    "with_org_name",
    "with_transactions",
]
