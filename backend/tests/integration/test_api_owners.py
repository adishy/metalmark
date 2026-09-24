"""The ownership seam over HTTP (ADR-0026/0027).

Service tests can't reach three things that only exist in the request path: the
CSRF/role gates, `effective_owner_id` (computed at serialization, never stored),
and signup's first-user-creates-the-household rule. Those are what this covers.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import httpx
import pytest

from app.deps import SESSION_COOKIE

pytestmark = pytest.mark.integration

CSRF = "X-CSRF-Token"
UNSAFE = {"POST", "PATCH", "DELETE", "PUT"}


class Client:
    """A cookie- and CSRF-aware client, so tests read like the frontend's calls."""

    def __init__(self, app):
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self._token: str | None = None
        self._csrf: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def request(self, method: str, url: str, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self._csrf and method in UNSAFE:
            headers[CSRF] = self._csrf
        if self._token:
            self._http.cookies.set(SESSION_COOKIE, self._token)
        resp = await self._http.request(method, url, headers=headers, **kw)
        if SESSION_COOKIE in resp.cookies:
            self._token = resp.cookies[SESSION_COOKIE]
        if resp.status_code < 400 and resp.headers.get("content-type", "").startswith(
            "application/json"
        ):
            body = resp.json()
            if isinstance(body, dict) and body.get("csrf_token"):
                self._csrf = body["csrf_token"]
        return resp

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


@pytest.fixture(autouse=True)
def _clean_identity():
    """Start every test here with no users and no households.

    With open signup, "the first signup creates the household" is a one-shot
    property of the database, and every test in this file shares one. Truncating
    between tests is what lets that assertion mean the same thing wherever it
    appears instead of depending on test order.
    """
    import psycopg

    dsn = (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        # CASCADE follows the foreign keys, so the whole ledger goes with it.
        conn.execute("TRUNCATE TABLE users, households CASCADE")
    yield


@pytest.fixture
async def client():
    # Imported here, not at module scope: `create_app()` reads settings, and the
    # test database env is only set once the session fixture has run.
    from app.main import create_app

    async with Client(create_app()) as c:
        yield c


async def _signup(client, *, email=None, name="Alex", household_name="Home"):
    email = email or f"{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "display_name": name, "password": "password123",
              "household_name": household_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- Signup (ADR-0027) -----------------------------------------------------


async def test_first_signup_creates_the_household_as_admin(client):
    me = await _signup(client, household_name="The Shyleshes")
    assert me["role"] == "owner"
    assert me["user"]["is_admin"] is True
    assert me["household_name"] == "The Shyleshes"
    assert me["base_currency"] == "USD"
    assert me["csrf_token"]

    # And the household came with its Shared owner, before anything was created.
    owners = (await client.get("/owners")).json()
    assert [(o["name"], o["kind"]) for o in owners] == [("Shared", "shared")]


async def test_second_signup_joins_the_existing_household(client):
    first = await _signup(client, name="Alex", household_name="Home")

    # A second client, so the first session is not disturbed.
    from app.main import create_app

    async with Client(create_app()) as other:
        second = await _signup(other, name="Beth", household_name="Somewhere Else")

        assert second["household_id"] == first["household_id"]
        assert second["household_name"] == "Home"  # the name it asked for is ignored
        assert second["role"] == "member"
        assert second["user"]["is_admin"] is False

        # One Shared owner for the household, not one per member.
        assert [o["name"] for o in (await other.get("/owners")).json()] == ["Shared"]


async def test_duplicate_email_signup_is_rejected(client):
    me = await _signup(client)
    resp = await client.post(
        "/auth/signup",
        json={"email": me["user"]["email"], "display_name": "Clone",
              "password": "password123"},
    )
    assert resp.status_code == 409


async def test_closed_signup_is_forbidden(client):
    """METALMARK_OPEN_SIGNUP=false is the self-hosted-lockdown switch."""
    from app.main import create_app
    from app.settings import get_settings

    previous = os.environ.get("METALMARK_OPEN_SIGNUP")
    os.environ["METALMARK_OPEN_SIGNUP"] = "false"
    get_settings.cache_clear()
    try:
        async with Client(create_app()) as closed:
            resp = await closed.post(
                "/auth/signup",
                json={"email": f"{uuid.uuid4().hex[:8]}@example.com",
                      "display_name": "Nope", "password": "password123"},
            )
        assert resp.status_code == 403
    finally:
        if previous is None:
            os.environ.pop("METALMARK_OPEN_SIGNUP", None)
        else:
            os.environ["METALMARK_OPEN_SIGNUP"] = previous
        get_settings.cache_clear()


# ---- Owner CRUD over HTTP --------------------------------------------------


async def test_owner_crud_round_trip(client):
    await _signup(client)

    created = await client.post("/owners", json={"name": "Alex"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "person" and body["name"] == "Alex"

    # Shared sorts first regardless of creation order.
    assert [o["name"] for o in (await client.get("/owners")).json()] == ["Shared", "Alex"]

    renamed = await client.patch(f"/owners/{body['id']}", json={"name": "Alexander"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Alexander"

    duplicate = await client.post("/owners", json={"name": "  alexander "})
    assert duplicate.status_code == 409

    deleted = await client.delete(f"/owners/{body['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {
        "reassigned_accounts": 0, "reassigned_transactions": 0, "reassigned_splits": 0,
    }
    assert [o["name"] for o in (await client.get("/owners")).json()] == ["Shared"]


async def test_shared_owner_cannot_be_deleted_over_http(client):
    await _signup(client)
    shared = (await client.get("/owners")).json()[0]
    assert shared["kind"] == "shared"
    resp = await client.delete(f"/owners/{shared['id']}")
    assert resp.status_code == 409


async def test_delete_reassigns_rows_and_reports_the_counts(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()
    shared = (await client.get("/owners")).json()[0]

    acct = (await client.post("/accounts", json={
        "name": "Alex Card", "type": "credit", "currency": "USD",
        "current_balance": "0", "owner_id": alex["id"],
    })).json()
    assert acct["owner_id"] == alex["id"]
    txn = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10", "transacted_at": "2026-01-05T00:00:00Z",
        "owner_id": alex["id"],
    })).json()

    resp = await client.delete(f"/owners/{alex['id']}")
    assert resp.status_code == 200
    assert resp.json()["reassigned_accounts"] == 1
    assert resp.json()["reassigned_transactions"] == 1

    # Both the account and the row it holds now point at Shared.
    assert (await client.get(f"/accounts/{acct['id']}")).json()["owner_id"] == shared["id"]
    moved = (await client.get(f"/transactions/{txn['id']}")).json()
    assert moved["owner_id"] == shared["id"]
    assert moved["effective_owner_id"] == shared["id"]


async def test_owner_writes_require_the_owner_role(client):
    """A member may read the owner list — the pickers and filters need it — but
    every write is owner-only. Gating the delete alone left a one-way door: a
    member could create an owner they had no way to remove."""
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()

    from app.main import create_app

    async with Client(create_app()) as member:
        await _signup(member, name="Beth")
        assert (await member.get("/owners")).status_code == 200

        assert (await member.post("/owners", json={"name": "Carol"})).status_code == 403
        assert (await member.patch(
            f"/owners/{alex['id']}", json={"name": "Not Alex"})).status_code == 403
        assert (await member.delete(f"/owners/{alex['id']}")).status_code == 403

        # None of the refusals changed anything.
        names = [o["name"] for o in (await client.get("/owners")).json()]
        assert names == ["Shared", "Alex"]


async def test_unknown_owner_is_404_not_a_foreign_key_error(client):
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD", "current_balance": "0",
    })).json()
    txn = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z",
    })).json()
    ghost = uuid.uuid4()

    assert (await client.patch(f"/owners/{ghost}", json={"name": "Ghost"})).status_code == 404
    # Not the foreign key answering with a 500: every write path resolves first.
    assert (await client.post("/accounts", json={
        "name": "Ghost Card", "type": "credit", "currency": "USD",
        "current_balance": "0", "owner_id": str(ghost),
    })).status_code == 404
    assert (await client.patch(f"/accounts/{acct['id']}",
                              json={"owner_id": str(ghost)})).status_code == 404
    assert (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z", "owner_id": str(ghost),
    })).status_code == 404
    assert (await client.patch(f"/transactions/{txn['id']}",
                              json={"owner_id": str(ghost)})).status_code == 404
    assert (await client.put(f"/transactions/{txn['id']}/splits", json=[
        {"amount": "-10", "owner_id": str(ghost)},
    ])).status_code == 404


async def test_another_households_owner_id_is_404_too(client, household_factory):
    """The RLS-scoped lookup turns a cross-tenant id into "not found".

    A second household can only be built out of band — open signup joins the
    existing one — so this one comes from the factory.
    """
    from app.db import scoped_session
    from app.services import owners as owner_svc

    await _signup(client)  # the household the request will run in
    other_hh = await household_factory(name="Them")
    async with scoped_session(household_id=other_hh) as s:
        theirs = await owner_svc.create_owner(s, other_hh, name="Them")

    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD", "current_balance": "0",
    })).json()
    assert (await client.patch(f"/accounts/{acct['id']}",
                              json={"owner_id": str(theirs.id)})).status_code == 404
    assert [o["name"] for o in (await client.get("/owners")).json()] == ["Shared"]


async def test_owner_routes_are_closed_to_the_unauthenticated(client):
    assert (await client.get("/owners")).status_code == 401
    assert (await client.post("/owners", json={"name": "Alex"})).status_code == 401
    assert (await client.get("/household")).status_code == 401


# ---- The computed owner on responses ---------------------------------------


async def test_effective_owner_is_computed_for_transactions_and_splits(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()
    beth = (await client.post("/owners", json={"name": "Beth"})).json()
    shared = (await client.get("/owners")).json()[0]

    acct = (await client.post("/accounts", json={
        "name": "Joint Card", "type": "credit", "currency": "USD",
        "current_balance": "0", "owner_id": shared["id"],
    })).json()

    # No owner on the row -> the account's.
    inheriting = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z",
    })).json()
    assert inheriting["owner_id"] is None
    assert inheriting["effective_owner_id"] == shared["id"]

    # The row's own owner wins.
    owned = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-20",
        "transacted_at": "2026-01-06T00:00:00Z", "owner_id": beth["id"],
    })).json()
    assert owned["effective_owner_id"] == beth["id"]

    # Each split child is resolved on its own, including the one that inherits.
    split_txn = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-30",
        "transacted_at": "2026-01-07T00:00:00Z", "owner_id": beth["id"],
    })).json()
    split = (await client.put(f"/transactions/{split_txn['id']}/splits", json=[
        {"amount": "-10", "owner_id": alex["id"]}, {"amount": "-20"},
    ])).json()
    assert {(Decimal(s["amount"]), s["effective_owner_id"]) for s in split["splits"]} == {
        (Decimal("-10"), alex["id"]),
        (Decimal("-20"), beth["id"]),  # inherits the parent transaction, not the account
    }

    # And the list endpoint computes it the same way.
    page = (await client.get("/transactions")).json()
    by_id = {t["id"]: t for t in page["items"]}
    assert by_id[inheriting["id"]]["effective_owner_id"] == shared["id"]
    assert {
        (Decimal(s["amount"]), s["effective_owner_id"]) for s in by_id[split["id"]]["splits"]
    } == {(Decimal("-10"), alex["id"]), (Decimal("-20"), beth["id"])}


async def test_owner_filter_narrows_the_transaction_list(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()
    shared = (await client.get("/owners")).json()[0]
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "0", "owner_id": shared["id"],
    })).json()
    alex_acct = (await client.post("/accounts", json={
        "name": "Alex Chk", "type": "depository", "currency": "USD",
        "current_balance": "0", "owner_id": alex["id"],
    })).json()

    mine = (await client.post("/transactions", json={
        "account_id": alex_acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z",
    })).json()
    theirs = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-20",
        "transacted_at": "2026-01-06T00:00:00Z",
    })).json()

    assert [t["id"] for t in (await client.get(
        "/transactions", params={"owner_id": alex["id"]})).json()["items"]] == [mine["id"]]
    assert [t["id"] for t in (await client.get(
        "/transactions", params={"owner_id": shared["id"]})).json()["items"]] == [theirs["id"]]
    # The account filter is the account's own owner, plainly.
    assert [a["id"] for a in (await client.get(
        "/accounts", params={"owner_id": alex["id"]})).json()] == [alex_acct["id"]]


# ---- Explicit null semantics over the wire ---------------------------------


async def test_transaction_bare_null_owner_clears_it(client):
    await _signup(client)
    beth = (await client.post("/owners", json={"name": "Beth"})).json()
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "0",
    })).json()
    txn = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z", "owner_id": beth["id"],
    })).json()

    # Omitted: no change.
    assert (await client.patch(f"/transactions/{txn['id']}",
                              json={"notes": "hi"})).json()["owner_id"] == beth["id"]
    # Explicit null: cleared, and it falls back to the account's owner.
    cleared = (await client.patch(f"/transactions/{txn['id']}", json={"owner_id": None})).json()
    assert cleared["owner_id"] is None
    assert cleared["effective_owner_id"] == (await client.get(
        f"/accounts/{acct['id']}")).json()["owner_id"]


async def test_account_bare_null_owner_is_422(client):
    """An account always has an owner; the picker has no empty option."""
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD", "current_balance": "0",
    })).json()
    assert (await client.patch(f"/accounts/{acct['id']}",
                              json={"owner_id": None})).status_code == 422
    # Required fields are not clearable either — that would be an IntegrityError.
    assert (await client.patch(f"/accounts/{acct['id']}",
                              json={"name": None})).status_code == 422
    assert (await client.patch(f"/accounts/{acct['id']}",
                              json={"current_balance": None})).status_code == 422


async def test_transaction_required_fields_are_not_clearable(client):
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD", "current_balance": "0",
    })).json()
    txn = (await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10",
        "transacted_at": "2026-01-05T00:00:00Z",
    })).json()
    for field in ("amount", "transacted_at", "is_hidden", "review_status"):
        resp = await client.patch(f"/transactions/{txn['id']}", json={field: None})
        assert resp.status_code == 422, f"{field} accepted a null"


# ---- Household -------------------------------------------------------------


async def test_household_rename(client):
    await _signup(client, household_name="Home")
    resp = await client.patch("/household", json={"name": "The Shyleshes"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "The Shyleshes"
    assert (await client.get("/household")).json()["name"] == "The Shyleshes"


async def test_household_rename_requires_the_owner_role(client):
    await _signup(client)
    from app.main import create_app

    async with Client(create_app()) as member:
        await _signup(member, name="Beth")
        assert (await member.patch("/household", json={"name": "Hijacked"})).status_code == 403


async def test_base_currency_cannot_be_patched(client):
    """Immutable once set (ADR-0017) — and loudly so, not a silent no-op."""
    await _signup(client)
    resp = await client.patch("/household", json={"base_currency": "EUR"})
    assert resp.status_code == 422
    assert (await client.get("/household")).json()["base_currency"] == "USD"


# ---- Reports ---------------------------------------------------------------

#: Every report endpoint. Kept as one list so a fourth cannot be added without the
#: window, attribution and owner-filter sweeps below covering it — each of which
#: exists because a report once shipped without that property.
REPORTS = (
    "/reports/net-worth",
    "/reports/cash-flow",
    "/reports/cash-flow/sankey",
    "/reports/spending",
)


async def test_net_worth_response_declares_its_attribution(client):
    await _signup(client)
    await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-01-01",
    })
    body = (await client.get(
        "/reports/net-worth", params={"start": "2026-01-01", "end": "2026-01-31"})).json()
    assert body["attribution"] == "account"
    assert body["points"][-1]["net_worth"] == "100.0000"
    assert body["delta_net_worth"] == "0.0000"


async def test_the_sankey_totals_the_same_window_the_bars_do(client):
    """Two endpoints over one quantity, compared through the wire.

    The service tests pin the arithmetic; this pins the *route* — that the graph
    reads the same window, under the same owner filter, and comes back with
    figures that reconcile against the bars beside them. A graph is more
    convincing than a bar chart and would be believed over one, so the two are
    held to each other rather than trusted to agree.

    ``expense`` is where the two shapes part company: the series carries it
    negative, the graph positive, because a Sankey encodes direction by which side
    a node sits on. That is the one difference, and it is asserted rather than
    glossed.
    """
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-01-01",
    })).json()
    group = (await client.post("/category-groups", json={
        "name": "Income", "type": "income"})).json()
    salary = (await client.post("/categories", json={
        "group_id": group["id"], "name": "Salary"})).json()
    for amount, day, cat in (
        ("2000", "2026-02-05", salary["id"]),
        ("-120", "2026-02-09", None),
        ("-45", "2026-03-01", None),
    ):
        await client.post("/transactions", json={
            "account_id": acct["id"], "amount": amount,
            "transacted_at": f"{day}T12:00:00Z", "category_id": cat,
        })

    window = {"start": "2026-01-01", "end": "2026-03-31"}
    bars = (await client.get("/reports/cash-flow", params=window)).json()
    graph = (await client.get("/reports/cash-flow/sankey", params=window)).json()
    donut = (await client.get("/reports/spending", params=window)).json()

    def bars_total(field: str) -> Decimal:
        return sum((Decimal(p[field]) for p in bars["points"]), Decimal("0"))

    assert graph["attribution"] == bars["attribution"] == donut["attribution"] == "row"
    assert graph["base_currency"] == bars["base_currency"] == donut["base_currency"]
    assert (graph["start"], graph["end"]) == (donut["start"], donut["end"]) == (
        "2026-01-01", "2026-03-31",
    )
    # The bars open at the first transaction instead (they would be empty
    # before it), so they cover the same money over a shorter axis.
    assert (bars["start"], bars["end"]) == ("2026-02-05", "2026-03-31")
    assert Decimal(graph["total_income"]) == bars_total("income") == Decimal("2000")
    # The one place the two shapes part company, asserted rather than glossed: the
    # series carries expense negative, the graph carries it positive.
    assert Decimal(graph["total_expense"]) == -bars_total("expense") == Decimal("165")
    assert Decimal(graph["net"]) == bars_total("net")
    # All three row-scoped reports over one window, and the spending total is the
    # expense side of the others rather than a fourth opinion: they are built on
    # one call (`_load_entries`), and this is what says so through the wire.
    assert Decimal(donut["total"]) == Decimal(graph["total_expense"])
    assert Decimal(donut["total"]) == -bars_total("expense")
    # Every row carries its own identity, and here that is doing real work: the
    # two uncategorized rows share a `category_id` of null and must stay two rows.
    assert len({r["key"] for r in donut["rows"]}) == len(donut["rows"])
    # And the graph's own figures add up, so no reader has to reconcile a residual
    # the server could have left in it.
    assert Decimal(graph["total_income"]) - Decimal(graph["total_expense"]) == Decimal(
        graph["net"]
    )


async def test_every_report_declares_which_question_its_owner_filter_answered(client):
    """The reports share an ``owner_id`` and mean different things by it.

    Net worth scopes *accounts*; cash-flow and spending scope *entries*. Nothing in
    the request says which, and the figures are not additive across the two
    readings, so each payload has to carry its own answer (ADR-0026). Cash-flow was
    a bare JSON array, which left it nowhere to say so.
    """
    await _signup(client)
    await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-01-01",
    })
    window = {"start": "2026-01-01", "end": "2026-01-31"}

    nw = (await client.get("/reports/net-worth", params=window)).json()
    cf = (await client.get("/reports/cash-flow", params=window)).json()
    spending = (await client.get("/reports/spending", params=window)).json()

    assert (nw["attribution"], cf["attribution"], spending["attribution"]) == (
        "account", "row", "row",
    )
    # The envelope is also what finally names the currency the points are in.
    assert cf["base_currency"] == nw["base_currency"]
    assert isinstance(cf["points"], list)


async def test_the_window_is_echoed_on_every_report(client):
    """A payload that says which window it answered for needs no second source.

    The chart labels its own axis from the response rather than from the controls,
    so the picture and the numbers cannot disagree about what is on screen — and a
    reader holding two of these does not have to remember what they asked for.
    """
    await _signup(client)
    await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-01-01",
    })
    window = {"start": "2026-03-01", "end": "2026-09-20"}

    for path in REPORTS:
        body = (await client.get(path, params=window)).json()
        assert (body["start"], body["end"]) == ("2026-03-01", "2026-09-20"), path

    # `granularity` is echoed *resolved* on the two reports that have buckets, and
    # is absent from the two that do not: `auto` is a request, not a period, and a
    # chart handed it back would be doing the resolution it delegated. The sankey
    # is one graph over the window, so a granularity on it would promise a
    # sequence of graphs rather than the one picture it draws.
    nw = (await client.get("/reports/net-worth", params=window)).json()
    cf = (await client.get("/reports/cash-flow", params=window)).json()
    assert nw["granularity"] == cf["granularity"] == "month"
    for path in ("/reports/spending", "/reports/cash-flow/sankey"):
        assert "granularity" not in (await client.get(path, params=window)).json(), path

    # And an explicit granularity is honoured rather than re-resolved.
    quarterly = (await client.get(
        "/reports/cash-flow", params={**window, "granularity": "quarter"})).json()
    assert quarterly["granularity"] == "quarter"
    assert len(quarterly["points"]) == 3
    # A granularity that is not in the vocabulary is a 422, not a silent `auto`.
    assert (await client.get(
        "/reports/cash-flow", params={**window, "granularity": "fortnight"})).status_code == 422


async def test_an_omitted_start_opens_where_the_data_does(client):
    """`start` is optional for exactly one preset, and it is the one a client
    cannot compute: only the server knows where the household's data begins.

    All three dated tables count. The balance here is the earliest thing the
    household has and there is no transaction anywhere near it, which is the case
    a "first transaction" rule would get wrong — a household whose first act was
    to type a balance has no transactions at all.
    """
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-02-14",
    })).json()
    await client.post("/transactions", json={
        "account_id": acct["id"], "amount": "-10", "transacted_at": "2026-05-01T12:00:00Z",
    })

    for path in REPORTS:
        body = (await client.get(path, params={"end": "2026-09-20"})).json()
        # Cash flow's bars start at the first money that moved (below); every
        # other report opens at the first dated row of any kind.
        expected = "2026-05-01" if path == "/reports/cash-flow" else "2026-02-14"
        assert body["start"] == expected, path
        assert body["end"] == "2026-09-20", path


async def test_cash_flow_is_cut_to_the_span_that_has_money_in_it(client):
    """Forty-five days of history in a twelve-month window are daily bars.

    Found on a real instance whose bank history began six weeks before the
    report: `auto` resolved over the requested 365 days to months, and the
    whole of the household's activity was two bars at the right edge of an empty
    year. The window now opens at the first transaction, `auto` resolves over
    what is left, and the echoed `start` says so.
    """
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2025-01-01",
    })).json()
    for day, amount in (("2026-08-10", "-40"), ("2026-08-20", "3000"), ("2026-09-20", "-12")):
        await client.post("/transactions", json={
            "account_id": acct["id"], "amount": amount, "transacted_at": f"{day}T12:00:00Z",
        })

    body = (await client.get(
        "/reports/cash-flow", params={"start": "2025-09-24", "end": "2026-09-23"})).json()
    assert body["start"] == "2026-08-10"
    assert body["granularity"] == "day"
    assert body["points"][0]["date"] == "2026-08-10"
    assert body["points"][0]["expense"] == "-40.0000"
    assert len(body["points"]) == 45

    # A window that starts after the first transaction is left as asked.
    inside = (await client.get(
        "/reports/cash-flow", params={"start": "2026-09-01", "end": "2026-09-23"})).json()
    assert inside["start"] == "2026-09-01"


async def test_an_inverted_window_is_refused_rather_than_drawn_empty(client):
    """`start` after `end` is a client bug, and an empty chart is the one answer
    that hides it: it renders as "this household has no money", not as "you asked
    backwards"."""
    await _signup(client)
    await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "100", "balance_date": "2026-01-01",
    })
    for path in REPORTS:
        resp = await client.get(path, params={"start": "2026-09-20", "end": "2026-01-01"})
        assert resp.status_code == 422, f"{path}: {resp.status_code}"
        assert resp.json()["detail"] == "start is after end"


async def test_owner_filters_are_accepted_everywhere_they_are_offered(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()
    scoped = {"start": "2026-01-01", "end": "2026-01-31", "owner_id": alex["id"]}
    for path in REPORTS:
        resp = await client.get(path, params=scoped)
        assert resp.status_code == 200, f"{path}: {resp.text}"
    for path in ("/accounts", "/accounts/net-worth", "/transactions"):
        resp = await client.get(path, params={"owner_id": alex["id"]})
        assert resp.status_code == 200, f"{path}: {resp.text}"
    # A malformed id is a 422, not a 500 from the uuid cast.
    assert (await client.get(
        "/transactions", params={"owner_id": "not-a-uuid"})).status_code == 422


async def test_owner_patch_distinguishes_absent_from_null(client):
    """Absent means "no change". Null is a client bug and says so — ``is not None``
    used to turn it into a 200 that changed nothing while looking like it worked,
    and neither field is nullable (``owners.sort`` is NOT NULL)."""
    await _signup(client)
    owner = (await client.post("/owners", json={"name": "Beth"})).json()

    async def stored() -> dict:
        return next(
            o for o in (await client.get("/owners")).json() if o["id"] == owner["id"]
        )

    # absent everywhere: no change at all
    resp = await client.patch(f"/owners/{owner['id']}", json={})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Beth"

    # present: applied
    resp = await client.patch(f"/owners/{owner['id']}", json={"name": "Bethany"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Bethany"

    # explicit null: refused rather than silently ignored
    for field in ("name", "sort"):
        resp = await client.patch(f"/owners/{owner['id']}", json={field: None})
        assert resp.status_code == 422, f"{field}=null should be a 422, got {resp.status_code}"

    # and the refusals left the owner exactly as it was
    assert (await stored())["name"] == "Bethany"
