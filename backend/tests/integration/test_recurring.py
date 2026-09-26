"""Recurring series (ADR-0053): what a series matches, what it totals, and the
detector's suggestions as the routes see them.

The claims worth testing here are the ones that decide whether the feature is
honest about the ledger:

* a series owns nothing — deleting it leaves every transaction alone, and the
  occurrence count it shows is derived from rows, not stored beside them;
* the count and the "already tracked" check are the same predicate, so a series
  and the suggestions it suppresses cannot disagree;
* accepting a suggestion creates exactly the series that suggestion described;
* totals never add one currency to another.

The routes get their own section at the end, because a status code, the CSRF
gate and the 404-vs-422 split live only in the request path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import scoped_session
from app.deps import SESSION_COOKIE
from app.models import RecurringSeries
from app.schemas.ledger import AccountCreate
from app.schemas.recurring import RecurringCreate, RecurringUpdate
from app.schemas.transactions import TransactionCreate
from app.services import ledger
from app.services import recurring as svc
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal


def _dt(y, m, d, hour=12):
    return datetime(y, m, d, hour, tzinfo=UTC)


async def _account(session, household_id, name="Checking", currency="USD"):
    return await ledger.create_account(
        session, household_id,
        AccountCreate(name=name, type="depository", currency=currency),
    )


async def _category(session, household_id, name="Subscriptions"):
    group = await ledger.create_category_group(session, household_id, "Expense", "expense", 0)
    return await ledger.create_category(session, household_id, group.id, name, None, None, 0)


async def _txn(session, household_id, account_id, amount, *, text="Streaming", **kw):
    """A transaction as a person or a bank leaves one: text in ``merchant`` when
    a provider or a rule wrote it, in ``description`` when it was typed."""
    txn = await txns.create_transaction(
        session, household_id,
        TransactionCreate(
            account_id=account_id, amount=D(amount),
            transacted_at=kw.pop("transacted_at", _dt(2026, 1, 5)),
            description=kw.pop("description", None if kw.get("merchant") else text),
            **kw,
        ),
    )
    return txn


async def _series(session, household_id, **kw):
    # A name is the one thing ``create`` will not invent, so the helper supplies
    # one — but never when the call is seeding from a picked transaction, where a
    # supplied name would be the explicit-wins path under test.
    if "name" not in kw and "transaction_id" not in kw:
        kw["name"] = "Streaming"
    kw.setdefault("cadence", "monthly")
    return await svc.create(session, household_id, RecurringCreate(**kw))


# ---- creating, seeding, validating -------------------------------------------


async def test_a_series_can_be_created_from_a_picked_transaction(household_factory):
    """The "pick a transaction" path: the row supplies everything the body left
    blank, so a client that picked one sends the cadence and the id."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        category = await _category(s, hh)
        picked = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                            category_id=category.id)
        series = await _series(s, hh, transaction_id=picked.id, cadence="monthly")
        assert series.name == "NETFLIX.COM"
        assert series.merchant == "NETFLIX.COM"
        assert series.amount == D("-12.9900")
        assert series.currency == "USD"
        assert series.account_id == acct.id
        assert series.category_id == category.id
        assert series.transaction_id == picked.id
        assert series.is_active is True


async def test_what_the_body_sends_explicitly_wins_over_the_seed(household_factory):
    """Accepting a suggestion posts it back whole, and the person's edit — a new
    name, a different cadence, a merchant filter they narrowed — is the point of
    the request, not something the seed may overwrite."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        picked = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM")
        series = await _series(
            s, hh, transaction_id=picked.id, name="Netflix", merchant="netflix",
            cadence="annual", amount=D("-155.88"), next_due_date=date(2026, 3, 1),
        )
        assert series.name == "Netflix"
        assert series.merchant == "netflix"
        assert series.cadence == "annual"
        assert series.amount == D("-155.8800")
        assert series.next_due_date == date(2026, 3, 1)
        # Unstated, so still from the row: the account it belongs to.
        assert series.account_id == acct.id


async def test_a_series_with_neither_a_name_nor_an_amount_is_a_422(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(LedgerError) as no_name:
            await _series(s, hh, name="", amount=D("-5"))
        assert no_name.value.status == 422
        with pytest.raises(LedgerError) as no_amount:
            await _series(s, hh, name="Rent")
        assert no_amount.value.status == 422


async def test_a_unknown_account_category_or_transaction_is_a_404(household_factory):
    hh = await household_factory()
    ghost = uuid.uuid4()
    async with scoped_session(household_id=hh) as s:
        for kw in ({"account_id": ghost}, {"category_id": ghost}, {"transaction_id": ghost}):
            with pytest.raises(LedgerError) as exc:
                await _series(s, hh, amount=D("-5"), **kw)
            assert exc.value.status == 404, kw


async def test_a_pick_from_another_households_ledger_is_a_404(household_factory):
    """RLS is what makes this true, not a check in the service — the row is not
    visible in this session at all."""
    a = await household_factory()
    b = await household_factory()
    async with scoped_session(household_id=b) as s:
        b_acct = await _account(s, b, name="B Checking")
        b_txn = await _txn(s, b, b_acct.id, "-9.99")
    async with scoped_session(household_id=a) as s:
        with pytest.raises(LedgerError) as exc:
            await _series(s, a, amount=D("-5"), transaction_id=b_txn.id)
        assert exc.value.status == 404


async def test_a_cadence_the_app_does_not_know_is_refused_at_the_schema():
    with pytest.raises(ValidationError):
        RecurringCreate(cadence="fortnightly")
    with pytest.raises(ValidationError):
        RecurringUpdate(cadence="fortnightly")


# ---- what a series matches ---------------------------------------------------


async def test_occurrences_come_from_the_ledger_and_ignore_hidden_and_pending_rows(
    household_factory,
):
    """The count is derived on every read (ADR-0035/0053): five matching charges,
    two of which a person hid or the bank has not posted yet, is three."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        for month in (1, 2, 3):
            await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                       transacted_at=_dt(2026, month, 5))
        hidden = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                            transacted_at=_dt(2026, 4, 5))
        hidden.is_hidden = True
        await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                   transacted_at=_dt(2026, 5, 5), is_pending=True)
        # A different merchant, the wrong direction, and another account: none of
        # them are occurrences.
        await _txn(s, hh, acct.id, "-12.99", merchant="SPOTIFY", transacted_at=_dt(2026, 4, 6))
        await _txn(s, hh, acct.id, "12.99", merchant="NETFLIX.COM", transacted_at=_dt(2026, 4, 7))
        other = await _account(s, hh, name="Card")
        await _txn(s, hh, other.id, "-12.99", merchant="NETFLIX.COM", transacted_at=_dt(2026, 4, 8))
        await s.flush()

        series = await _series(s, hh, merchant="NETFLIX.COM", amount=D("-12.99"),
                               account_id=acct.id)
        out = await svc.to_out(s, series)
        assert out.occurrences == 3
        assert out.last_seen_date == date(2026, 3, 5)


async def test_a_series_with_no_merchant_matches_everything_on_its_account(
    household_factory,
):
    """The "the rent leaves this account monthly" series: a person who does not
    want to type the payee's spelling gets the account-wide version, and the
    direction still separates the money in from the money out."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        await _txn(s, hh, acct.id, "-1500.00", merchant="LANDLORD", transacted_at=_dt(2026, 1, 1))
        await _txn(s, hh, acct.id, "-60.00", merchant="GYM", transacted_at=_dt(2026, 1, 2))
        await _txn(s, hh, acct.id, "2500.00", merchant="PAYROLL", transacted_at=_dt(2026, 1, 3))
        await s.flush()

        out = await svc.to_out(
            s, await _series(s, hh, name="Rent", merchant=None, amount=D("-1500.00"),
                             account_id=acct.id)
        )
        assert out.occurrences == 2  # the rent and the gym, both money out
        assert out.monthly_amount == D("-1500.00")


async def test_monthly_amount_scales_the_cadence_not_the_amount(household_factory):
    """A weekly bill and an annual one are comparable in the same list because the
    monthly figure is derived from the cadence, not from what is stored."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        weekly = await _series(s, hh, name="Cleaner", amount=D("-100.00"), cadence="weekly")
        annual = await _series(s, hh, name="Insurance", amount=D("-1200.00"), cadence="annual")
        assert (await svc.to_out(s, weekly)).monthly_amount == D("-433.33")
        assert (await svc.to_out(s, annual)).monthly_amount == D("-100.00")


async def test_totals_are_per_currency_and_the_base_currency_leads(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        dollars = await _account(s, hh, name="Dollars")
        euros = await _account(s, hh, name="Euros", currency="EUR")
        await _series(s, hh, name="Rent", amount=D("-1500.00"), account_id=dollars.id)
        await _series(s, hh, name="Pay", amount=D("2500.00"), account_id=dollars.id,
                      cadence="biweekly")
        await _series(s, hh, name="Flat", amount=D("-900.00"), account_id=euros.id)
        await s.flush()

        listed = await svc.list_series(s, hh)
        assert [t.currency for t in listed.totals] == ["USD", "EUR"]
        usd = listed.totals[0]
        assert usd.monthly_in == D("5416.67")  # 2500 × 26/12
        assert usd.monthly_out == D("-1500.00")
        assert usd.net_monthly == D("3916.67")
        # Nothing was converted: the euros are counted in euros, in their own row.
        assert listed.totals[1].monthly_out == D("-900.00")
        assert listed.totals[1].net_monthly == D("-900.00")


# ---- listing, filtering, changing, removing ----------------------------------


async def test_the_list_filters_by_account_category_direction_and_name(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        other = await _account(s, hh, name="Card")
        category = await _category(s, hh)
        await _series(s, hh, name="Netflix", merchant="NETFLIX",
                      amount=D("-12.99"), account_id=acct.id,
                      category_id=category.id)
        await _series(s, hh, name="Salary", merchant="PAYROLL", amount=D("2500.00"),
                      account_id=acct.id, cadence="biweekly")
        await _series(s, hh, name="Gym", merchant="GYM", amount=D("-60.00"),
                      account_id=other.id, is_active=False)

        by_account = await svc.list_series(s, hh, account_id=acct.id)
        assert [i.name for i in by_account.items] == ["Netflix", "Salary"]
        by_category = await svc.list_series(s, hh, category_id=category.id)
        assert [i.name for i in by_category.items] == ["Netflix"]
        money_in = await svc.list_series(s, hh, direction="in")
        assert [i.name for i in money_in.items] == ["Salary"]
        money_out = await svc.list_series(s, hh, direction="out")
        assert [i.name for i in money_out.items] == ["Netflix", "Gym"]
        paused = await svc.list_series(s, hh, is_active=False)
        assert [i.name for i in paused.items] == ["Gym"]
        found = await svc.list_series(s, hh, q="netfl")
        assert [i.name for i in found.items] == ["Netflix"]
        assert (await svc.list_series(s, hh)).items[0].name == "Netflix"  # active first

        # The totals follow the filter — and never count a paused series, even
        # when it is the filter that asked for them: pausing is what stops a
        # charge being counted, and the row says so.
        assert [(t.currency, t.monthly_in, t.monthly_out, t.net_monthly)
                for t in by_account.totals] == [
            ("USD", D("5416.67"), D("-12.99"), D("5403.68")),
        ]
        assert [(t.monthly_in, t.monthly_out) for t in paused.totals] == []
        assert [(t.monthly_in, t.monthly_out) for t in money_out.totals] == [
            (D("0.00"), D("-12.99")),
        ]


async def test_patch_leaves_absent_fields_alone_and_clears_on_an_explicit_null(
    household_factory,
):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        series = await _series(s, hh, name="Netflix", merchant="NETFLIX",
                               amount=D("-12.99"), account_id=acct.id,
                               next_due_date=date(2026, 3, 5))
        updated = await svc.update(s, series.id, RecurringUpdate(cadence="annual"))
        assert updated.cadence == "annual"
        assert updated.merchant == "NETFLIX"        # untouched: absent
        assert updated.next_due_date == date(2026, 3, 5)

        cleared = await svc.update(
            s, series.id, RecurringUpdate.model_validate({"merchant": None, "next_due_date": None})
        )
        assert cleared.merchant is None
        assert cleared.next_due_date is None
        assert cleared.amount == D("-12.9900")      # still untouched

        paused = await svc.update(s, series.id, RecurringUpdate(is_active=False))
        assert paused.is_active is False


async def test_a_patch_cannot_empty_the_name(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        series = await _series(s, hh, amount=D("-5.00"))
        with pytest.raises(LedgerError) as exc:
            await svc.update(s, series.id, RecurringUpdate(name="   "))
        assert exc.value.status == 422
        assert (await svc.get(s, series.id)).name == "Streaming"


async def test_moving_a_series_to_another_account_follows_its_currency(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        dollars = await _account(s, hh, name="Dollars")
        euros = await _account(s, hh, name="Euros", currency="EUR")
        series = await _series(s, hh, amount=D("-12.99"), account_id=dollars.id)
        moved = await svc.update(s, series.id, RecurringUpdate(account_id=euros.id))
        assert moved.currency == "EUR"
        # And an explicit currency still wins over the account's.
        overridden = await svc.update(s, series.id, RecurringUpdate(currency="gbp"))
        assert overridden.currency == "GBP"


async def test_deleting_a_series_leaves_every_transaction_alone(household_factory):
    """ADR-0053's promise, and the reason a series has no ledger rows of its
    own: it describes transactions, it does not hold them."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                         category_id=(await _category(s, hh)).id)
        series = await _series(s, hh, merchant="NETFLIX.COM", amount=D("-12.99"),
                               account_id=acct.id, transaction_id=txn.id)
        await svc.delete(s, series.id)
        await s.flush()
        assert (await s.execute(select(RecurringSeries))).scalars().all() == []
        await s.refresh(txn)
        assert txn.amount == D("-12.9900")
        assert txn.category_id is not None


async def test_deleting_the_picked_transaction_does_not_delete_the_series(household_factory):
    """``transaction_id`` is provenance, and its foreign key is SET NULL: a
    charge that gets ducked or re-imported away must not take the person's list
    with it (0014's live-data case states the same thing in SQL)."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM")
        series = await _series(s, hh, merchant="NETFLIX.COM", amount=D("-12.99"),
                               account_id=acct.id, transaction_id=txn.id)
        series_id = series.id
        await txns.delete_transaction(s, txn.id)
        await s.flush()
        # The database nulled the reference (ON DELETE SET NULL); expiring drops
        # the stale value SQLAlchemy still holds in the identity map. The id is
        # read out first because expiring makes reading it a load of its own.
        s.expire_all()
        surviving = await svc.get(s, series_id)
        assert surviving.transaction_id is None
        assert surviving.name == "NETFLIX.COM"


async def test_series_are_invisible_across_households(household_factory):
    a = await household_factory()
    b = await household_factory()
    async with scoped_session(household_id=a) as s:
        series = await _series(s, a, amount=D("-12.99"), name="A only")
    async with scoped_session(household_id=b) as s:
        assert (await svc.list_series(s, b)).items == []
        with pytest.raises(LedgerError) as exc:
            await svc.get(s, series.id)
        assert exc.value.status == 404


# ---- suggestions, as the service sees them -----------------------------------


async def test_suggestions_describe_a_real_monthly_run_and_accepting_one_creates_it(
    household_factory,
):
    """The whole loop: the ledger shows a pattern, the route offers it, and the
    body it offers is exactly what ``create`` needs to make the series real."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        category = await _category(s, hh)
        for month in (1, 2, 3):
            await _txn(s, hh, acct.id, "-420.00", text="Supermarket",
                       transacted_at=_dt(2026, month, 5), category_id=category.id)
        # A one-off, to prove the detector is not simply listing transactions.
        await _txn(s, hh, acct.id, "-160.00", text="Department store",
                   transacted_at=_dt(2026, 2, 22))
        await s.flush()

        offered = await svc.suggestions(s)
        assert [x.name for x in offered] == ["Supermarket"]
        suggestion = offered[0]
        assert suggestion.cadence == "monthly"
        assert suggestion.occurrences == 3
        assert suggestion.amount == D("-420.00")
        assert suggestion.account_id == acct.id
        assert suggestion.category_id == category.id

        series = await svc.create(
            s, hh, RecurringCreate(**suggestion.model_dump(include=set(
                RecurringCreate.model_fields
            )))
        )
        await s.flush()
        assert series.amount == suggestion.amount
        assert series.transaction_id == suggestion.transaction_id
        # And now the detector stops asking: the series answers it.
        assert await svc.suggestions(s) == []


async def test_suggestions_skip_transactions_inside_a_series_that_already_covers_them(
    household_factory,
):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        for month in (1, 2, 3):
            await _txn(s, hh, acct.id, "-420.00", text="Supermarket",
                       transacted_at=_dt(2026, month, 5))
        await s.flush()
        assert len(await svc.suggestions(s)) == 1
        # A series created by hand, without the suggestion ever being accepted.
        await _series(s, hh, name="Groceries", merchant="Supermarket", amount=D("-420.00"),
                      account_id=acct.id)
        await s.flush()
        assert await svc.suggestions(s) == []


# ---- the routes over HTTP ---------------------------------------------------

CSRF = "X-CSRF-Token"
_UNSAFE = {"POST", "PATCH", "PUT", "DELETE"}


class _Api:
    """A signed-in client, in miniature — the same shape ``test_rules`` uses."""

    def __init__(self, app):
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self._cookie: str | None = None
        self.csrf: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()

    async def login(self, email, password="password123"):
        resp = await self.http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        self._cookie = resp.cookies[SESSION_COOKIE]
        self.csrf = resp.json()["csrf_token"]
        # The session lives on the client rather than being re-set per request,
        # so a request sent through ``self.http`` without the CSRF header is
        # still a signed-in one — which is what the CSRF gate's test needs to
        # tell a missing token (403) apart from no session at all (401).
        self.http.cookies.set(SESSION_COOKIE, self._cookie)
        return resp.json()

    async def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.csrf and method in _UNSAFE:
            headers[CSRF] = self.csrf
        return await self.http.request(method, url, headers=headers, **kw)


@pytest.fixture
async def api():
    """A signed-in owner client with a household of its own (see ``test_rules``
    for why this is built through ``bootstrap_household``)."""
    from app.db import unscoped_session
    from app.main import create_app
    from app.services import auth as auth_svc

    email = f"{uuid.uuid4().hex[:8]}@example.com"
    async with unscoped_session() as s:
        household, user = await auth_svc.bootstrap_household(
            s, name="Recurring over HTTP", base_currency="USD", owner_email=email,
            owner_name="Alex", owner_password="password123",
        )
    async with _Api(create_app()) as client:
        await client.login(email)
        yield client, household.id, user.id


async def test_the_routes_add_change_and_remove_a_series(api):
    client, hh, _user = api
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        category = await _category(s, hh)
        picked = await _txn(s, hh, acct.id, "-12.99", merchant="NETFLIX.COM",
                            transacted_at=_dt(2026, 1, 5))
        picked_id, acct_id, category_id = picked.id, acct.id, category.id

    created = await client.request(
        "POST", "/recurring",
        json={"transaction_id": str(picked_id), "cadence": "monthly",
              "next_due_date": "2026-03-05"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    series_id = body["id"]
    assert body["name"] == "NETFLIX.COM"
    assert body["amount"] == "-12.9900"
    assert body["monthly_amount"] == "-12.99"
    assert body["occurrences"] == 1
    assert body["last_seen_date"] == "2026-01-05"
    assert body["account_id"] == str(acct_id)

    listed = (await client.request("GET", "/recurring")).json()
    assert [i["id"] for i in listed["items"]] == [series_id]
    assert listed["totals"] == [{"currency": "USD", "monthly_in": "0.00",
                                 "monthly_out": "-12.99", "net_monthly": "-12.99"}]

    # The filters the tab's pills drive.
    assert (await client.request("GET", f"/recurring?account_id={acct_id}")).json()["items"]
    assert (await client.request(
        "GET", f"/recurring?category_id={category_id}"
    )).json()["items"] == []
    assert (await client.request("GET", "/recurring?direction=in")).json()["items"] == []
    assert (await client.request("GET", "/recurring?q=netflix")).json()["items"]
    assert (await client.request("GET", "/recurring?direction=sideways")).status_code == 422

    patched = await client.request(
        "PATCH", f"/recurring/{series_id}",
        json={"cadence": "annual", "merchant": None, "is_active": False},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["cadence"] == "annual"
    assert patched.json()["merchant"] is None
    assert patched.json()["is_active"] is False
    assert patched.json()["amount"] == "-12.9900"

    single = await client.request("GET", f"/recurring/{series_id}")
    assert single.json()["id"] == series_id
    # Annual, so the monthly figure is a twelfth of the charge — and the row
    # itself still shows what a person pays, unchanged.
    assert single.json()["monthly_amount"] == "-1.08"

    deleted = await client.request("DELETE", f"/recurring/{series_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert (await client.request("GET", f"/recurring/{series_id}")).status_code == 404
    assert (await client.request("GET", "/recurring")).json()["items"] == []


async def test_a_series_that_does_not_exist_is_a_404_not_a_500(api):
    client, _hh, _user = api
    ghost = uuid.uuid4()
    assert (await client.request("GET", f"/recurring/{ghost}")).status_code == 404
    assert (await client.request("PATCH", f"/recurring/{ghost}",
                                 json={"cadence": "monthly"})).status_code == 404
    assert (await client.request("DELETE", f"/recurring/{ghost}")).status_code == 404


async def test_the_routes_refuse_an_unknown_cadence_and_a_write_without_csrf(api):
    client, _hh, _user = api
    no_csrf = await client.http.post(
        "/recurring", json={"name": "Rent", "amount": "-1500.00", "cadence": "monthly"}
    )
    assert no_csrf.status_code == 403

    body = await client.request(
        "POST", "/recurring",
        json={"name": "Rent", "amount": "-1500.00", "cadence": "fortnightly"},
    )
    assert body.status_code == 422
    assert (await client.request(
        "GET", "/recurring"
    )).json()["items"] == []


async def test_the_suggestions_route_answers_with_what_the_ledger_shows(api):
    client, hh, _user = api
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        for month in (1, 2, 3):
            await _txn(s, hh, acct.id, "-420.00", text="Supermarket",
                       transacted_at=_dt(2026, month, 5))
        await s.flush()

    offered = (await client.request("GET", "/recurring/suggestions")).json()
    assert [x["name"] for x in offered] == ["Supermarket"]
    suggestion = offered[0]
    assert suggestion["cadence"] == "monthly"
    assert suggestion["occurrences"] == 3
    assert suggestion["first_date"] == "2026-01-05"
    assert suggestion["last_date"] == "2026-03-05"

    # Accepting it is posting it back: every field the route needs is in the
    # suggestion (``transaction_id`` is the newest matching charge).
    accepted = await client.request(
        "POST", "/recurring",
        json={k: suggestion[k] for k in
              ("name", "merchant", "account_id", "category_id", "amount", "currency",
               "cadence", "next_due_date", "transaction_id")},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["name"] == "Supermarket"
    assert accepted.json()["occurrences"] == 3
    assert (await client.request("GET", "/recurring/suggestions")).json() == []


async def test_the_routes_are_closed_to_anonymous_callers():
    from app.main import create_app

    async with _Api(create_app()) as stranger:
        assert (await stranger.request("GET", "/recurring")).status_code == 401
        assert (await stranger.request("GET", "/recurring/suggestions")).status_code == 401


async def test_a_member_may_see_and_change_what_the_household_tracks(api):
    """A series is household data like a category, not a control-panel setting
    (ADR-0026/0053): the ledger is member-visible, so what annotates it is too."""
    from app.db import unscoped_session
    from app.models import HouseholdMember

    client, hh, user_id = api
    async with unscoped_session() as s:
        membership = await s.get(HouseholdMember, (hh, user_id))
        membership.role = "member"

    created = await client.request(
        "POST", "/recurring", json={"name": "Rent", "amount": "-1500.00", "cadence": "monthly"}
    )
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]
    assert (await client.request("GET", "/recurring")).status_code == 200
    assert (await client.request("PATCH", f"/recurring/{series_id}",
                                 json={"is_active": False})).status_code == 200
    assert (await client.request("DELETE", f"/recurring/{series_id}")).status_code == 204


async def test_a_manual_series_needs_no_account_and_defaults_to_the_household_currency(
    household_factory,
):
    hh = await household_factory(base="CAD")
    async with scoped_session(household_id=hh) as s:
        series = await _series(s, hh, name="Home insurance", amount=D("-840.00"),
                               cadence="annual")
        assert series.account_id is None
        assert series.currency == "CAD"
        assert (await svc.list_series(s, hh)).totals[0].monthly_out == D("-70.00")
