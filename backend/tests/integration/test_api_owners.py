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


async def test_delete_owner_requires_the_owner_role(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()

    from app.main import create_app

    async with Client(create_app()) as member:
        await _signup(member, name="Beth")
        # Members can list and create owners, but not re-attribute history.
        assert (await member.get("/owners")).status_code == 200
        assert (await member.post("/owners", json={"name": "Carol"})).status_code == 201
        denied = await member.delete(f"/owners/{alex['id']}")
        assert denied.status_code == 403


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


async def test_owner_filters_are_accepted_everywhere_they_are_offered(client):
    await _signup(client)
    alex = (await client.post("/owners", json={"name": "Alex"})).json()
    scoped = {"start": "2026-01-01", "end": "2026-01-31", "owner_id": alex["id"]}
    for path in ("/reports/net-worth", "/reports/cash-flow", "/reports/spending"):
        resp = await client.get(path, params=scoped)
        assert resp.status_code == 200, f"{path}: {resp.text}"
    for path in ("/accounts", "/accounts/net-worth", "/transactions"):
        resp = await client.get(path, params={"owner_id": alex["id"]})
        assert resp.status_code == 200, f"{path}: {resp.text}"
    # A malformed id is a 422, not a 500 from the uuid cast.
    assert (await client.get(
        "/transactions", params={"owner_id": "not-a-uuid"})).status_code == 422
