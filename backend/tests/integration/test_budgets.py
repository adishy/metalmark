"""Per-category budgets (ADR-0058): the plan, and the spend it is read against.

The property this file exists for is the one in the first test: **a budget's
``spent`` figures and the Spending report's figures are the same figures**. A
budget bar that disagrees with the report printed beside it is a worse outcome
than not having budgets at all, and the way it happens is a second query — so
the anchor here is that ``total_spent`` equals ``spending_by_category``'s total
over the same window, to the cent, for a month that contains a transfer, an
uncategorized charge, a split and an investment fee.

Everything after that is the edges: what a row means with no plan or no spend,
which categories may be planned for, what a period is, what a deleted category
takes with it, and that one household's plans are invisible to another.
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
from app.models import Budget, CategoryGroup, Transaction
from app.schemas.ledger import AccountCreate
from app.schemas.transactions import SplitIn, TransactionCreate
from app.services import budgets, ledger, reports
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal
CSRF = "X-CSRF-Token"

#: The month every unit-shaped case below writes into. A fixed month, not
#: "now", so a run in any month tests the same window.
SEPTEMBER = date(2026, 9, 1)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def _group(session, household_id, group_type: str, name: str) -> CategoryGroup:
    g = CategoryGroup(household_id=household_id, name=name, type=group_type)
    session.add(g)
    await session.flush()
    return g


async def _category(session, household_id, group_type: str, name: str, icon=None):
    g = await _group(session, household_id, group_type, group_type.title())
    return await ledger.create_category(session, household_id, g.id, name, icon, None, 0)


async def _account(session, household_id, name="Checking"):
    return await ledger.create_account(
        session, household_id,
        AccountCreate(name=name, type="depository", currency="USD"),
    )


async def _spend(session, household_id, account, amount, day, *, category_id=None,
                 description="x"):
    return await txns.create_transaction(
        session, household_id,
        TransactionCreate(
            account_id=account.id, amount=D(amount), transacted_at=_dt(2026, 9, day),
            category_id=category_id, description=description,
        ),
    )


# ---- the anchor: the two reports are one report ------------------------------


async def test_spent_agrees_with_the_spending_report_to_the_cent(household_factory):
    """The whole reason this feature is built on ``spending_by_category``.

    The month is deliberately awkward: a categorized charge, an uncategorized
    one, a linked transfer (excluded from both), a split parent whose category
    differs from its children's, and a charge on a transfer-typed category. Each
    of those is a place a hand-rolled sum would drift — and a drift here is a
    budget bar that contradicts the report on the next tab.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        groceries = await _category(s, hh, "expense", "Groceries")
        dining = await _category(s, hh, "expense", "Dining")
        shopping = await _category(s, hh, "expense", "Shopping")
        moving = await _category(s, hh, "transfer", "Transfer")
        acct = await _account(s, hh)
        other = await _account(s, hh, "Savings")

        await _spend(s, hh, acct, "-400", 3, category_id=groceries.id)
        await _spend(s, hh, acct, "-120", 4, category_id=dining.id)
        await _spend(s, hh, acct, "-75", 5, category_id=shopping.id)
        # No category at all: the Uncategorized sentinel, which no plan can cover.
        await _spend(s, hh, acct, "-42.5", 6)
        # A transfer, linked: out of both reports.
        out = await _spend(s, hh, acct, "-500", 7)
        into = await _spend(s, hh, other, "500", 7)
        await txns.link_transfer(s, hh, out.id, into.id)
        # A transfer-typed category: excluded by _load_entries, so it must be
        # excluded here too rather than showing up as unbudgeted spend.
        await _spend(s, hh, acct, "-60", 8, category_id=moving.id)
        # A split parent, categorized as Shopping, whose children are Dining.
        parent = await _spend(s, hh, acct, "-90", 9, category_id=shopping.id)
        await txns.replace_splits(s, parent.id, [
            SplitIn(amount=D("-50"), category_id=dining.id),
            SplitIn(amount=D("-40"), category_id=groceries.id),
        ])

        await budgets.set_budget(s, hh, groceries.id, SEPTEMBER, D("500"))
        await budgets.set_budget(s, hh, dining.id, SEPTEMBER, D("100"))
        # Shopping is left unplanned on purpose: it has spend, so it must appear
        # as a row with no plan rather than vanish.

        _base, rows, totals, _warn = await budgets.budget_report(s, hh, SEPTEMBER)
        _b, _srows, spend_total, _sw = await reports.spending_by_category(
            s, hh, SEPTEMBER, date(2026, 9, 30)
        )

    # The figure the app prints beside a budget is the figure the Spending
    # report prints for the same month.
    assert totals["total_spent"] == spend_total
    # Exactly the charges that survive every exclusion: 400 + 120 + 75 + 42.50 +
    # 90 (the split parent's children) — not the transfer legs, not the
    # transfer-typed 60.
    assert totals["total_spent"] == D("727.5000")

    # The three totals partition, and by construction rather than by rounding.
    assert totals["budgeted_spent"] + totals["unbudgeted_spent"] == totals["total_spent"]
    # 400 + 40 and 120 + 50: the split's legs land on the categories the split
    # named, not on the parent's own tag.
    assert totals["budgeted_spent"] == D("610.0000")
    assert totals["unbudgeted_spent"] == D("117.5000")  # Shopping 75 + uncategorized 42.50
    assert totals["total_budget"] == D("600")

    by_id = {r.category_id: r for r in rows}
    # A split parent reports through its children, so its two legs land on
    # Dining and Groceries and the parent's own Shopping tag does not — which is
    # also why Shopping's 75 is its own charge and not 75 + 90.
    assert by_id[groceries.id].spent == D("440.0000")
    assert by_id[dining.id].spent == D("170.0000")
    assert by_id[shopping.id].spent == D("75.0000")
    assert by_id[shopping.id].budget is None
    # The transfer-typed category is in neither report.
    assert moving.id not in by_id


# ---- what a row means --------------------------------------------------------


async def test_a_row_carries_the_plan_the_spend_or_both(household_factory):
    """Three states, three rows, and each one says which it is.

    A planned category with no spend is a row with ``spent == 0``; an unplanned
    category with spend is a row with ``budget is None``; a category with
    neither is not a row at all, because there is nothing to say about it.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        planned = await _category(s, hh, "expense", "Planned, unspent", icon="🛒")
        unplanned = await _category(s, hh, "expense", "Unplanned, spent")
        neither = await _category(s, hh, "expense", "Neither")
        acct = await _account(s, hh)
        await _spend(s, hh, acct, "-25", 3, category_id=unplanned.id)
        await budgets.set_budget(s, hh, planned.id, SEPTEMBER, D("300"))

        _base, rows, totals, _warn = await budgets.budget_report(s, hh, SEPTEMBER)

    by_id = {r.category_id: r for r in rows}
    assert by_id[planned.id].budget == D("300")
    # Zero, at the storage scale — not a bare ``Decimal("0")``, which would
    # reach the wire as ``"0"`` beside other rows' ``"25.0000"``.
    assert by_id[planned.id].spent == D("0.0000")
    assert by_id[planned.id].category_icon == "🛒"
    assert by_id[unplanned.id].budget is None
    assert by_id[unplanned.id].spent == D("25.0000")
    assert neither.id not in by_id
    # Planned rows first, then the spend nobody planned for.
    assert [r.category_id for r in rows] == [planned.id, unplanned.id]
    assert totals["unbudgeted_spent"] == D("25.0000")


async def test_a_month_with_no_rows_still_states_its_window(household_factory):
    """An empty period is a real answer, and it names the month it is about —
    a figure without its window is the bug this app has been burned by."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        base, rows, totals, warnings = await budgets.budget_report(s, hh, SEPTEMBER)

    assert rows == []
    assert warnings == []
    assert base == "USD"
    assert budgets.period_end(SEPTEMBER) == date(2026, 9, 30)
    assert totals == {
        "total_budget": D("0"),
        "total_spent": D("0"),
        "budgeted_spent": D("0"),
        "unbudgeted_spent": D("0"),
    }


async def test_an_overspent_category_is_reported_over_its_plan(household_factory):
    """Spend past the plan is not clamped anywhere. The row says what was spent
    and what was planned, and the reader subtracts — a server that capped
    ``spent`` at ``budget`` would be the app editing the ledger."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        dining = await _category(s, hh, "expense", "Dining")
        acct = await _account(s, hh)
        await _spend(s, hh, acct, "-180", 3, category_id=dining.id)
        await budgets.set_budget(s, hh, dining.id, SEPTEMBER, D("100"))

        _base, rows, totals, _warn = await budgets.budget_report(s, hh, SEPTEMBER)

    row = next(r for r in rows if r.category_id == dining.id)
    assert row.spent == D("180.0000")
    assert row.budget == D("100")
    assert totals["budgeted_spent"] == D("180.0000")


# ---- writing a plan ----------------------------------------------------------


async def test_set_is_an_upsert_on_the_category_and_period(household_factory):
    """The natural key is the row's identity, so setting twice leaves one row.

    Two rows for one category-month would make "what did we budget" have two
    answers, which is the failure the unique constraint is there to prevent.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        groceries = await _category(s, hh, "expense", "Groceries")
        await budgets.set_budget(s, hh, groceries.id, SEPTEMBER, D("500"))
        await budgets.set_budget(s, hh, groceries.id, SEPTEMBER, D("640"))

        stored = (
            await s.execute(select(Budget).where(Budget.category_id == groceries.id))
        ).scalars().all()

    assert len(stored) == 1
    assert stored[0].amount == D("640")


async def test_any_day_in_the_month_names_that_month(household_factory):
    """A period is a month, and the caller does not have to know that.

    The 15th and the 1st are the same plan — and the same plan is *not* October's.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        groceries = await _category(s, hh, "expense", "Groceries")
        row = await budgets.set_budget(s, hh, groceries.id, date(2026, 9, 15), D("500"))
        assert row.period == date(2026, 9, 1)

        # Read back through the middle of the month, too.
        _base, rows, _t, _w = await budgets.budget_report(s, hh, date(2026, 9, 28))
        assert [r.category_id for r in rows] == [groceries.id]

        _base, october, _t, _w = await budgets.budget_report(s, hh, date(2026, 10, 1))
        assert october == []


async def test_a_budget_is_refused_on_a_category_that_is_not_an_expense(household_factory):
    """``spending_by_category`` counts money *out*.

    A plan on an income category would be compared against a quantity the report
    does not compute — an income target is a floor to reach, not a cap to stay
    under — so the write is refused, and the message names the type it found
    rather than saying "invalid".
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        salary = await _category(s, hh, "income", "Salary")
        moving = await _category(s, hh, "transfer", "Transfer")

        for category, kind in ((salary, "income"), (moving, "transfer")):
            with pytest.raises(LedgerError) as exc:
                await budgets.set_budget(s, hh, category.id, SEPTEMBER, D("100"))
            assert exc.value.status == 422
            assert kind in exc.value.message

        with pytest.raises(LedgerError) as missing:
            await budgets.set_budget(s, hh, uuid.uuid4(), SEPTEMBER, D("100"))
        assert missing.value.status == 404


async def test_clear_removes_the_plan_and_leaves_the_ledger_alone(household_factory):
    """Clearing is a statement about a period, not about the money.

    It is also idempotent: the state a second "Clear" asks for is the state that
    already holds, so it is not an error. A category that does not exist still
    is, because that is a wrong id rather than a request to remove nothing.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        groceries = await _category(s, hh, "expense", "Groceries")
        acct = await _account(s, hh)
        txn = await _spend(s, hh, acct, "-50", 3, category_id=groceries.id)
        await budgets.set_budget(s, hh, groceries.id, SEPTEMBER, D("200"))

        await budgets.clear_budget(s, groceries.id, SEPTEMBER)
        # Twice: nothing to remove is not a failure.
        await budgets.clear_budget(s, groceries.id, SEPTEMBER)

        _base, rows, totals, _warn = await budgets.budget_report(s, hh, SEPTEMBER)
        still_there = (
            await s.execute(select(Transaction.amount).where(Transaction.id == txn.id))
        ).scalar_one()

        with pytest.raises(LedgerError) as exc:
            await budgets.clear_budget(s, uuid.uuid4(), SEPTEMBER)

    assert exc.value.status == 404
    assert still_there == D("-50.0000")
    assert totals["total_budget"] == D("0")
    # The category still has its spend, now as a row with no plan.
    row = next(r for r in rows if r.category_id == groceries.id)
    assert row.budget is None
    assert row.spent == D("50.0000")


async def test_deleting_the_category_takes_the_budget_and_not_the_ledger(household_factory):
    """What the ``CASCADE`` is for.

    ``SET NULL`` — what the transaction tables use — would raise here, because
    ``category_id`` is ``NOT NULL``: a plan for a category that no longer exists
    is not a state, it is a dangling pointer. So the plan goes with the category,
    while the transactions that were filed under it are moved, not deleted
    (that is ``delete_category``'s own job, and it happens first).
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        groceries = await _category(s, hh, "expense", "Groceries")
        acct = await _account(s, hh)
        await _spend(s, hh, acct, "-50", 3, category_id=groceries.id)
        await budgets.set_budget(s, hh, groceries.id, SEPTEMBER, D("200"))

        await ledger.delete_category(s, groceries.id)
        await s.flush()

        left = (
            await s.execute(select(Budget).where(Budget.category_id == groceries.id))
        ).scalars().all()
        # The charge survives, uncategorized.
        _base, rows, totals, _warn = await budgets.budget_report(s, hh, SEPTEMBER)

    assert left == []
    assert totals["total_spent"] == D("50.0000")
    assert totals["unbudgeted_spent"] == D("50.0000")
    assert all(r.category_id != groceries.id for r in rows)


async def test_one_households_plans_are_invisible_to_another(household_factory):
    """RLS, the same rule every other household table carries (ADR-0014/0025)."""
    mine = await household_factory(name="Mine")
    theirs = await household_factory(name="Theirs")
    async with scoped_session(household_id=mine) as s:
        cat = await _category(s, mine, "expense", "Groceries")
        await budgets.set_budget(s, mine, cat.id, SEPTEMBER, D("500"))

    async with scoped_session(household_id=theirs) as s:
        assert (await s.execute(select(Budget))).scalars().all() == []
        _base, rows, totals, _warn = await budgets.budget_report(s, theirs, SEPTEMBER)
        assert rows == []
        assert totals["total_budget"] == D("0")


# ---- over HTTP: the three routes --------------------------------------------

#: Methods the CSRF middleware guards (``deps._check_csrf``).
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class _Api:
    """A cookie- and CSRF-aware client, so these tests read like the frontend's.

    ``test_api_owners`` has the full one; this file needs three routes and one
    sign-in, and importing across test modules would couple this file to that
    one's internals for no gain (the call ``test_rules`` made for the same
    reason).
    """

    def __init__(self, app):
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self._token: str | None = None
        self.csrf: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def login(self, email, password="password123"):
        resp = await self._http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        self._token = resp.cookies[SESSION_COOKIE]
        self.csrf = resp.json()["csrf_token"]
        return resp.json()

    async def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.csrf and method in UNSAFE:
            headers[CSRF] = self.csrf
        if self._token:
            self._http.cookies.set(SESSION_COOKIE, self._token)
        return await self._http.request(method, url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


@pytest.fixture
async def api(household_factory):
    """A signed-in owner client, with a household created for this test alone.

    Built through ``bootstrap_household`` and **logged in** over HTTP, rather than
    signing up. Signup is open (ADR-0027) and *joins the oldest household*; it
    only creates one when there is none. So on a database shared with the rest of
    the suite — which is what the harness gives every test — a test that signed up
    would be reading and writing whatever household ran before it. That is exactly
    how this file first reported one household's budgets under another's cookie:
    the request was authenticated, RLS was working, and the household was simply
    the wrong one. Creating the household directly leaves other tests' data alone.
    """
    from app.main import create_app

    email = f"{uuid.uuid4().hex[:8]}@example.com"
    await household_factory(name="Budgets over HTTP", base="USD", email=email)
    async with _Api(create_app()) as client:
        await client.login(email)
        yield client


async def test_the_routes_set_read_and_clear_a_month(api):
    """Set, read, clear — and the two things only the request layer decides: the
    422 for a non-expense category and the 204 on a clear with nothing to clear."""
    group = await api.post(
        "/category-groups", json={"name": "Everyday", "type": "expense"}
    )
    assert group.status_code == 201, group.text
    gid = group.json()["id"]
    cat = await api.post("/categories", json={"name": "🛒 Groceries", "group_id": gid})
    assert cat.status_code == 201, cat.text
    cid = cat.json()["id"]
    income_group = await api.post(
        "/category-groups", json={"name": "Earned", "type": "income"}
    )
    income_cat = await api.post(
        "/categories", json={"name": "Salary", "group_id": income_group.json()["id"]}
    )

    put = await api.put(
        f"/budgets/{cid}", json={"amount": "600"}, params={"period": "2026-09-01"}
    )
    assert put.status_code == 200, put.text
    # Money crosses the wire as a string (ADR-0005), never a float.
    assert put.json() == {"category_id": cid, "period": "2026-09-01", "amount": "600.0000"}

    got = await api.get("/budgets", params={"period": "2026-09-01"})
    assert got.status_code == 200, got.text
    body = got.json()
    # The window is stated on every read, both ends.
    assert (body["period_start"], body["period_end"]) == ("2026-09-01", "2026-09-30")
    assert body["rows"] == [{
        "category_id": cid, "category_name": "🛒 Groceries", "category_icon": None,
        "budget": "600.0000", "spent": "0.0000",
    }]
    assert body["total_budget"] == "600.0000"
    assert body["attribution"] == "row"

    # An income category is refused, and told what it is.
    refused = await api.put(
        f"/budgets/{income_cat.json()['id']}", json={"amount": "100"},
        params={"period": "2026-09-01"},
    )
    assert refused.status_code == 422, refused.text
    assert "income" in refused.json()["detail"]

    # A negative plan is a typo, refused at the edge.
    negative = await api.put(
        f"/budgets/{cid}", json={"amount": "-5"}, params={"period": "2026-09-01"}
    )
    assert negative.status_code == 422

    cleared = await api.delete(f"/budgets/{cid}", params={"period": "2026-09-01"})
    assert cleared.status_code == 204
    assert not cleared.content
    # Idempotent: nothing left to clear is the state the caller asked for.
    again = await api.delete(f"/budgets/{cid}", params={"period": "2026-09-01"})
    assert again.status_code == 204

    # A category that does not exist is a wrong id, not an empty clear.
    missing = await api.delete(f"/budgets/{uuid.uuid4()}", params={"period": "2026-09-01"})
    assert missing.status_code == 404


async def test_the_report_defaults_to_this_month(api):
    """A reader can open the page and see "this month" without working out what
    month it is — and the response says which month it decided on."""
    from app.services.ledger import today as ledger_today

    got = await api.get("/budgets")
    assert got.status_code == 200, got.text
    first = ledger_today().replace(day=1)
    assert got.json()["period_start"] == first.isoformat()
    assert got.json()["period_end"] >= got.json()["period_start"]


async def test_a_budget_field_the_agent_mirror_exposes_is_policy_covered():
    """The anonymized mirror (ADR-0048) reads the app's own route table.

    This is the cheap end-to-end proof that the router is in ``APP_ROUTERS`` and
    that the field walker has a policy for every field a budget response carries:
    the unit walker is the exhaustive check, and this is the one that would catch
    the route never having been registered at all.
    """
    from app.main import APP_ROUTERS

    assert any(r.prefix == "/budgets" for r in APP_ROUTERS)

    from app.agent.policies import REGISTRY
    from app.schemas.budgets import BudgetOut, BudgetReport, BudgetRow

    for model in (BudgetOut, BudgetRow, BudgetReport):
        for field in model.model_fields:
            assert (model, field) in REGISTRY, f"{model.__name__}.{field} has no policy"
