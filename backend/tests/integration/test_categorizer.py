"""The auto-categorizer and the starter categories it files into.

Private and local: every test here runs with no network, because the
categorizer has none to use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.db import scoped_session
from app.models import Category, CategoryGroup
from app.models.ledger import Transaction
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import TransactionCreate, TransactionUpdate
from app.services import categorizer, ledger
from app.services import transactions as txns
from app.services.default_categories import DEFAULT_CATEGORIES, install_defaults

# The API tests share test_api_owners' cookie- and CSRF-aware client, and its
# per-test truncation (signup's "first user creates the household" is one-shot).
from tests.integration.test_api_owners import (  # noqa: F401 - fixtures
    _clean_identity,
    _signup,
    client,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def hh(household_factory):
    return await household_factory()


def _dt(d: int) -> datetime:
    return datetime(2026, 9, d, 12, tzinfo=UTC)


async def _world(session, hh):
    await install_defaults(session, hh)
    checking = await ledger.create_account(
        session, hh, AccountCreate(name="Checking", type="depository", currency="USD")
    )
    card = await ledger.create_account(
        session, hh, AccountCreate(name="Card", type="credit", currency="USD")
    )
    names = dict(
        (await session.execute(select(Category.name, Category.id))).all()
    )
    return checking, card, names


async def _txn(session, hh, account_id, amount, merchant, *, day=5, description=None):
    return await txns.create_transaction(
        session, hh,
        TransactionCreate(
            account_id=account_id, amount=D(amount), transacted_at=_dt(day),
            merchant=merchant, description=description or merchant,
        ),
    )


# ---- the starter set -----------------------------------------------------------


async def test_install_defaults_is_typed_iconed_and_only_for_an_empty_household(hh):
    async with scoped_session(hh) as s:
        added = await install_defaults(s, hh)
        assert added == sum(len(c) for _g, _t, c in DEFAULT_CATEGORIES)
        rows = (await s.execute(
            select(Category.name, Category.icon, CategoryGroup.type)
            .join(CategoryGroup, CategoryGroup.id == Category.group_id)
        )).all()
        by_name = {n: (i, t) for n, i, t in rows}
        assert by_name["Paychecks"][1] == "income"
        assert by_name["Transfer"][1] == "transfer"
        assert by_name["Credit Card Payment"][1] == "transfer"
        assert by_name["Groceries"][1] == "expense"
        assert all(icon for icon, _t in by_name.values()), "every category has an emoji"
        # A second call is a no-op: the household has categories now.
        assert await install_defaults(s, hh) == 0


def test_the_starter_names_are_unique():
    names = [n for _g, _t, cats in DEFAULT_CATEGORIES for n, _i in cats]
    assert len(names) == len(set(names))


def test_every_keyword_names_a_starter_category():
    names = {n for _g, _t, cats in DEFAULT_CATEGORIES for n, _i in cats}
    assert {n for n, _p in categorizer._KEYWORDS} <= names


# ---- suggestions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("merchant", "amount", "expected"),
    [
        ("SAFEWAY #1234", "-54.20", "Groceries"),
        ("TRADER JOE'S #552", "-31.00", "Groceries"),
        ("STARBUCKS STORE 0042", "-6.45", "Bars & Coffee Shops"),
        ("DOORDASH*THAI PALACE", "-38.10", "Restaurants"),
        ("UBER *TRIP", "-17.80", "Taxi & Ride Shares"),
        ("UBER EATS", "-25.00", "Restaurants"),
        ("CHEVRON 0091", "-48.00", "Gas"),
        ("NETFLIX.COM", "-15.49", "Subscription – Entertainment"),
        ("GITHUB, INC.", "-4.00", "Subscription – Dev / SaaS"),
        ("MULLVAD VPN", "-5.00", "Subscription – Privacy"),
        ("NYTIMES DIGITAL", "-4.00", "Subscription – News"),
        ("STEAMPOWERED.COM", "-19.99", "Video Games"),
        ("NAMECHEAP.COM", "-10.98", "Domain Registration"),
        ("USCIS ELIS FEE", "-470.00", "Immigration"),
        ("ACME CORP PAYROLL", "3230.71", "Paychecks"),
        ("INTEREST PAYMENT", "1.19", "Interest"),
        ("PAYMENT - THANK YOU", "905.15", "Credit Card Payment"),
        ("ONLINE TRANSFER TO SAVINGS", "-500.00", "Transfer"),
        ("FIDELITY MONEYLINE BROKERAGE", "-20000.00", "Transfer"),
        ("ZELLE TO SOMEONE", "-60.00", "Other People"),
        ("ATM WITHDRAWAL 1234", "-100.00", "Cash & ATM"),
    ],
)
async def test_keywords_file_common_merchants(hh, merchant, amount, expected):
    async with scoped_session(hh) as s:
        checking, _card, names = await _world(s, hh)
        t = await _txn(s, hh, checking.id, amount, merchant)
        cat = await categorizer.load(s)
        choice = cat.suggest(t)
        assert choice is not None, merchant
        assert choice.category_id == names[expected], merchant


async def test_short_keywords_do_not_match_inside_words(hh):
    """'aws' is not in 'LAWSON', 'gas' is not in 'VEGAS'."""
    async with scoped_session(hh) as s:
        checking, _card, _names = await _world(s, hh)
        cat = await categorizer.load(s)
        for merchant in ("LAWSON STORE", "LAS VEGAS SOUVENIR"):
            t = await _txn(s, hh, checking.id, "-5", merchant)
            choice = cat.suggest(t)
            assert choice is None, merchant


async def test_the_sign_constrains_the_type(hh):
    """A refund from a grocery store is not income filed as groceries-shaped
    income, and a payroll word on money out is not a paycheck."""
    async with scoped_session(hh) as s:
        checking, _card, names = await _world(s, hh)
        cat = await categorizer.load(s)
        refund = await _txn(s, hh, checking.id, "12.00", "SAFEWAY REFUND")
        assert cat.suggest(refund) is None
        out = await _txn(s, hh, checking.id, "-300", "PAYROLL SERVICE FEE")
        choice = cat.suggest(out)
        assert choice is None or choice.category_id != names["Paychecks"]


async def test_history_teaches_a_custom_category(hh):
    """Once a human files a merchant under their own category, it follows —
    even over a keyword, and for a merchant string that differs only in noise."""
    async with scoped_session(hh) as s:
        checking, _card, _names = await _world(s, hh)
        group = await ledger.create_category_group(s, hh, "Mine", "expense", 99)
        beans = await ledger.create_category(s, hh, group.id, "Coffee Beans", None, None, 0)
        first = await _txn(s, hh, checking.id, "-18", "SQ *BLUE BOTTLE COFFEE #12")
        await txns.update_transaction(
            s, hh, first.id, TransactionUpdate(category_id=beans.id)
        )
        later = await _txn(s, hh, checking.id, "-22", "BLUE BOTTLE COFFEE 0042", day=9)
        cat = await categorizer.load(s)
        choice = cat.suggest(later)
        assert choice is not None
        assert (choice.category_id, choice.reason) == (beans.id, "history")


async def test_a_custom_category_matches_its_own_name(hh):
    async with scoped_session(hh) as s:
        checking, _card, _names = await _world(s, hh)
        group = await ledger.create_category_group(s, hh, "Mine", "expense", 99)
        pottery = await ledger.create_category(s, hh, group.id, "Pottery", None, None, 0)
        t = await _txn(s, hh, checking.id, "-80", "CLAYWORKS POTTERY STUDIO")
        choice = (await categorizer.load(s)).suggest(t)
        assert choice is not None and choice.category_id == pottery.id


# ---- writing -------------------------------------------------------------------


async def test_categorize_blank_fills_only_blanks_and_marks_auto(hh):
    async with scoped_session(hh) as s:
        checking, _card, names = await _world(s, hh)
        blank = await _txn(s, hh, checking.id, "-54", "SAFEWAY #1")
        # A different merchant: a human's Safeway would be history for the blank.
        mine = await _txn(s, hh, checking.id, "-54", "WHOLE FOODS #2")
        await txns.update_transaction(
            s, hh, mine.id, TransactionUpdate(category_id=names["Gifts"])
        )
        changed = await categorizer.categorize_blank(s, [blank.id, mine.id])
        assert changed == 1
        await s.refresh(blank)
        await s.refresh(mine)
        assert blank.category_id == names["Groceries"]
        assert blank.field_sources["category"] == "auto"
        assert mine.category_id == names["Gifts"]  # a human's choice is never touched


async def test_categorize_all_links_transfers_overwrites_and_keeps_unknowns(hh):
    async with scoped_session(hh) as s:
        checking, card, names = await _world(s, hh)
        # An earlier wrong guess by the categorizer itself — not a human's choice,
        # which would be history and would (rightly) be followed.
        wrong = await _txn(s, hh, checking.id, "-54", "SAFEWAY #1")
        wrong.category_id = names["Gifts"]
        wrong.field_sources = {**(wrong.field_sources or {}), "category": "auto"}
        await s.flush()
        unknown = await _txn(s, hh, checking.id, "-9", "QWXZ LLC")
        await txns.update_transaction(
            s, hh, unknown.id, TransactionUpdate(category_id=names["Books"])
        )
        pay_out = await _txn(s, hh, checking.id, "-905.15", "CHASE CARD")
        pay_in = await _txn(s, hh, card.id, "905.15", "PAYMENT RECEIVED")

        result = await categorizer.categorize_all(s, hh)
        assert result.transfers_linked == 1
        for row in (wrong, unknown, pay_out, pay_in):
            await s.refresh(row)
        assert wrong.category_id == names["Groceries"]  # overwritten, as warned
        assert unknown.category_id == names["Books"]  # no guess: kept, not blanked
        assert pay_out.transfer_group_id is not None
        assert pay_out.category_id == pay_in.category_id == names["Credit Card Payment"]


async def test_sync_path_is_wired(hh):
    """``categorize_blank`` is what sync calls; a sanity check that it tolerates
    an empty batch and a batch of already-categorized rows."""
    async with scoped_session(hh) as s:
        assert await categorizer.categorize_blank(s, []) == 0
        checking, _card, names = await _world(s, hh)
        t = await _txn(s, hh, checking.id, "-5", "SAFEWAY")
        assert await categorizer.categorize_blank(s, [t.id]) == 1
        assert await categorizer.categorize_blank(s, [t.id]) == 0
        row = (await s.execute(select(Transaction).where(Transaction.id == t.id))).scalar_one()
        assert row.category_id == names["Groceries"]


# ---- the API -------------------------------------------------------------------


async def test_signup_gives_the_household_the_starter_set(client):  # noqa: F811 - the imported fixture
    await _signup(client)
    names = {c["name"] for c in (await client.get("/categories")).json()}
    assert {"Groceries", "Transfer", "Paychecks", "Rent"} <= names
    icons = {c["name"]: c["icon"] for c in (await client.get("/categories")).json()}
    assert icons["Groceries"] == "🛒"


async def test_a_category_can_be_renamed_and_re_emojied(client):  # noqa: F811 - the imported fixture
    await _signup(client)
    cat = next(c for c in (await client.get("/categories")).json() if c["name"] == "Books")
    resp = await client.patch(f"/categories/{cat['id']}", json={"icon": "📕", "name": "Reading"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["icon"], resp.json()["name"]) == ("📕", "Reading")
    assert (await client.patch(f"/categories/{cat['id']}", json={"name": None})).status_code == 422


async def test_auto_categorize_all_is_for_admins(client):  # noqa: F811 - the imported fixture
    await _signup(client)
    resp = await client.post("/categories/auto-categorize-all")
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {
        "examined", "categorized", "changed", "left_blank", "transfers_linked",
    }

