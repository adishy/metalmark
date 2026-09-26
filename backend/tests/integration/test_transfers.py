"""Transfers through their whole life: link, exclude, propose, unlink (ADR-0008/0018).

The properties worth protecting here are the ones a plausible-looking refactor
would quietly break — that unlinking really puts both legs back into cash-flow and
spending (which is what makes the exclusion safe to apply at all), that a
cross-currency pair's residual is the real spread and not a zero (ADR-0018's
entire point), and that the candidate picker only ever offers rows the link
endpoint would accept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.deps import SESSION_COOKIE
from app.models import CategoryGroup, Transaction, TransferGroup
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import TransactionCreate, TransactionUpdate
from app.services import ledger, reports
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal
CSRF = "X-CSRF-Token"


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def _make_category(session, household_id, group_type: str, name: str):
    g = CategoryGroup(household_id=household_id, name=group_type.title(), type=group_type)
    session.add(g)
    await session.flush()
    return await ledger.create_category(session, household_id, g.id, name, None, None, 0)


async def _accounts(session, hh, *currencies: str):
    return [
        await ledger.create_account(
            session, hh,
            AccountCreate(name=f"{ccy}-{i}", type="depository", currency=ccy),
        )
        for i, ccy in enumerate(currencies)
    ]


async def _linked_pair(session, hh):
    """A plain same-currency transfer, already linked."""
    (a, b) = await _accounts(session, hh, "USD", "USD")
    out = await txns.create_transaction(session, hh, TransactionCreate(
        account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
    into = await txns.create_transaction(session, hh, TransactionCreate(
        account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
    group = await txns.link_transfer(session, hh, out.id, into.id)
    return group, out, into


# ---- The FX cost (ADR-0018) -----------------------------------------------
#
# CHF, deliberately, and not EUR: ``fx_rates`` is global reference data — no
# household_id, unique on (base, quote, date) — so a rate written here is visible
# to every other test in the session. EUR is what test_ledger.py and the demo seed
# both reach for, and a rate of ours on the same key would decide *their*
# conversion depending on collection order. CHF is a pair no other test touches.


async def test_cross_currency_pair_records_the_real_fx_cost(household_factory):
    """Acceptance bar: a cross-currency pair's ``fx_cost_base`` is Σ base_amount,
    and it is not zero.

    The bank gave 106 USD for 100 CHF where the ledger's rate says those CHF were
    worth 108 — that 2 USD is the spread, and it is exactly the amount the
    cash-flow exclusion would swallow if the residual were not recorded on the
    group. A zero here would mean the feature silently loses money.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        # 1 CHF = 1.08 USD, so 100 CHF is 108 USD at the ledger's rate.
        await ledger.upsert_fx_rate(s, hh, base_ccy="CHF", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("1.08"))
        chf, usd = await _accounts(s, hh, "CHF", "USD")
        out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=chf.id, amount=D("-100"), transacted_at=_dt(2026, 1, 10)))
        into = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd.id, amount=D("106"), transacted_at=_dt(2026, 1, 11)))
        # Not equal-and-opposite in native terms — which is exactly why ADR-0008's
        # equal-magnitude rule cannot match this pair.
        assert out.amount + into.amount != 0
        assert out.base_amount == D("-108.0000")
        assert into.base_amount == D("106.0000")

        group = await txns.link_transfer(s, hh, out.id, into.id)
        stored, expected = group.fx_cost_base, out.base_amount + into.base_amount

    assert stored is not None and stored != 0
    assert stored == expected == D("-2.0000")


async def test_same_currency_pair_has_no_residual(household_factory):
    """The counterpart case, so ``fx_cost_base`` means something: legs that do
    cancel exactly have no cost to report, and that is ``None``, not ``0``."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        group, _out, _into = await _linked_pair(s, hh)
    assert group.fx_cost_base is None


# ---- Exclusion, and getting the legs back ---------------------------------


async def test_unlink_returns_both_legs_to_cash_flow_and_spending(household_factory):
    """The property that makes ADR-0008's exclusion safe to apply: it is undoable.

    The out-leg is categorized as an ordinary expense on purpose — the exclusion
    must be doing the work, so a report that excluded by category instead of by
    group would pass the linked half of this test and fail the unlinked half.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        exp = await _make_category(s, hh, "expense", "Misc")
        a, b = await _accounts(s, hh, "USD", "USD")
        out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10),
            category_id=exp.id))
        into = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        # a real expense that must count throughout
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-30"), transacted_at=_dt(2026, 1, 11),
            category_id=exp.id))

        async def snapshot():
            _b, _g, months = await reports.cash_flow_series(
                s, hh, date(2026, 1, 1), date(2026, 1, 31), granularity="month")
            _b, _rows, spend, _w = await reports.spending_by_category(
                s, hh, date(2026, 1, 1), date(2026, 1, 31))
            return months[0], spend

        before_month, before_spend = await snapshot()
        group = await txns.link_transfer(s, hh, out.id, into.id)
        linked_month, linked_spend = await snapshot()
        await txns.unlink_transfer(s, hh, group.id)
        after_month, after_spend = await snapshot()

    # Unlinked, the legs are indistinguishable from income and spending.
    assert before_month["expense"] == D("-530.0000")
    assert before_month["income"] == D("500.0000")
    assert before_spend == D("530.0000")
    # Linked, only the real expense survives.
    assert linked_month["expense"] == D("-30.0000")
    assert linked_month["income"] == D("0.0000")
    assert linked_spend == D("30.0000")
    # Unlinked again: both legs are back, to the cent.
    assert after_month["expense"] == D("-530.0000")
    assert after_month["income"] == D("500.0000")
    assert after_spend == D("530.0000")


async def test_unlink_clears_the_legs_and_deletes_the_group(household_factory):
    """Once unlinked there is no trace of the link to trip over later."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        group, out, into = await _linked_pair(s, hh)
        await txns.unlink_transfer(s, hh, group.id)
        legs_after = (
            await s.execute(
                select(Transaction.id).where(Transaction.transfer_group_id == group.id)
            )
        ).scalars().all()
        group_after = (
            await s.execute(select(TransferGroup).where(TransferGroup.id == group.id))
        ).scalar_one_or_none()
        counts = {
            t.id: t.transfer_group_id
            for t in (
                await s.execute(
                    select(Transaction).where(Transaction.id.in_([out.id, into.id]))
                )
            ).scalars().all()
        }
    assert counts == {out.id: None, into.id: None}
    assert legs_after == []
    assert group_after is None


async def test_unlink_is_not_found_for_an_unknown_group(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(LedgerError) as exc:
            await txns.unlink_transfer(s, hh, uuid.uuid4())
    assert exc.value.status == 404


async def test_unlink_cannot_touch_another_households_transfer(household_factory):
    """Tenant isolation on the delete path, not just the read path."""
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        group, _out, _into = await _linked_pair(s, a)
    async with scoped_session(household_id=b) as s:
        with pytest.raises(LedgerError) as exc:
            await txns.unlink_transfer(s, b, group.id)
    assert exc.value.status == 404
    # And A's transfer is exactly as it was.
    async with scoped_session(household_id=a) as s:
        legs = (
            await s.execute(
                select(Transaction.id).where(Transaction.transfer_group_id == group.id)
            )
        ).scalars().all()
        survivor = (
            await s.execute(select(TransferGroup).where(TransferGroup.id == group.id))
        ).scalar_one_or_none()
    assert len(legs) == 2
    assert survivor is not None


# ---- Linking still refuses what is not a transfer -------------------------


async def test_link_rejects_a_same_account_or_same_sign_pair(household_factory):
    """Extends the exclusion coverage with the rejections it never had: two rows
    in one account, and two rows pointing the same way, are not a transfer."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        same_account = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        same_sign = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        with pytest.raises(LedgerError) as acct_exc:
            await txns.link_transfer(s, hh, out.id, same_account.id)
        with pytest.raises(LedgerError) as sign_exc:
            await txns.link_transfer(s, hh, out.id, same_sign.id)
        # Nothing was half-created by either refusal.
        orphans = (
            await s.execute(select(TransferGroup.id))
        ).scalars().all()
    assert acct_exc.value.status == 400
    assert sign_exc.value.status == 400
    assert orphans == []


async def test_link_refuses_a_leg_that_moves_nothing(household_factory):
    """A zero-amount leg is not the opposite of a positive one.

    ``(a.amount > 0) == (b.amount > 0)`` reads `0 > 0` as False — the same side as
    an expense — so a $0.00 row pairs with a real $500 deposit and takes it out of
    cash-flow and spending with nothing on the other side to account for it. ADR-0049
    pairs equal-and-opposite legs, and $0.00 has no opposite.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        zero = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("0.0000"), transacted_at=_dt(2026, 1, 10)))
        real = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))

        # Either position: the zero leg offered, and the zero leg asked for.
        with pytest.raises(LedgerError) as as_other_exc:
            await txns.link_transfer(s, hh, zero.id, real.id)
        with pytest.raises(LedgerError) as as_subject_exc:
            await txns.link_transfer(s, hh, real.id, zero.id)

        groups = (await s.execute(select(TransferGroup.id))).scalars().all()
        linked = (
            await s.execute(
                select(Transaction.transfer_group_id).where(
                    Transaction.id.in_([zero.id, real.id])
                )
            )
        ).scalars().all()
    assert as_other_exc.value.status == 400
    assert as_subject_exc.value.status == 400
    assert groups == []
    assert all(leg is None for leg in linked)


async def test_link_refuses_a_leg_that_is_already_in_a_transfer(household_factory):
    """The picker hides taken legs; this is where it is enforced.

    Re-linking one leg of a pair moves it into the new group and leaves the old one
    one-legged: still excluded from cash-flow and spending, but no longer a pair —
    a state nothing else in the app can produce, and nothing can repair, because
    unlinking the group that is left deletes it and orphans the leg it still holds.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b, c = await _accounts(s, hh, "USD", "USD", "USD")
        out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        into = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        free = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=c.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        group = await txns.link_transfer(s, hh, out.id, into.id)

        # One leg taken, the other free — and the pair that is already a pair.
        with pytest.raises(LedgerError) as taken_exc:
            await txns.link_transfer(s, hh, out.id, free.id)
        with pytest.raises(LedgerError) as again_exc:
            await txns.link_transfer(s, hh, out.id, into.id)

        # The group it was in is untouched, and the free leg is still free.
        groups = (await s.execute(select(TransferGroup.id))).scalars().all()
        legs = (
            await s.execute(
                select(Transaction.id).where(Transaction.transfer_group_id == group.id)
            )
        ).scalars().all()
        free_group = (
            await s.execute(
                select(Transaction.transfer_group_id).where(Transaction.id == free.id)
            )
        ).scalar_one()
    assert taken_exc.value.status == 400
    assert again_exc.value.status == 400
    assert groups == [group.id]
    assert sorted(legs) == sorted([out.id, into.id])
    assert free_group is None


# ---- Candidates -----------------------------------------------------------


async def test_the_picker_offers_nothing_the_linker_would_refuse(household_factory):
    """The picker's stated property, on the rows this change moved.

    A zero row used to be offered to a positive subject (``amount <= 0``) and
    accepted by the linker; a taken leg used to be hidden as a *row* but the subject
    itself was never checked. Both are now refusals on the link side, so neither may
    be offered here — and what is offered has to link, which is asserted rather than
    argued.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b, c, d = await _accounts(s, hh, "USD", "USD", "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        # A row that moves nothing, in an account of its own: the shape the old
        # `amount <= 0` predicate let through.
        zero = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("0"), transacted_at=_dt(2026, 1, 10)))
        counterpart = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        taken_out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=c.id, amount=D("-700"), transacted_at=_dt(2026, 1, 10)))
        taken_in = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=d.id, amount=D("700"), transacted_at=_dt(2026, 1, 10)))
        await txns.link_transfer(s, hh, taken_out.id, taken_in.id)

        offered = await txns.list_transfer_candidates(s, hh, subject.id)
        # A zero subject pairs with nothing at all.
        no_pair = await txns.list_transfer_candidates(s, hh, zero.id)
        # And neither does one that is already half of a transfer.
        taken = await txns.list_transfer_candidates(s, hh, taken_out.id)

        assert [c.txn.id for c in offered] == [counterpart.id]

        # The property itself: the row offered is a row the linker takes.
        (only,) = offered
        group = await txns.link_transfer(s, hh, subject.id, only.txn.id)
        subject_group = (
            await s.execute(
                select(Transaction.transfer_group_id).where(Transaction.id == subject.id)
            )
        ).scalar_one()
    assert no_pair == []
    assert taken == []
    assert subject_group == group.id


async def test_candidates_offer_the_counterpart_and_nothing_else(household_factory):
    """The obvious counterpart is found; the four things that only look like
    candidates are not. Every row here is one ``link_transfer`` would refuse or
    one that is already spoken for, so offering any of them would be a dead end."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b, c = await _accounts(s, hh, "USD", "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        counterpart = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        # same account as the subject
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        # same sign as the subject
        await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        # already in a group of its own
        taken_out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-700"), transacted_at=_dt(2026, 1, 10)))
        taken_in = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=c.id, amount=D("700"), transacted_at=_dt(2026, 1, 10)))
        await txns.link_transfer(s, hh, taken_out.id, taken_in.id)

        cands = await txns.list_transfer_candidates(s, hh, subject.id)

    assert [c.txn.id for c in cands] == [counterpart.id]
    assert counterpart.id != subject.id


async def test_candidates_respect_the_days_window(household_factory):
    """``days`` is the caller's, and it is a real bound: a leg three days out is
    invisible to a one-day search and found by a three-day one."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        three_days = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 13)))

        tight = await txns.list_transfer_candidates(s, hh, subject.id, days=1)
        roomy = await txns.list_transfer_candidates(s, hh, subject.id, days=3)

    assert [c.txn.id for c in tight] == []
    assert [c.txn.id for c in roomy] == [three_days.id]
    assert roomy[0].days_apart == 3
    # The window is symmetric — a leg before the subject counts just as much.
    async with scoped_session(household_id=hh) as s:
        earlier = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 8)))
        back = await txns.list_transfer_candidates(s, hh, subject.id, days=2)
    assert [c.txn.id for c in back] == [earlier.id]


async def test_cross_currency_candidate_matches_on_base_amount_within_tolerance(
    household_factory,
):
    """ADR-0018's matching rule, and its limit.

    Across currencies the native amounts are never equal-and-opposite, so
    equality would find nothing at all. The pair whose base amounts nearly cancel
    is offered as a match; the one whose base amounts are far apart is *still*
    offered — explicit user linking is the ADR's stated override — but flagged,
    with its residual in plain sight rather than dressed up as a match.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.upsert_fx_rate(s, hh, base_ccy="CHF", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("1.08"))
        chf, usd = await _accounts(s, hh, "CHF", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=chf.id, amount=D("-100"), transacted_at=_dt(2026, 1, 10)))
        likely = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd.id, amount=D("106"), transacted_at=_dt(2026, 1, 10)))
        unlikely = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd.id, amount=D("50"), transacted_at=_dt(2026, 1, 10)))

        cands = await txns.list_transfer_candidates(s, hh, subject.id)

    by_id = {c.txn.id: c for c in cands}
    assert set(by_id) == {likely.id, unlikely.id}
    # Found by base amount, not by equality of the native amounts.
    assert likely.amount + subject.amount != 0
    assert by_id[likely.id].within_tolerance is True
    assert by_id[likely.id].fx_cost_base == D("-2.0000")  # inside 2% of 108
    assert by_id[unlikely.id].within_tolerance is False
    assert by_id[unlikely.id].fx_cost_base == D("-58.0000")
    # The closer base amount sorts first, so the likely match is the one on top.
    assert [c.txn.id for c in cands] == [likely.id, unlikely.id]


async def test_the_residual_shown_before_linking_is_the_one_stored_after(
    household_factory,
):
    """The picker's whole reason for existing: the FX cost is visible *before* the
    user commits, and committing charges exactly what was shown. Both directions
    are checked — a real cost, and no cost at all."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await ledger.upsert_fx_rate(s, hh, base_ccy="CHF", quote_ccy="USD",
                                    rate_date=date(2026, 1, 1), rate=D("1.08"))
        chf, usd_a, usd_b = await _accounts(s, hh, "CHF", "USD", "USD")

        fx_out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=chf.id, amount=D("-100"), transacted_at=_dt(2026, 1, 10)))
        fx_in = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd_a.id, amount=D("106"), transacted_at=_dt(2026, 1, 10)))
        same_out = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd_a.id, amount=D("-500"), transacted_at=_dt(2026, 3, 10)))
        same_in = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=usd_b.id, amount=D("500"), transacted_at=_dt(2026, 3, 10)))

        fx_shown = (await txns.list_transfer_candidates(s, hh, fx_out.id))[0]
        same_shown = (await txns.list_transfer_candidates(s, hh, same_out.id))[0]
        fx_group = await txns.link_transfer(s, hh, fx_out.id, fx_in.id)
        same_group = await txns.link_transfer(s, hh, same_out.id, same_in.id)

    assert fx_shown.txn.id == fx_in.id
    assert fx_shown.fx_cost_base == fx_group.fx_cost_base == D("-2.0000")
    assert same_shown.txn.id == same_in.id
    # A same-currency pair cancels exactly: nothing to report, on either side —
    # and it still counts as a match, because there is no rate between them to move.
    assert same_shown.fx_cost_base is None
    assert same_shown.within_tolerance is True
    assert same_group.fx_cost_base is None


async def test_candidates_are_ordered_by_proximity_then_base_amount(household_factory):
    """Ordering is time first — a transfer posts within days of its other leg — and
    the base-amount gap only breaks ties inside a date. Reversing those two keys
    would put a distant-but-exact leg above the leg that posted that afternoon."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-1000"), transacted_at=_dt(2026, 1, 10)))
        nearest = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("1000"), transacted_at=_dt(2026, 1, 10)))
        same_day_lumpy = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("990"), transacted_at=_dt(2026, 1, 10)))
        next_day_lumpy = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("990"), transacted_at=_dt(2026, 1, 11)))
        far_but_exact = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("1000"), transacted_at=_dt(2026, 1, 13)))

        cands = await txns.list_transfer_candidates(s, hh, subject.id)

    assert [c.txn.id for c in cands] == [
        nearest.id, same_day_lumpy.id, next_day_lumpy.id, far_but_exact.id,
    ]
    assert [c.days_apart for c in cands] == [0, 0, 1, 3]


async def test_hidden_rows_are_still_offered_as_candidates(household_factory):
    """Hiding a row is a decision about the list, not a claim that the row is not
    half of a transfer — so the picker still offers it. Filtering hidden rows out
    here would have been a silent extra rule the caller never asked for."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        hidden = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("500"), transacted_at=_dt(2026, 1, 10)))
        await txns.update_transaction(s, hh, hidden.id, TransactionUpdate(is_hidden=True))

        cands = await txns.list_transfer_candidates(s, hh, subject.id)

    assert [c.txn.id for c in cands] == [hidden.id]


# ---- The auto-matcher (ADR-0049) ------------------------------------------
#
# `auto_match_transfers` had no test of its own until this section: the pairs it
# links are exercised through sync. These pin the three rules the docstring states
# — closest or none, a leg already linked is not free (including one this same pass
# linked), and the picker's cap and ordering bound it — because the pass below them
# was rewritten to discover candidates in one query instead of one per subject, and
# the outcome has to be the same outcome.


def _at(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=UTC)


async def _linked_ids(session) -> set:
    return set(
        (await session.execute(
            select(Transaction.id).where(Transaction.transfer_group_id.is_not(None))
        )).scalars().all()
    )


async def test_the_auto_matcher_takes_the_closest_and_refuses_what_it_cannot_tell(
    household_factory,
):
    """A household that moves $20k on two consecutive days, two more $900 out/in
    within one day, and one $700 against two equally likely $700 deposits.

    The first block is the docstring's own case — "each withdrawal has one deposit
    on its own day and one a day off" — and the second is the sharper version of it:
    the deposits are two hours either side of the withdrawals, so which one belongs
    to which is decided by proximity and by what the pass has already taken, not by
    the amount that happens to be equal. The caller's order decides that, which is
    why it is given explicitly here rather than left to collection order.

    The last block is a tie, and it is refused — **from the side that can see it**.
    The withdrawal has two candidates an equal distance away, so the matcher cannot
    tell which is the transfer and links neither. Its pass then reaches the first
    deposit, which is a subject in its own right and has exactly one candidate; from
    there the pair is not ambiguous, so it links. The refusal is per subject, not per
    pair, and the outcome depends on the order the ids arrive in — that is the
    behaviour here, and the reason the fixture states the order.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b, c = await _accounts(s, hh, "USD", "USD", "USD")
        made = []

        async def _mk(account, amount, when, description):
            txn = await txns.create_transaction(s, hh, TransactionCreate(
                account_id=account.id, amount=D(amount), transacted_at=when,
                description=description,
            ))
            made.append(txn)
            return txn

        # Two consecutive days, $20k each way: each withdrawal has a deposit on its
        # own day (distance 0) and one a day off (distance 24h).
        day1_out = await _mk(a, "-20000", _at(2026, 1, 10, 9), "To savings")
        day1_in = await _mk(b, "20000", _at(2026, 1, 10, 9), "From checking")
        day2_out = await _mk(a, "-20000", _at(2026, 1, 11, 9), "To savings")
        day2_in = await _mk(b, "20000", _at(2026, 1, 11, 9), "From checking")
        # A row only a careless matcher would pair with either of them: same date,
        # opposite sign, wrong amount.
        noise = await _mk(c, "5000", _at(2026, 1, 10, 9), "An unrelated deposit")

        # Two withdrawals an hour apart, two deposits two hours apart. The first
        # withdrawal's nearest deposit is taken; the second's only remaining deposit
        # is an hour off — and that same deposit is *tied* with the other one for the
        # first withdrawal, so which pair exists depends on the order.
        near_out_1 = await _mk(a, "-900", _at(2026, 1, 20, 10), "Out 1")
        near_out_2 = await _mk(a, "-900", _at(2026, 1, 20, 11), "Out 2")
        near_in_1 = await _mk(b, "900", _at(2026, 1, 20, 10), "In 1")
        near_in_2 = await _mk(b, "900", _at(2026, 1, 20, 12), "In 2")

        # The tie: two identical deposits two hours either side of the withdrawal.
        tie_out = await _mk(a, "-700", _at(2026, 1, 25, 12), "Out")
        tie_in_1 = await _mk(b, "700", _at(2026, 1, 25, 10), "In a")
        tie_in_2 = await _mk(b, "700", _at(2026, 1, 25, 14), "In b")

        # Seen from the withdrawal, the two are interchangeable — the fixture says so
        # rather than the test assuming it. (A third row is offered and does not
        # match: the $900 deposit five days back, which is inside the window and the
        # wrong amount.)
        tie_view = await txns.list_transfer_candidates(s, hh, tie_out.id)
        tie_matching = [c for c in tie_view if c.within_tolerance]
        assert len(tie_matching) == 2
        assert {c.txn.id for c in tie_matching} == {tie_in_1.id, tie_in_2.id}
        gaps = [
            abs((c.txn.transacted_at - tie_out.transacted_at).total_seconds())
            for c in tie_matching
        ]
        # Equal to the second: a tie, which is what the matcher refuses on. Which of
        # the two comes first in the list is decided by the query's last ordering key
        # (their ids), so the two are compared as a set and not in order.
        assert gaps[0] == gaps[1]

        linked = await txns.auto_match_transfers(
            s, hh,
            [t.id for t in (day1_out, day1_in, day2_out, day2_in, noise,
                            near_out_1, near_out_2, near_in_1, near_in_2,
                            tie_out, tie_in_1, tie_in_2)],
        )

        groups = {}
        for txn in made:
            groups[txn.id] = (
                await s.execute(
                    select(Transaction.transfer_group_id).where(Transaction.id == txn.id)
                )
            ).scalar_one()

    assert linked == 5
    grouped = {txn_id: group for txn_id, group in groups.items() if group is not None}
    assert set(grouped) == {
        day1_out.id, day1_in.id, day2_out.id, day2_in.id,
        near_out_1.id, near_in_1.id, near_out_2.id, near_in_2.id,
        tie_out.id, tie_in_1.id,
    }
    # Every pair is its own group, and each group is exactly one pair.
    assert len(set(grouped.values())) == 5
    # The consecutive-day case pairs by proximity, not by the amount that happens to
    # be equal: day 1 with day 1, day 2 with day 2.
    assert grouped[day1_out.id] == grouped[day1_in.id]
    assert grouped[day2_out.id] == grouped[day2_in.id]
    # And the same-day case by the nearest, in the order the ids arrived: Out 1 takes
    # the deposit on the same hour, which leaves Out 2 the one two hours later.
    assert grouped[near_out_1.id] == grouped[near_in_1.id]
    assert grouped[near_out_2.id] == grouped[near_in_2.id]
    assert grouped[day1_out.id] != grouped[day2_out.id]

    # The tie is linked from the deposit's side and not the withdrawal's, and the
    # second deposit is left for a human — as is the wrong-amount row.
    assert grouped[tie_out.id] == grouped[tie_in_1.id]
    assert tie_in_2.id not in grouped
    assert noise.id not in grouped


async def test_the_auto_matcher_skips_a_leg_this_same_pass_linked(household_factory):
    """A deposit that the pass has already linked is not free to be a subject.

    The fixture is built so a matcher that re-examined it would not merely do
    redundant work but pick a *different* pair: the deposit's only remaining
    candidate is the second withdrawal, which the first withdrawal did not take.
    The link guard would refuse that attempt anyway, so the pass has to know.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b = await _accounts(s, hh, "USD", "USD")
        out_1 = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-900"), transacted_at=_at(2026, 1, 20, 10)))
        out_2 = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-900"), transacted_at=_at(2026, 1, 20, 11)))
        into = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=b.id, amount=D("900"), transacted_at=_at(2026, 1, 20, 10)))

        linked = await txns.auto_match_transfers(s, hh, [out_1.id, into.id, out_2.id])
        remaining = await _linked_ids(s)

    assert linked == 1
    assert remaining == {out_1.id, into.id}


async def test_the_auto_matcher_is_bounded_by_the_pickers_cap(household_factory):
    """The cap is a bound on the work the picker does, and the matcher is bound by it.

    A matching row beyond the cap is not found — here the 25 offered rows are all the
    wrong amount, and the exact counterpart sits just past them at five days out. The
    same query with the cap lifted *does* include it, which is what makes this about
    the cap rather than about the window or the amount rule.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        a, b, c = await _accounts(s, hh, "USD", "USD", "USD")
        subject = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=a.id, amount=D("-500"), transacted_at=_dt(2026, 1, 10)))
        for _ in range(txns.CANDIDATE_LIMIT):
            await txns.create_transaction(s, hh, TransactionCreate(
                account_id=b.id, amount=D("400"), transacted_at=_dt(2026, 1, 10)))
        distant = await txns.create_transaction(s, hh, TransactionCreate(
            account_id=c.id, amount=D("500"), transacted_at=_dt(2026, 1, 15)))

        capped = await txns.list_transfer_candidates(s, hh, subject.id)
        uncapped = await txns.list_transfer_candidates(
            s, hh, subject.id, limit=txns.CANDIDATE_LIMIT + 1
        )
        linked = await txns.auto_match_transfers(s, hh, [subject.id])

        still_free = await _linked_ids(s)

    assert len(capped) == txns.CANDIDATE_LIMIT
    assert distant.id not in [c.txn.id for c in capped]
    assert distant.id in [c.txn.id for c in uncapped]
    assert linked == 0
    assert still_free == set()


async def test_the_pass_is_the_same_however_the_subjects_are_chunked(
    household_factory, monkeypatch
):
    """The candidates are discovered a chunk of subjects at a time, not one at a time.

    ``CANDIDATE_CHUNK`` is a transport detail — how many ids one query carries — and
    it must not be part of the rule. The same fixture is built in a fresh household
    at each chunk size, and the pairs that come out have to be the same ones. A chunk
    of one *is* the per-subject discovery this replaced, so this is the two of them
    answering one fixture the same way; four puts the whole fixture in one chunk, and
    two and three cut across the only place it can go wrong: the second withdrawal's
    nearest deposit is the one the first withdrawal has just taken, so a pass that
    forgot what it had linked at a boundary would reach for a leg that is no longer
    free — and ``link_transfer`` refuses those outright.

    Both shapes of near-duplicate are here: the same amount on consecutive days,
    where the pick is by distance, and two pairs inside one day an hour apart, where
    the pick is by distance *and* by what the pass has already taken.
    """

    async def run(chunk: int) -> tuple[int, tuple]:
        hh = await household_factory(base="USD")
        async with scoped_session(household_id=hh) as s:
            a, b = await _accounts(s, hh, "USD", "USD")
            labels: dict = {}

            async def _mk(account, amount, when, label):
                txn = await txns.create_transaction(s, hh, TransactionCreate(
                    account_id=account.id, amount=D(amount), transacted_at=when))
                labels[txn.id] = label
                return txn

            # $20k each way on two consecutive days.
            day1_out = await _mk(a, "-20000", _at(2026, 1, 10, 9), "day1 out")
            day1_in = await _mk(b, "20000", _at(2026, 1, 10, 9), "day1 in")
            day2_out = await _mk(a, "-20000", _at(2026, 1, 11, 9), "day2 out")
            day2_in = await _mk(b, "20000", _at(2026, 1, 11, 9), "day2 in")
            # $900 each way, two withdrawals an hour apart and two deposits two
            # hours apart.
            out_1 = await _mk(a, "-900", _at(2026, 1, 20, 10), "out 1")
            out_2 = await _mk(a, "-900", _at(2026, 1, 20, 11), "out 2")
            in_1 = await _mk(b, "900", _at(2026, 1, 20, 11), "in 1")
            in_2 = await _mk(b, "900", _at(2026, 1, 20, 13), "in 2")

            monkeypatch.setattr(txns, "CANDIDATE_CHUNK", chunk)
            linked = await txns.auto_match_transfers(
                s, hh,
                [day1_out.id, day1_in.id, day2_out.id, day2_in.id,
                 out_1.id, out_2.id, in_1.id, in_2.id],
            )

            groups: dict = {}
            for txn_id, label in labels.items():
                group = (
                    await s.execute(
                        select(Transaction.transfer_group_id).where(
                            Transaction.id == txn_id
                        )
                    )
                ).scalar_one()
                if group is not None:
                    groups.setdefault(group, []).append(label)
            return linked, tuple(sorted(tuple(sorted(v)) for v in groups.values()))

    expected = (
        4,
        (
            ("day1 in", "day1 out"),
            ("day2 in", "day2 out"),
            ("in 1", "out 1"),
            ("in 2", "out 2"),
        ),
    )
    for chunk in (1, 2, 3, 4, 5, 8, txns.CANDIDATE_CHUNK):
        assert await run(chunk) == expected, f"chunk size {chunk}"


# ---- The wire contract ----------------------------------------------------


@pytest.fixture
async def client():
    from app.main import create_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


async def test_transfer_lifecycle_over_http(client):
    """The three paths the UI needs — link, candidates, unlink — plus the two
    things only the request layer decides: the ``days`` bound and the 404."""
    signup = await client.post(
        "/auth/signup",
        json={"email": f"{uuid.uuid4().hex[:8]}@example.com", "display_name": "Owner",
              "password": "password123", "household_name": "Transfers"},
    )
    assert signup.status_code == 201, signup.text
    csrf = signup.json()["csrf_token"]
    headers = {CSRF: csrf}
    # The session cookie is Secure outside dev, and httpx will not send a Secure
    # cookie to an http:// origin — so it is lifted onto the jar by hand, as the
    # cookie/CSRF client in test_api_owners.py does.
    client.cookies.set(SESSION_COOKIE, signup.cookies.get(SESSION_COOKIE))

    accts = []
    for name in ("Checking", "Savings"):
        resp = await client.post(
            "/accounts", json={"name": name, "type": "depository", "currency": "USD"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        accts.append(resp.json()["id"])

    made = []
    for account_id, amount in ((accts[0], "-500"), (accts[1], "500")):
        resp = await client.post(
            "/transactions",
            json={"account_id": account_id, "amount": amount,
                  "transacted_at": "2026-01-10T12:00:00Z"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        made.append(resp.json()["id"])
    out_id, in_id = made

    # Candidates: the counterpart, priced, in the shape the picker reads.
    #
    # Open signup joins whatever household already exists (ADR-0027), so this
    # request runs in a household that the rest of the session's HTTP tests have
    # also written to, and a suite running alongside this one can add rows
    # mid-flight. The assertion is therefore scoped to the accounts this test
    # made: what is being pinned is that the counterpart is found and priced, not
    # that the household happens to hold nothing else near this date.
    found = await client.get(f"/transactions/transfer-candidates?txn_id={out_id}")
    assert found.status_code == 200, found.text
    items = found.json()["items"]
    assert items, found.text
    assert items[0]["days_apart"] == 0           # nearest-first, and one is same-day
    mine = {i["transaction"]["id"]: i for i in items
            if i["transaction"]["account_id"] in set(accts)}
    assert set(mine) == {in_id}
    counterpart = mine[in_id]
    assert counterpart["days_apart"] == 0
    assert counterpart["fx_cost_base"] is None   # same currency: nothing to report
    assert counterpart["within_tolerance"] is True
    assert counterpart["transaction"]["currency"] == "USD"

    # The window is bounded on both sides, so a caller cannot ask for a scan whose
    # cost it cannot see.
    assert (await client.get(
        f"/transactions/transfer-candidates?txn_id={out_id}&days=0")).status_code == 422
    assert (await client.get(
        f"/transactions/transfer-candidates?txn_id={out_id}&days=31")).status_code == 422
    assert (await client.get(
        f"/transactions/transfer-candidates?txn_id={out_id}&days=30")).status_code == 200
    # The subject has to be named, and has to be one this household can see.
    assert (await client.get("/transactions/transfer-candidates")).status_code == 422
    assert (await client.get(
        f"/transactions/transfer-candidates?txn_id={uuid.uuid4()}")).status_code == 404

    linked = await client.post(
        "/transactions/transfers",
        json={"from_txn_id": out_id, "to_txn_id": in_id}, headers=headers,
    )
    assert linked.status_code == 201, linked.text
    group = linked.json()
    assert group["txn_ids"] == [out_id, in_id]
    assert group["matched_by"] == "manual"

    # A linked leg is no longer free to match, from either side — least of all
    # with the leg it is already grouped with.
    for txn_id in (out_id, in_id):
        after = await client.get(f"/transactions/transfer-candidates?txn_id={txn_id}")
        listed = {i["transaction"]["id"] for i in after.json()["items"]}
        assert not listed & {out_id, in_id}

    # A leg carries only its group id, so the detail view reads the pair back here.
    detail = await client.get(f"/transactions/transfers/{group['transfer_group_id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert {leg["id"] for leg in body["legs"]} == {out_id, in_id}
    assert body["fx_cost_base"] is None
    assert body["matched_by"] == "manual"
    assert (await client.get(
        f"/transactions/transfers/{uuid.uuid4()}")).status_code == 404

    removed = await client.delete(
        f"/transactions/transfers/{group['transfer_group_id']}", headers=headers)
    assert removed.status_code == 204
    assert (await client.delete(
        f"/transactions/transfers/{group['transfer_group_id']}",
        headers=headers)).status_code == 404
    assert (await client.delete(
        f"/transactions/transfers/{uuid.uuid4()}", headers=headers)).status_code == 404

    # The legs are back to being ordinary transactions.
    txn = (await client.get(f"/transactions/{out_id}")).json()
    assert txn["transfer_group_id"] is None
    # ...and matching is offered again.
    again = await client.get(f"/transactions/transfer-candidates?txn_id={out_id}")
    assert in_id in {i["transaction"]["id"] for i in again.json()["items"]}
