"""Sync's whole life: window, accounts, ingest, reconcile, expire, remap.

Everything here runs against the **committed demo capture** through
``FakeProvider``, so the wire format under test is the real one and the network
is not involved. The scenarios a capture cannot contain — pending rows,
reconnects, auth failures — are constructed in ``tests/fakes/simplefin.py``, each
naming the assumption it encodes.

The properties worth protecting are the ones a plausible-looking refactor would
quietly break: that a re-sync writes nothing at all, that a human's edits outlive
a posting, that a reconnect loses nothing, and that ambiguity inserts a visible
row instead of silently merging two charges.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.db import scoped_session
from app.models import (
    Account,
    AccountConnection,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    SyncRun,
    SyncRunEvent,
    Transaction,
    TransferGroup,
)
from app.schemas.rules import RuleActions, RuleConditions, RuleCreate
from app.schemas.transactions import TransactionCreate, TransactionUpdate
from app.security.crypto import SecretBox
from app.services import rules, sync
from app.services import transactions as txns
from app.services.aggregator import FetchStats, ProviderError, ProviderTransaction
from app.services.fake_simplefin import FAKE_ACCESS_URL, FakeProvider
from app.settings import get_settings
from tests.fakes import simplefin as scenarios

pytestmark = pytest.mark.integration

D = Decimal

#: Pinned to the day after the capture was taken, not to ``datetime.now()``. The
#: capture's dates are frozen; a window test that compared them against the wall
#: clock would pass today and fail next year, for reasons that have nothing to do
#: with the code under test.
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

#: How long sync goes quiet before the late-posting test looks again. Comfortably
#: longer than ``OVERLAP_DAYS`` and than the pending matcher's own window, which is
#: the point: neither can be what makes the row reconcile.
LATE_DAYS = 9


# ---- helpers --------------------------------------------------------------


async def _make_connection(household_id, *, org_name="SimpleFIN Bridge") -> uuid.UUID:
    """A connection whose credential is encrypted exactly as the API stores it."""
    async with scoped_session(household_id) as session:
        connection = AccountConnection(
            household_id=household_id,
            provider="fake",
            access_url_encrypted=SecretBox(get_settings().secret_key).encrypt(FAKE_ACCESS_URL),
            org_name=org_name,
        )
        session.add(connection)
        await session.flush()
        return connection.id


async def _sync(household_id, connection_id, provider, *, now=NOW, fence=None):
    return await sync.run_connection_sync(
        household_id, connection_id, provider=provider, now=now, fence=fence
    )


async def _txns(session) -> list[Transaction]:
    return list(
        (
            await session.execute(
                select(Transaction).order_by(
                    Transaction.transacted_at, Transaction.external_id
                )
            )
        ).scalars().all()
    )


async def _accounts(session) -> dict[str, Account]:
    return {a.name: a for a in (await session.execute(select(Account))).scalars().all()}


async def _events(session, run_id) -> list[SyncRunEvent]:
    return list(
        (
            await session.execute(
                select(SyncRunEvent)
                .where(SyncRunEvent.sync_run_id == run_id)
                .order_by(SyncRunEvent.seq)
            )
        ).scalars().all()
    )


async def _run_row(session, run_id) -> SyncRun:
    return (await session.execute(select(SyncRun).where(SyncRun.id == run_id))).scalar_one()


async def _connection_row(session, connection_id) -> AccountConnection:
    return (
        await session.execute(
            select(AccountConnection).where(AccountConnection.id == connection_id)
        )
    ).scalar_one()


async def _the_pending_row(session) -> Transaction:
    """The one still-unsettled row. Every pending scenario has exactly one."""
    rows = (
        await session.execute(select(Transaction).where(Transaction.is_pending.is_(True)))
    ).scalars().all()
    assert len(rows) == 1, f"expected exactly one pending row, found {len(rows)}"
    return rows[0]


async def _fresh_category(session, household_id) -> uuid.UUID:
    group = CategoryGroup(household_id=household_id, name="Everyday", type="expense")
    session.add(group)
    await session.flush()
    category = Category(household_id=household_id, group_id=group.id, name="Groceries")
    session.add(category)
    await session.flush()
    return category.id


def _payload(legs: dict[str, list[ProviderTransaction]]):
    """The capture with exactly these transactions on each demo account.

    Blanking the accounts the test does not care about is what keeps a transfer
    assertion about *the two legs I built* rather than about whatever the demo
    happens to contain — otherwise a pair inside the capture could match and the
    count would be right for the wrong reason.
    """
    account_set = scenarios.demo()
    for name in (scenarios.DEMO_SAVINGS, scenarios.DEMO_CHECKING, scenarios.DEMO_EMPTY):
        account_set = scenarios.with_transactions(account_set, name, legs.get(name, []))
    return account_set


@pytest.fixture
async def hh(household_factory):
    return await household_factory()


# ---- the happy path -------------------------------------------------------


async def test_a_first_sync_creates_one_account_per_provider_account(hh) -> None:
    connection_id = await _make_connection(hh)
    outcome = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )

    assert outcome.status == "ok"
    async with scoped_session(hh) as session:
        accounts = await _accounts(session)
        assert set(accounts) == {
            scenarios.DEMO_SAVINGS,
            scenarios.DEMO_CHECKING,
            scenarios.DEMO_EMPTY,
        }
        assert outcome.counts.accounts_seen == 3
        assert outcome.counts.accounts_created == 3
        assert outcome.counts.txns_inserted == 12  # six per account; the empty one has none
        assert outcome.counts.txns_updated == 0
        assert outcome.counts.txns_reconciled == 0

        # A synced account is not a manual one, and it carries an owner from birth
        # so the attribution chain terminates instead of needing a null case.
        for account in accounts.values():
            assert account.is_manual is False
            assert account.owner_id is not None
            assert account.external_key is not None
            assert account.connection_id == connection_id


async def test_the_account_type_is_inferred_from_holdings_not_the_name(hh) -> None:
    """The capture's trap: "SimpleFIN Savings" holds 550 shares of AAPL.

    Trusting the name would file a brokerage balance under ``depository`` forever.
    """
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())))

    async with scoped_session(hh) as session:
        accounts = await _accounts(session)
        assert accounts[scenarios.DEMO_SAVINGS].type == "investment"
        assert accounts[scenarios.DEMO_CHECKING].type == "depository"


async def test_a_re_sync_inserts_nothing_and_updates_nothing(hh) -> None:
    """The idempotency bar, stated exactly: both counters are zero.

    Not "no duplicates" — a re-sync that rewrote every row with identical values
    would pass that and still be wrong, because it would re-run rules and re-stamp
    provenance on rows nobody asked about.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo(), scenarios.demo()))

    first = await _sync(hh, connection_id, provider)
    second = await _sync(hh, connection_id, provider)

    assert first.counts.txns_inserted == 12
    assert second.counts.txns_inserted == 0
    assert second.counts.txns_updated == 0
    assert second.counts.txns_reconciled == 0
    assert second.counts.accounts_created == 0
    assert second.counts.accounts_remapped == 0
    assert second.counts.rules_applied == 0
    async with scoped_session(hh) as session:
        assert len(await _txns(session)) == 12


async def test_the_same_provider_id_on_two_accounts_stays_two_rows(hh) -> None:
    """Fixture README correction 5, as a live assertion.

    The demo gives Savings and Checking the *same* transaction ids. Anything that
    looked a transaction up by ``external_id`` alone would find the other account's
    row and overwrite it, and the ledger would quietly hold half the transactions
    it was sent.
    """
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())))

    async with scoped_session(hh) as session:
        accounts = await _accounts(session)
        by_account: dict[uuid.UUID, list[Transaction]] = {}
        rows = await _txns(session)
        for row in rows:
            by_account.setdefault(row.account_id, []).append(row)

        savings = {t.external_id for t in by_account[accounts[scenarios.DEMO_SAVINGS].id]}
        checking = {t.external_id for t in by_account[accounts[scenarios.DEMO_CHECKING].id]}
        assert savings == checking  # the trap is actually present in the fixture
        assert len(rows) == 12  # and both sets of rows survived it


# ---- provenance -----------------------------------------------------------


async def test_a_human_value_survives_a_re_sync(hh) -> None:
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo(), scenarios.demo()))
    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        row_id = (await _txns(session))[0].id
        await txns.update_transaction(
            session, hh, row_id, TransactionUpdate(description="My own words")
        )

    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.id == row_id))
        ).scalar_one()
        assert row.description == "My own words"
        assert row.field_sources["description"] == "user"


async def test_a_rule_may_improve_a_provider_value_and_the_provider_cannot_take_it_back(
    hh,
) -> None:
    """The "a rule may improve a provider value" acceptance bar.

    ``merchant`` is written from the provider's ``payee`` on insert and marked
    ``provider`` — the weakest origin, so a rule outranks it. Once the rule has
    written, the next sync must leave it alone: ADR-0007's precedence is
    ``user > rule > provider``, and a provider write that could undo a rule would
    make the ordering a one-way trip.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo(), scenarios.demo()))
    async with scoped_session(hh) as session:
        await rules.create_rule(
            session, hh,
            RuleCreate(
                name="Tidy the fishing shop",
                conditions=RuleConditions(merchant_contains="Fishin"),
                actions=RuleActions(rename_merchant="John's Fishin Shack (tidy)"),
            ),
        )

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        improved = [
            t for t in await _txns(session)
            if (t.field_sources or {}).get("merchant") == "rule"
        ]
        assert improved, "the rule should have claimed the provider's merchant value"
        assert {t.merchant for t in improved} == {"John's Fishin Shack (tidy)"}
        improved_ids = [t.id for t in improved]

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        for txn_id in improved_ids:
            fresh = (
                await session.execute(select(Transaction).where(Transaction.id == txn_id))
            ).scalar_one()
            assert fresh.merchant == "John's Fishin Shack (tidy)"
            assert fresh.field_sources["merchant"] == "rule"


# ---- pending → posted -----------------------------------------------------


async def test_a_pending_charge_posts_under_the_same_id_and_keeps_the_human_category(
    hh,
) -> None:
    """The common case, and the one that must not lose work.

    A human categorizes a pending charge; the bank then posts it. The row is
    *updated*, not replaced — so the category is not carried forward by a copy
    step, it simply was never anywhere else. There is deliberately no copy step to
    get wrong.
    """
    first, second = scenarios.pending_then_posted_same_id()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        category_id = await _fresh_category(session, hh)
        row_id = (await _the_pending_row(session)).id
        await txns.update_transaction(
            session, hh, row_id, TransactionUpdate(category_id=category_id)
        )

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.counts.txns_reconciled == 0  # same id: an update, not a reconciliation
    assert outcome.counts.txns_updated == 1
    assert outcome.counts.txns_inserted == 0
    async with scoped_session(hh) as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.id == row_id))
        ).scalar_one()
        assert row.is_pending is False
        assert row.pending_since is None
        assert row.category_id == category_id
        assert row.field_sources["category"] == "user"


async def test_a_pending_charge_posts_under_a_new_id_and_adopts_it(hh) -> None:
    """ADR-0019's whole reason for existing.

    The bank re-mints the id when the charge settles. The row must be recognised by
    amount, date and description — and *stay the same row*, so the human's category
    is untouched.
    """
    first, second = scenarios.pending_then_posted_new_id()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        category_id = await _fresh_category(session, hh)
        row_id = (await _the_pending_row(session)).id
        await txns.update_transaction(
            session, hh, row_id, TransactionUpdate(category_id=category_id)
        )

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.counts.txns_reconciled == 1
    assert outcome.counts.txns_inserted == 0
    async with scoped_session(hh) as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.id == row_id))
        ).scalar_one()
        assert row.external_id.startswith("new-")  # the id was adopted
        assert row.is_pending is False
        assert row.pending_since is None
        assert row.category_id == category_id
        assert len(await _txns(session)) == 12  # no duplicate was inserted


async def test_a_late_posting_reconciles_and_the_window_is_why(hh) -> None:
    """The plan's item 3: this must not pass for the wrong reason.

    ``FakeProvider`` ignores ``start``, so a second sync at ``NOW`` would reconcile
    this whatever the window did — the payload is served regardless. Two things
    make the test mean something: the clock really moves (``LATE_DAYS``, past both
    the overlap window and the pending TTL, so only the anchor can bring this row
    home), and the run's *own* window is asserted to have reached back to it. A
    fixed ``now - 3d`` computed at the later clock starts days after the charge
    settled and would never have seen the posting at all.

    The final assertion names the row by id rather than counting unsettled ones. A
    count of zero would also be true if the row had been *expired and deleted*, so
    it would go on passing while quietly testing the wrong path.
    """
    first, second = scenarios.pending_then_posted_late(now=NOW)
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        pending_id = (await _the_pending_row(session)).id

    late_posting = scenarios.account_named(second, scenarios.DEMO_SAVINGS).transactions[0]
    moved_clock = NOW + timedelta(days=LATE_DAYS)
    assert late_posting.transacted_at < moved_clock - timedelta(days=3), (
        "the posting must sit outside a fixed three-day window at the later "
        "clock, or this test proves nothing about the window"
    )

    outcome = await _sync(hh, connection_id, provider, now=moved_clock)

    assert outcome.counts.txns_reconciled == 1
    assert outcome.counts.txns_inserted == 0  # adopted, not duplicated
    async with scoped_session(hh) as session:
        events = await _events(session, outcome.run_id)
        window = next(e for e in events if e.event == "window.computed")
        assert datetime.fromisoformat(window.detail["start"]) <= late_posting.transacted_at
        assert window.detail["pending_anchors"] >= 1
        settled = (
            await session.execute(select(Transaction).where(Transaction.id == pending_id))
        ).scalar_one()
        assert settled.is_pending is False
        assert settled.external_id == late_posting.external_id


async def test_two_lookalikes_adopt_one_and_insert_the_other(hh) -> None:
    """The tally stays right even though the two rows are indistinguishable.

    The two incoming rows are not ambiguous *to the matcher*, and cannot be: they
    arrive in sequence, so the first one resolves against the pending row before
    the second is ever considered. The assertion below is therefore not "it
    refused" — it is that two charges produced two settled rows and stranded
    nothing. Which of the two inherits a human's category on the pending charge is
    not a fact anyone can observe, because the two are identical in every field a
    person could look at.
    """
    first, second = scenarios.pending_then_two_lookalikes()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        pending = await _the_pending_row(session)
        assert pending.pending_since == NOW  # the TTL clock, stamped on first sight
        pending_id = pending.id
        before = len(await _txns(session))

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.counts.txns_reconciled == 1  # the first lookalike took the id
    assert outcome.counts.txns_inserted == 1  # the second is a charge of its own
    original = scenarios.account_named(scenarios.demo(), scenarios.DEMO_SAVINGS)
    async with scoped_session(hh) as session:
        adopted = (
            await session.execute(select(Transaction).where(Transaction.id == pending_id))
        ).scalar_one()
        assert adopted.is_pending is False
        assert adopted.external_id == f"new-{original.transactions[0].external_id}"
        # Two charges arrived; exactly one row was added. The pending row became one
        # of them rather than being stranded alongside them.
        assert len(await _txns(session)) == before + 1


async def test_two_pendings_cannot_both_claim_one_posting(hh) -> None:
    """The literal "exactly one candidate" rule, where the ambiguity is real.

    Both unsettled rows are already in the ledger when the posting arrives, and
    nothing distinguishes them: same account, same amount, same date, same
    description. Adopting either would rewrite an arbitrary purchase into another,
    so sync refuses and inserts — and both pendings stay as they were, to expire on
    their own TTL. That is the visible failure ADR-0019 prefers over the silent one.
    """
    first, second = scenarios.two_pendings_one_posting()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        pendings = (
            await session.execute(select(Transaction).where(Transaction.is_pending.is_(True)))
        ).scalars().all()
        assert len(pendings) == 2
        pending_ids = {t.id for t in pendings}

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.counts.txns_reconciled == 0
    assert outcome.counts.txns_inserted == 1  # the posting became a row of its own
    async with scoped_session(hh) as session:
        still = (
            await session.execute(
                select(Transaction).where(Transaction.id.in_(pending_ids))
            )
        ).scalars().all()
        assert len(still) == 2
        assert all(t.is_pending for t in still)  # neither was claimed


async def test_a_manual_row_is_never_a_pending_match(hh) -> None:
    """ADR-0019's manual-origin boundary, as a fact about the query.

    The row built here is *identical* to the pending one in everything the matcher
    looks at — same account, amount, date, description, and unsettled — so only the
    predicate can keep it out. A human marking a charge pending by hand is something
    ``PATCH`` allows, so this is a row the ledger can really contain.
    """
    first, second = scenarios.pending_then_posted_new_id()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))
    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        pending = await _the_pending_row(session)
        pending_id, account_id = pending.id, pending.account_id
        amount, when, description = pending.amount, pending.transacted_at, pending.description
        await txns.create_transaction(
            session, hh,
            TransactionCreate(
                account_id=account_id,
                amount=amount,
                transacted_at=when,
                description=description,
                is_pending=True,
            ),
        )
        hand = [
            t for t in await _txns(session)
            if t.external_id is None
        ]
        assert len(hand) == 1
        hand_id = hand[0].id

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.counts.txns_reconciled == 1
    async with scoped_session(hh) as session:
        hand_now = (
            await session.execute(select(Transaction).where(Transaction.id == hand_id))
        ).scalar_one()
        assert hand_now.external_id is None  # still a manual row, untouched
        assert hand_now.is_pending is True
        pending_now = (
            await session.execute(select(Transaction).where(Transaction.id == pending_id))
        ).scalar_one()
        assert pending_now.is_pending is False  # the *provider* row reconciled


# ---- expiry ---------------------------------------------------------------


async def test_a_phantom_pending_is_deleted_after_the_ttl(hh) -> None:
    first, second = scenarios.pending_then_gone()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))

    await _sync(hh, connection_id, provider)
    async with scoped_session(hh) as session:
        row_id = (await _the_pending_row(session)).id

    # Past the TTL, so the unsettled row no longer holds it back from expiry.
    outcome = await _sync(hh, connection_id, provider, now=NOW + timedelta(days=8))

    assert outcome.counts.pendings_expired == 1
    async with scoped_session(hh) as session:
        assert (
            await session.execute(select(Transaction).where(Transaction.id == row_id))
        ).scalar_one_or_none() is None
        expired = [e.event for e in await _events(session, outcome.run_id) if "expired" in e.event]
        assert expired == ["pending.expired"]  # deleted, not kept: nobody had edited it


async def test_a_phantom_a_human_edited_is_kept_and_sent_to_review(hh) -> None:
    """Deleting work someone did is the loss ADR-0007/0019 exist to prevent.

    The cost of keeping the row is one review-queue entry; the cost of deleting it
    is a categorized transaction that silently ceases to exist.
    """
    first, second = scenarios.pending_then_gone()
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(first, second))
    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        category_id = await _fresh_category(session, hh)
        row_id = (await _the_pending_row(session)).id
        await txns.update_transaction(
            session, hh, row_id, TransactionUpdate(category_id=category_id)
        )

    outcome = await _sync(hh, connection_id, provider, now=NOW + timedelta(days=8))

    assert outcome.counts.pendings_expired == 1
    async with scoped_session(hh) as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.id == row_id))
        ).scalar_one()
        assert row.is_pending is False
        assert row.review_status == "needs_review"
        assert row.category_id == category_id  # the human's work is intact
        expired = [e for e in await _events(session, outcome.run_id) if "expired" in e.event]
        assert [e.event for e in expired] == ["pending.expired_kept"]


# ---- reconnect (ADR-0009) -------------------------------------------------


async def test_a_reconnect_with_new_ids_loses_nothing_and_creates_nothing(hh) -> None:
    """The M2 acceptance bar, end to end.

    The connection row is deleted and re-added — which is what a reconnect *is* —
    and every provider id has changed, accounts and transactions both. The accounts
    are recognised by their reconnect key, and the rows hanging off them are
    re-pointed by content, because ``(account_id, external_id)`` has stopped
    identifying anything and there is nothing else left to match on.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo()))
    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        original_ids = {a.name: a.id for a in (await _accounts(session)).values()}
        before = len(await _txns(session))
        assert before == 12

    async with scoped_session(hh) as session:
        await session.delete(await _connection_row(session, connection_id))
    new_connection_id = await _make_connection(hh)

    outcome = await _sync(
        hh, new_connection_id,
        FakeProvider(script=scenarios.scenario(scenarios.reidentified(scenarios.demo()))),
    )

    assert outcome.counts.accounts_remapped == 3
    assert outcome.counts.accounts_created == 0
    assert outcome.counts.accounts_seen == 3
    assert outcome.counts.txns_rekeyed == before  # every row re-pointed, none re-made
    assert outcome.counts.txns_inserted == 0
    async with scoped_session(hh) as session:
        # The same rows, now pointing at the new connection — not new accounts.
        assert {a.name: a.id for a in (await _accounts(session)).values()} == original_ids
        for account in (await _accounts(session)).values():
            assert account.connection_id == new_connection_id
        rows = await _txns(session)
        assert len(rows) == before  # nothing lost, nothing duplicated
        # The new provider ids really are new, so this was a remap and not a no-op.
        assert all((t.external_id or "").startswith("b-") for t in rows)


async def test_a_reconnect_does_not_revise_a_humans_attribution(hh) -> None:
    """``owner_id`` is never touched on remap — attribution is not the bank's."""
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())))

    async with scoped_session(hh) as session:
        owner_id = (await _accounts(session))[scenarios.DEMO_CHECKING].owner_id

    async with scoped_session(hh) as session:
        await session.delete(await _connection_row(session, connection_id))
    new_connection_id = await _make_connection(hh)
    await _sync(
        hh, new_connection_id,
        FakeProvider(script=scenarios.scenario(scenarios.reidentified(scenarios.demo()))),
    )

    async with scoped_session(hh) as session:
        assert (await _accounts(session))[scenarios.DEMO_CHECKING].owner_id == owner_id


async def test_a_key_held_by_another_live_connection_is_shared_not_duplicated(hh) -> None:
    """Two claims against one bank report the *same* accounts, and the ledger says so.

    There is exactly one account per ``(household_id, external_key)`` — the unique
    constraint makes that a fact rather than a policy — so the second connection
    cannot have its own copy. What it can do is take the row over, which it does,
    and the run says so out loud.

    The flip-flop below is the honest consequence and is pinned deliberately: a
    user who claims one bank twice has two connections genuinely reporting one
    account, and which of them "owns" it is whoever synced last. The alternative —
    each connection writing its own copy — would double-count the money, and the
    database is right to refuse it.
    """
    first_connection = await _make_connection(hh, org_name="Bridge A")
    second_connection = await _make_connection(hh, org_name="Bridge B")
    await _sync(hh, first_connection, FakeProvider(script=scenarios.scenario(scenarios.demo())))
    async with scoped_session(hh) as session:
        before = len(await _txns(session))

    outcome = await _sync(
        hh, second_connection, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )

    assert outcome.counts.accounts_created == 0  # one account, not two
    assert outcome.counts.accounts_remapped == 3
    assert outcome.counts.txns_inserted == 0  # same provider ids, so the same rows
    async with scoped_session(hh) as session:
        accounts = await _accounts(session)
        assert len(accounts) == 3
        assert {a.connection_id for a in accounts.values()} == {second_connection}
        assert len(await _txns(session)) == before  # not one transaction duplicated
        events = await _events(session, outcome.run_id)
        contested = [e for e in events if e.event == "account.contested"]
        assert len(contested) == 3
        assert all(e.level == "warning" for e in contested)

    # And it follows the next connection to sync, which is the thing the warning is for.
    back = await _sync(
        hh, first_connection, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )
    assert back.counts.accounts_remapped == 3
    async with scoped_session(hh) as session:
        assert {a.connection_id for a in (await _accounts(session)).values()} == {
            first_connection
        }
        assert len(await _txns(session)) == before


# ---- health, errlist and failure ------------------------------------------


async def test_a_connection_level_errlist_entry_marks_the_run_errored(hh) -> None:
    connection_id = await _make_connection(hh)
    provider = FakeProvider(
        script=scenarios.scenario(scenarios.with_errlist(scenarios.demo(), scenarios.ERR_AUTH))
    )

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "error"
    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        assert connection.status == "auth_error"
        assert scenarios.ERR_AUTH in (connection.last_error or "")
        run = await _run_row(session, outcome.run_id)
        assert run.status == "error"
        # The data still landed: a connection complaint is not a reason to discard a
        # payload that parsed.
        assert run.txns_inserted == 12
        events = await _events(session, outcome.run_id)
        assert [e.event for e in events if e.level == "warning"] == ["provider.errmessage"]


async def test_a_request_warning_makes_the_run_partial_and_leaves_health_alone(hh) -> None:
    """``gen.api`` is the bridge complaining about *our* date range.

    Mapping it to a connection failure would mark a healthy bank broken because we
    asked for too much history.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(
        script=scenarios.scenario(
            scenarios.with_errlist(scenarios.demo(), scenarios.WARN_WINDOW_CAPPED)
        )
    )

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "partial"
    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        assert connection.status == "ok"
        assert connection.last_error is None
        assert (await _run_row(session, outcome.run_id)).status == "partial"


async def test_a_transient_failure_leaves_the_connection_health_alone(hh) -> None:
    """A 5xx from the bridge is our problem, not the connection's.

    Marking it broken would put an alarming badge in front of the user for
    something that fixes itself, and Reconnect would not fix it either.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo()))
    provider.raise_on_fetch = ProviderError(
        "bridge answered HTTP 503", kind="transient", status=503
    )

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "error"
    assert "503" in (outcome.error or "")
    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        assert connection.status == "ok"
        assert connection.last_error is not None
        # The cadence clock still moved, so the tick does not re-enqueue this
        # connection every minute against a bank that is down.
        assert connection.next_sync_at == NOW + timedelta(seconds=sync.backoff_seconds(1))
        assert connection.last_synced_at is None  # a failure is not a sync
        run = await _run_row(session, outcome.run_id)
        assert run.status == "error"
        assert run.http_status == 503
        assert [e.event for e in await _events(session, outcome.run_id)] == [
            "window.computed",
            "run.failed",
        ]


async def test_a_successful_run_records_the_fetch_telemetry(hh) -> None:
    """All three numbers the provider reported, not the two that are easy to see.

    ``http_status`` is the one that goes missing without anything failing: the
    panel renders its chip only when the column is set, so a NULL reads as "this
    run has nothing to say" — which is exactly how a healthy run's 200 was
    silently dropped while ``http_ms`` and ``bytes_fetched`` arrived. The fake
    reports no stats (it makes no request), so this scripts them onto the set it
    serves; the real provider fills them from the response it read.
    """
    connection_id = await _make_connection(hh)
    captured = scenarios.scenario(scenarios.demo())
    provider = FakeProvider(script=[
        replace(captured[0], stats=FetchStats(http_ms=182, http_status=200, bytes_fetched=4096))
    ])

    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "ok"
    async with scoped_session(hh) as session:
        run = await _run_row(session, outcome.run_id)
        assert (run.http_ms, run.http_status, run.bytes_fetched) == (182, 200, 4096)


async def test_a_recovered_connection_clears_its_previous_error(hh) -> None:
    """Health describes the connection *now*, not its worst day."""
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo()))
    provider.raise_on_fetch = ProviderError(
        "bridge answered HTTP 503", kind="transient", status=503
    )
    await _sync(hh, connection_id, provider)

    provider.raise_on_fetch = None
    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "ok"
    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        assert connection.status == "ok"
        assert connection.last_error is None
        assert connection.last_synced_at == NOW


async def test_a_connection_with_no_credential_fails_without_fetching(hh) -> None:
    async with scoped_session(hh) as session:
        connection = AccountConnection(household_id=hh, provider="fake", org_name="Nowhere")
        session.add(connection)
        await session.flush()
        connection_id = connection.id

    provider = FakeProvider(script=scenarios.scenario(scenarios.demo()))
    outcome = await _sync(hh, connection_id, provider)

    assert outcome.status == "error"
    assert "reconnect" in (outcome.error or "").lower()
    assert provider.seen_access_urls == []  # never went to the bridge without one
    async with scoped_session(hh) as session:
        assert (await _connection_row(session, connection_id)).status == "auth_error"


async def test_the_fetch_window_is_the_window_the_run_logged(hh) -> None:
    """The window computed in TX1 is the window the provider is handed.

    Without this the two could drift — the run log saying 45 days while the fetch
    asked for five — and every window assertion above would still pass.
    """
    connection_id = await _make_connection(hh)
    seen: list[datetime] = []

    class _Recording(FakeProvider):
        async def fetch_accounts(self, access_url, *, start):
            seen.append(start)
            return await super().fetch_accounts(access_url, start=start)

    outcome = await _sync(
        hh, connection_id, _Recording(script=scenarios.scenario(scenarios.demo()))
    )

    assert seen == [NOW - timedelta(days=sync.FIRST_SYNC_WINDOW_DAYS)]
    async with scoped_session(hh) as session:
        window = next(
            e for e in await _events(session, outcome.run_id) if e.event == "window.computed"
        )
        assert window.detail["start"] == seen[0].isoformat()


# ---- the fence (what a cancel does) ---------------------------------------


async def test_a_fenced_run_aborts_before_ingesting(hh) -> None:
    """Cancelled or reaped during the fetch: nothing lands, and the run says so."""
    connection_id = await _make_connection(hh)

    async def _refuse(_session) -> bool:
        return False

    outcome = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())),
        fence=_refuse,
    )

    assert outcome.status == "cancelled"
    async with scoped_session(hh) as session:
        assert await _txns(session) == []
        assert await _accounts(session) == {}
        run = await _run_row(session, outcome.run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        assert [e.event for e in await _events(session, outcome.run_id)] == [
            "window.computed",
            "run.cancelled",
        ]


async def test_a_run_that_still_owns_its_job_ingests_normally(hh) -> None:
    connection_id = await _make_connection(hh)
    asked: list[int] = []

    async def _allow(_session) -> bool:
        asked.append(1)
        return True

    outcome = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())),
        fence=_allow,
    )

    assert asked == [1]
    assert outcome.status == "ok"
    assert outcome.counts.txns_inserted == 12


# ---- the run log ----------------------------------------------------------


async def test_run_events_are_ordered_and_numbered_from_zero(hh) -> None:
    """``seq`` is what orders the dashboard's feed, and it must be per-run unique.

    A run writes its events across more than one transaction, so a second
    ``RunLog`` for the same run has to *resume* the sequence rather than restart it
    — ``UNIQUE (sync_run_id, seq)`` would refuse a restart, and the run would fail
    for a reason that has nothing to do with the bank.
    """
    connection_id = await _make_connection(hh)
    outcome = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )

    async with scoped_session(hh) as session:
        events = await _events(session, outcome.run_id)
        assert [e.seq for e in events] == list(range(len(events)))
        assert events[0].event == "window.computed"
        assert events[-1].event == "run.finished"
        assert events[-1].detail["txns_inserted"] == 12


async def test_the_first_sync_teaches_the_connection_and_its_own_run_their_name(hh) -> None:
    """A claim stores no institution name, because nothing knows one yet.

    The claim endpoint is handed a token and a URL — neither says which bank is
    behind them — so ``org_name`` is NULL until a payload arrives, and the first
    fetch is where it is learned. Both halves are asserted here because only one
    of them is obvious: the *connection* keeps the name for later, but the *run
    row* is opened in an earlier transaction, before the fetch, and so was
    written with a NULL label. Since a run's ``connection_id`` is SET NULL on
    disconnect, that label is the only thing that would still say where those
    transactions came from — and the run a person watches immediately after
    pasting a token is exactly this one.
    """
    connection_id = await _make_connection(hh, org_name=None)
    outcome = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )

    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        run = await _run_row(session, outcome.run_id)
    assert connection.org_name == "SimpleFIN Bridge"
    assert run.connection_label == "SimpleFIN Bridge"


async def test_a_later_rename_updates_the_connection_not_the_old_runs_label(hh) -> None:
    """The label is a fact about a run, not a live join to the connection.

    The connection follows the bank — it says what the institution is called now
    — while each run keeps the name it was reported under. So a rename moves
    forward and never backward, and the history stays readable as the sequence of
    names the bank actually used.
    """
    connection_id = await _make_connection(hh, org_name=None)
    first = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo()))
    )

    renamed = scenarios.with_org_name(scenarios.demo(), "Renamed Bank")
    second = await _sync(
        hh, connection_id, FakeProvider(script=scenarios.scenario(renamed))
    )

    async with scoped_session(hh) as session:
        first_label = (await _run_row(session, first.run_id)).connection_label
        second_label = (await _run_row(session, second.run_id)).connection_label
        connection = await _connection_row(session, connection_id)

    assert first_label == "SimpleFIN Bridge"  # not relabelled
    assert second_label == "Renamed Bank"
    assert connection.org_name == "Renamed Bank"


# ---- balances -------------------------------------------------------------


async def test_a_stated_account_snapshots_its_balance_at_the_providers_date(hh) -> None:
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())))

    async with scoped_session(hh) as session:
        checking = (await _accounts(session))[scenarios.DEMO_CHECKING]
        source = scenarios.account_named(scenarios.demo(), scenarios.DEMO_CHECKING)
        snapshots = (
            await session.execute(
                select(BalanceSnapshot).where(BalanceSnapshot.account_id == checking.id)
            )
        ).scalars().all()
        assert len(snapshots) == 1
        assert snapshots[0].balance == source.balance
        assert snapshots[0].balance_date == source.balance_date
        assert checking.current_balance == source.balance


async def test_a_derived_account_does_not_write_its_statement_balance_into_history(hh) -> None:
    """ADR-0021's interaction with sync.

    Sync creates investment accounts *stated* — it writes no holdings, so there is
    nothing to derive from (session 04). The guard is for the other case: an
    account created derived before migration 0006 that has holdings entered by
    hand. Its history is its holdings', and a synced stated balance must not put a
    second, disagreeing series in the same column — while the column itself still
    moves.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=[scenarios.demo(), scenarios.demo()])
    await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        savings = (await _accounts(session))[scenarios.DEMO_SAVINGS]
        assert savings.balance_source == "stated"
        await session.execute(
            delete(BalanceSnapshot).where(BalanceSnapshot.account_id == savings.id)
        )
        savings.balance_source = "derived"

    outcome = await _sync(hh, connection_id, provider)

    async with scoped_session(hh) as session:
        savings = (await _accounts(session))[scenarios.DEMO_SAVINGS]
        assert savings.current_balance == scenarios.account_named(
            scenarios.demo(), scenarios.DEMO_SAVINGS
        ).balance
        assert (
            await session.execute(
                select(BalanceSnapshot).where(BalanceSnapshot.account_id == savings.id)
            )
        ).scalars().all() == []
        skipped = [
            e for e in await _events(session, outcome.run_id)
            if e.event == "balance.derived_skipped"
        ]
        assert len(skipped) == 1


# ---- the window -----------------------------------------------------------


async def test_the_first_window_is_the_bridges_recommended_range(hh) -> None:
    connection_id = await _make_connection(hh)
    async with scoped_session(hh) as session:
        window = await sync.compute_window(session, connection_id, now=NOW)

    assert window.first_sync is True
    assert window.pending_anchors == 0
    assert window.start == NOW - timedelta(days=sync.FIRST_SYNC_WINDOW_DAYS)


async def test_the_window_is_anchored_to_the_oldest_unsettled_row(hh) -> None:
    """Not a fixed ``now - 5d``: a charge that has not posted is the low-water mark.

    Without this the settled-row window would miss any posting older than the
    overlap, and sync would insert a *second* row for a transaction it already has.
    """
    first, _second = scenarios.pending_then_posted_same_id()
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(first)))

    async with scoped_session(hh) as session:
        pending = await _the_pending_row(session)
        transacted_at = pending.transacted_at
        window = await sync.compute_window(session, connection_id, now=NOW)

    assert window.first_sync is False
    assert window.pending_anchors == 1
    assert window.start == transacted_at
    assert window.start < NOW - timedelta(days=sync.OVERLAP_DAYS)  # the anchor won


async def test_the_window_is_clamped_at_both_ends(hh) -> None:
    """The overlap is a floor, floored again by a year, ceilinged by yesterday."""
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(scenarios.demo())))

    async with scoped_session(hh) as session:
        settled = await sync.compute_window(session, connection_id, now=NOW)
        assert settled.start == NOW - timedelta(days=sync.OVERLAP_DAYS)

    # A pending row from long ago drags the start back, but not without limit.
    async with scoped_session(hh) as session:
        row = (await _txns(session))[0]
        row.is_pending = True
        row.transacted_at = NOW - timedelta(days=400)
        await session.flush()
        clamped = await sync.compute_window(session, connection_id, now=NOW)

    assert clamped.start == NOW - timedelta(days=sync.MAX_LOOKBACK_DAYS)


# ---- transfers (ADR-0018) -------------------------------------------------


def _transfer_leg(external_id: str, amount: str, moment: datetime) -> ProviderTransaction:
    return ProviderTransaction(
        external_id=external_id,
        amount=D(amount),
        transacted_at=moment,
        currency="USD",
        posted_at=moment,
        description="Online transfer",
        payee="Transfer",
    )


async def test_two_legs_arriving_in_one_payload_still_get_matched(hh) -> None:
    """The reason the auto-match is a second pass, not part of the insert loop.

    Both legs arrive in the same fetch, so the first is ingested before the second
    exists. Matching inside the loop would leave both unmatched forever.
    """
    moment = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    payload = _payload({
        scenarios.DEMO_CHECKING: [_transfer_leg("leg-out", "-250.00", moment)],
        scenarios.DEMO_EMPTY: [_transfer_leg("leg-in", "250.00", moment)],
    })

    connection_id = await _make_connection(hh)
    outcome = await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(payload)))

    assert outcome.counts.transfers_matched == 1
    async with scoped_session(hh) as session:
        groups = {t.transfer_group_id for t in await _txns(session)}
        assert None not in groups  # both legs are in the group
        assert len(groups) == 1
        group = (
            await session.execute(
                select(TransferGroup).where(TransferGroup.id == groups.pop())
            )
        ).scalar_one()
        assert group.matched_by == "auto"  # not a human action, and must not claim to be
        assert group.fx_cost_base is None  # same currency: no residual to report


async def test_an_ambiguous_transfer_is_left_for_a_human(hh) -> None:
    """Two plausible legs on each side: the picker's job, not the matcher's.

    The matcher links only when one plausible counterpart is strictly closer in
    time than every other, so ambiguity has to exist from *both* sides and at the
    same moment — otherwise each twin would resolve against the nearest opposite
    leg and the ambiguity would never be seen.
    """
    moment = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    payload = _payload({
        scenarios.DEMO_CHECKING: [
            _transfer_leg("out-1", "-250.00", moment),
            _transfer_leg("out-2", "-250.00", moment),
        ],
        scenarios.DEMO_EMPTY: [
            _transfer_leg("in-1", "250.00", moment),
            _transfer_leg("in-2", "250.00", moment),
        ],
    })

    connection_id = await _make_connection(hh)
    outcome = await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(payload)))

    assert outcome.counts.transfers_matched == 0
    async with scoped_session(hh) as session:
        assert all(t.transfer_group_id is None for t in await _txns(session))


async def test_identical_moves_on_consecutive_days_pair_by_date(hh) -> None:
    """Two $20k moves a day apart are two transfers, not an ambiguity (ADR-0049).

    Found on a real instance: each withdrawal had two exact deposits, one on its
    own day and one a day off, and the "exactly one candidate" rule refused both,
    so $40k was reported as income and $40k as spending. The closer deposit is
    the leg, and each is used once.
    """
    day1 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    payload = _payload({
        scenarios.DEMO_CHECKING: [
            _transfer_leg("out-1", "-20000.00", day1),
            _transfer_leg("out-2", "-20000.00", day2),
        ],
        scenarios.DEMO_EMPTY: [
            _transfer_leg("in-1", "20000.00", day1),
            _transfer_leg("in-2", "20000.00", day2),
        ],
    })

    connection_id = await _make_connection(hh)
    outcome = await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(payload)))

    assert outcome.counts.transfers_matched == 2
    async with scoped_session(hh) as session:
        rows = await _txns(session)
        assert all(t.transfer_group_id is not None for t in rows)
        by_group: dict = {}
        for t in rows:
            by_group.setdefault(t.transfer_group_id, []).append(t.transacted_at)
        # Each pair is one day's withdrawal and that same day's deposit.
        assert sorted(len(set(days)) for days in by_group.values()) == [1, 1]


async def test_the_matcher_only_links_what_the_picker_would_offer(hh) -> None:
    """The two share ``list_transfer_candidates`` by construction; this is the
    end-to-end statement of it. A linked leg offers nothing, because a leg already
    in a group is not free to match again."""
    moment = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    payload = _payload({
        scenarios.DEMO_CHECKING: [_transfer_leg("leg-out", "-250.00", moment)],
        scenarios.DEMO_EMPTY: [_transfer_leg("leg-in", "250.00", moment)],
    })
    connection_id = await _make_connection(hh)
    await _sync(hh, connection_id, FakeProvider(script=scenarios.scenario(payload)))

    async with scoped_session(hh) as session:
        for row in await _txns(session):
            assert await txns.list_transfer_candidates(session, hh, row.id) == []


# ---- the leak (ADR-0016) --------------------------------------------------


async def test_the_access_url_reaches_no_run_record(hh) -> None:
    """Sanitized at write time, so the credential is not in the column at all.

    This is the half of the ADR-0016 leak test that lives in the database. The
    other half — that it reaches no *log record* — belongs with the logging
    configuration, because that is the code it is a test of.

    The provider is handed the real string here (``seen_access_urls`` is how we
    know), and it quotes it back at us in the failure: an aggregator's own message
    is the likeliest carrier, and the sanitizer has to catch it even when the caller
    forgot to pass ``secrets=``.
    """
    connection_id = await _make_connection(hh)
    provider = FakeProvider(script=scenarios.scenario(scenarios.demo()))
    provider.raise_on_fetch = ProviderError(
        f"bridge refused the credential at {FAKE_ACCESS_URL} (HTTP 403)", kind="auth", status=403
    )

    outcome = await _sync(hh, connection_id, provider)

    assert provider.seen_access_urls == [FAKE_ACCESS_URL]  # it really was handed it
    async with scoped_session(hh) as session:
        connection = await _connection_row(session, connection_id)
        run = await _run_row(session, outcome.run_id)
        blob = json.dumps(
            {
                "run_error": run.error,
                "last_error": connection.last_error,
                "events": [
                    {"event": e.event, "detail": e.detail}
                    for e in await _events(session, outcome.run_id)
                ],
            }
        )
    assert FAKE_ACCESS_URL not in blob
    assert "fake-password" not in blob
    assert "fake-user" not in blob
