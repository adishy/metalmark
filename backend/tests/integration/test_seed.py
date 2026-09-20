"""The demo ledger reconciles — the seed's own acceptance bar.

``app/seed.py`` claims its five months are *coherent*: every account's snapshot is
its opening balance plus that account's own flows, so the net-worth series
decomposes into cash flow and a currency-revaluation figure that is attributable to
FX alone. Nothing verified that claim, and it is exactly the claim a report bug
breaks — a transaction the reports never see leaves the identity intact (revaluation
is computed as a residual) while turning revaluation into a number that means
nothing. So these assertions do not trust the series: they recompute the terms from
the tables with direct SQL, and compare.

Not a substitute for the manual pass (PLAN.md Phase 3). It is the part of that pass
that can be a machine's job.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import scoped_session
from app.models import Account, Category, CategoryGroup, Transaction
from app.seed import DEMO_FX_RATE, DEMO_OPENING, _demo_ledger, demo_reference_data
from app.services import ledger, reports

pytestmark = pytest.mark.integration

D = Decimal
OWNER_NAME = "Owner"
EURO_NATIVE = DEMO_OPENING["euro"]


async def _seeded(household_factory):
    """A household carrying the demo ledger, plus the period it covers.

    The demo is the only thing in this household, which is what lets the assertions
    below use "every row in the ledger" as ground truth and skip the question of
    range bounds entirely.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await demo_reference_data(s, hh, OWNER_NAME)
        demo = await _demo_ledger(s, hh, OWNER_NAME)
    return hh, demo


async def _transfer_categories(session) -> set:
    """Category ids whose group is a transfer group.

    The category's kind lives on its group, so this is the same join the reports
    use. Sharing it is fine: what these tests check is the *row accounting*, not the
    category vocabulary.
    """
    return set(
        (
            await session.execute(
                select(Category.id).join(
                    CategoryGroup, Category.group_id == CategoryGroup.id
                ).where(CategoryGroup.type == "transfer")
            )
        )
        .scalars()
        .all()
    )


async def _cash_flow_from_tables(session) -> Decimal:
    """Σ base_amount over every row the report should count, read directly.

    Independent of ``services.reports`` on purpose: the point is to compare the
    report against the ledger rather than against itself. Transfer-category rows are
    excluded (both the unlinked and the linked demo pair carry "Transfer"), and a
    split contributes through its children, whose ``base_amount``s sum to the
    parent's exactly.
    """
    transfer_cats = await _transfer_categories(session)
    rows = (
        await session.execute(select(Transaction).options(selectinload(Transaction.splits)))
    ).scalars().all()

    total = Decimal("0")
    for t in rows:
        if t.splits:
            total += sum(
                (c.base_amount for c in t.splits if c.base_amount is not None), Decimal("0")
            )
        elif t.category_id not in transfer_cats:
            total += t.base_amount or Decimal("0")
    return total


async def test_demo_ledger_reports_every_row_it_created(household_factory):
    """The report's cash flow equals the ledger's, computed independently.

    This is the assertion the range-boundary bug failed: a transaction posted after
    midnight on the range's last day was invisible to every report, so the reported
    cash flow came up short by its amount while ``ΔNW = CF + revaluation`` still
    held — the identity is a residual, and cannot notice a missing row.
    """
    hh, demo = await _seeded(household_factory)
    async with scoped_session(household_id=hh) as s:
        expected = await _cash_flow_from_tables(s)
        series = await reports.net_worth_series(s, hh, demo["start"], demo["end"])

    assert series["net_cash_flow"] == expected
    # ...and the demo is worth reporting on at all, so the equality cannot pass by
    # both sides being zero.
    assert expected > D("1000")


async def test_demo_ledger_revaluation_is_only_currency(household_factory):
    """``currency_revaluation`` is the euro balance's FX move, to the cent.

    The euro account holds the same 2000 EUR all period and nothing touches it, so
    the only thing that can move its base value is the rate. Anything else arriving
    here — a dropped row, a mis-signed leg, a split that does not sum — lands in
    this number, because revaluation is a residual.
    """
    hh, demo = await _seeded(household_factory)
    async with scoped_session(household_id=hh) as s:
        series = await reports.net_worth_series(s, hh, demo["start"], demo["end"])

    # base -> EUR is quoted EUR per unit of base, so base value = native / rate.
    expected = EURO_NATIVE / DEMO_FX_RATE["latest"] - EURO_NATIVE / DEMO_FX_RATE["opening"]

    assert abs(series["currency_revaluation"] - expected) < D("0.02")


async def test_demo_ledger_net_worth_is_the_accounts_it_opened(household_factory):
    """The four seeded accounts, no unconverted currency, and the contract intact."""
    hh, demo = await _seeded(household_factory)
    async with scoped_session(household_id=hh) as s:
        accounts = (await s.execute(select(Account))).scalars().all()
        series = await reports.net_worth_series(s, hh, demo["start"], demo["end"])
        nw = await ledger.net_worth(s, hh)

    assert {a.id for a in accounts} == set(demo["accounts"].values())
    assert nw["unconverted_currencies"] == []
    assert series["delta_net_worth"] == (
        series["net_cash_flow"] + series["currency_revaluation"]
    )
    # The owner filter is account-scoped here, which the response says out loud.
    assert series["attribution"] == "account"


async def test_demo_ledger_offers_one_linked_and_one_unlinked_transfer(household_factory):
    """The linked pair is excluded from cash flow *because it is linked*.

    Two pairs are seeded on purpose — one linked, one left for a human to match in
    the UI — and the difference between them is the demonstration that exclusion is
    a property of the link, not of the rows. Were the unlinked pair excluded too,
    the transfer vertical would have nothing to show, and were the linked pair
    included, cash flow would double-count a move between two of the household's own
    accounts.
    """
    hh, _demo = await _seeded(household_factory)
    async with scoped_session(household_id=hh) as s:
        transfer_cats = await _transfer_categories(s)
        rows = (
            await s.execute(
                select(Transaction).where(Transaction.category_id.in_(transfer_cats))
            )
        ).scalars().all()

    linked = [t for t in rows if t.transfer_group_id is not None]
    unlinked = [t for t in rows if t.transfer_group_id is None]

    assert len(linked) == 2, "the demo links exactly one pair"
    assert len({t.transfer_group_id for t in linked}) == 1
    assert len({t.account_id for t in linked}) == 2, "a transfer crosses two accounts"
    assert sum(t.amount for t in linked) == 0

    assert len(unlinked) == 2, "and leaves exactly one pair for the picker to find"
    assert len({t.account_id for t in unlinked}) == 2
    assert sum(t.amount for t in unlinked) == 0
