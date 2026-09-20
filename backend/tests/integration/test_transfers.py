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
            _b, months = await reports.cash_flow_series(
                s, hh, date(2026, 1, 1), date(2026, 1, 31))
            _b, _rows, spend = await reports.spending_by_category(
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


# ---- Candidates -----------------------------------------------------------


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
