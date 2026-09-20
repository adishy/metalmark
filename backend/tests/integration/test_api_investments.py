"""Investments over HTTP.

Three things only exist in the request path and are what this covers: the CSRF and
owner-role gates on the writes, the serialization of a position's *resolved*
quantity (``quantity_source`` is computed, never stored), and the 409 a manual
write gets when history owns the column.
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
    """Start every test with no users and no households, for the same reason
    ``test_api_owners`` does: signup is a one-shot property of the database."""
    import psycopg

    dsn = (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE users, households CASCADE")
    yield


@pytest.fixture
async def client():
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


async def _brokerage(client, *, currency="USD"):
    resp = await client.post(
        "/accounts",
        json={"name": "Brokerage", "type": "investment", "currency": currency},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _security(client, *, ticker="VTI", name="Vanguard Total"):
    resp = await client.post(
        "/investments/securities",
        json={"name": name, "ticker": ticker, "security_type": "etf", "currency": "USD"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _account_balance(client, account_id):
    resp = await client.get("/accounts")
    assert resp.status_code == 200, resp.text
    return next(a for a in resp.json() if a["id"] == account_id)["current_balance"]


# ---- the writes land -------------------------------------------------------


async def test_a_price_and_a_position_move_the_account_balance(client):
    """End to end over HTTP: the stored balance follows Σ(quantity × price),
    because ADR-0011 makes it a function of the holdings."""
    await _signup(client)
    account = await _brokerage(client)
    security = await _security(client)

    resp = await client.post(
        "/investments/holdings",
        json={"account_id": account["id"], "security_id": security["id"],
              "quantity": "10"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["quantity_source"] == "manual"

    resp = await client.put(
        f"/investments/securities/{security['id']}/prices",
        json={"price_date": "2026-09-20", "price": "100"},
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(await _account_balance(client, account["id"])) == Decimal("1000.0000")


async def test_a_recorded_buy_is_a_position_over_http(client):
    """No holding row is created first: the trades are the position (ADR-0034)."""
    await _signup(client)
    account = await _brokerage(client)
    security = await _security(client)
    await client.put(
        f"/investments/securities/{security['id']}/prices",
        json={"price_date": "2026-09-20", "price": "50"},
    )

    resp = await client.post(
        "/investments/transactions",
        json={"account_id": account["id"], "security_id": security["id"], "type": "buy",
              "trade_date": "2026-09-20", "quantity": "3", "amount": "-150"},
    )
    assert resp.status_code == 201, resp.text

    holdings = (await client.get("/investments/holdings")).json()
    assert len(holdings) == 1
    one = holdings[0]
    assert one["id"] is None                  # no row behind it
    assert one["quantity"] == "3.00000000"
    assert one["quantity_source"] == "history"
    assert one["manual_quantity"] is None
    assert Decimal(await _account_balance(client, account["id"])) == Decimal("150.0000")


async def test_portfolio_reports_an_unpriced_position_rather_than_zeroing_it(client):
    """ADR-0032 §5. "We cannot value this" and "this is worth nothing" must not
    render identically — a total quietly missing 30% of the portfolio is the
    failure."""
    await _signup(client)
    account = await _brokerage(client)
    priced = await _security(client, ticker="VTI")
    unpriced = await _security(client, ticker="BND", name="Vanguard Bond")
    await client.put(
        f"/investments/securities/{priced['id']}/prices",
        json={"price_date": "2026-09-20", "price": "100"},
    )
    for sec, qty in ((priced, "10"), (unpriced, "5")):
        assert (
            await client.post(
                "/investments/holdings",
                json={"account_id": account["id"], "security_id": sec["id"], "quantity": qty},
            )
        ).status_code == 201

    body = (await client.get("/investments/portfolio", params={"on": "2026-09-20"})).json()
    assert Decimal(body["total_base"]) == Decimal("1000.0000")
    valuation = body["accounts"][0]
    assert valuation["unpriced"] == 1
    assert valuation["no_rate"] == 0
    assert valuation["is_fully_valued"] is False
    reasons = {h["security_id"]: h["reason"] for h in valuation["holdings"]}
    assert reasons[unpriced["id"]] == "no_price"


async def test_allocation_groups_one_instrument_across_accounts(client):
    """The reason a security is a household-level row at all (ADR-0011): the view
    has to answer "how much VTI do we own", not "how much does each account hold"."""
    await _signup(client)
    first = await _brokerage(client)
    second = (
        await client.post(
            "/accounts", json={"name": "IRA", "type": "investment", "currency": "USD"}
        )
    ).json()
    security = await _security(client)
    await client.put(
        f"/investments/securities/{security['id']}/prices",
        json={"price_date": "2026-09-20", "price": "10"},
    )
    for account, qty in ((first, "10"), (second, "30")):
        assert (
            await client.post(
                "/investments/holdings",
                json={"account_id": account["id"], "security_id": security["id"],
                      "quantity": qty},
            )
        ).status_code == 201

    body = (await client.get("/investments/allocation", params={"on": "2026-09-20"})).json()
    assert body["group_by"] == "security"
    assert len(body["rows"]) == 1
    assert Decimal(body["rows"][0]["value_base"]) == Decimal("400.0000")
    assert Decimal(body["rows"][0]["percent"]) == Decimal("100.0000")
    assert body["rows"][0]["holdings"] == 2

    # ... and by account, the same total split in two.
    body = (
        await client.get(
            "/investments/allocation", params={"on": "2026-09-20", "group_by": "account"}
        )
    ).json()
    assert {Decimal(r["value_base"]) for r in body["rows"]} == {
        Decimal("100.0000"),
        Decimal("300.0000"),
    }


# ---- the refusals ----------------------------------------------------------


async def test_a_manual_write_to_a_history_owned_position_is_a_409(client):
    await _signup(client)
    account = await _brokerage(client)
    security = await _security(client)
    holding = (
        await client.post(
            "/investments/holdings",
            json={"account_id": account["id"], "security_id": security["id"],
                  "quantity": "10"},
        )
    ).json()
    assert (
        await client.post(
            "/investments/transactions",
            json={"account_id": account["id"], "security_id": security["id"],
                  "type": "buy", "trade_date": "2026-09-20", "quantity": "4",
                  "amount": "-400"},
        )
    ).status_code == 201

    resp = await client.patch(f"/investments/holdings/{holding['id']}", json={"quantity": "99"})
    assert resp.status_code == 409, resp.text
    assert "recorded trades" in resp.json()["detail"]

    # The read still reports the fold, and shows what was overridden.
    one = (await client.get("/investments/holdings")).json()[0]
    assert one["quantity_source"] == "history"
    assert one["quantity"] == "4.00000000"
    assert one["manual_quantity"] == "10.00000000"


async def test_a_zero_quantity_is_a_422_not_a_constraint_violation(client):
    await _signup(client)
    account = await _brokerage(client)
    security = await _security(client)
    resp = await client.post(
        "/investments/holdings",
        json={"account_id": account["id"], "security_id": security["id"], "quantity": "0"},
    )
    assert resp.status_code == 422, resp.text


async def test_a_duplicate_ticker_is_a_409(client):
    await _signup(client)
    await _security(client, ticker="VTI")
    resp = await client.post(
        "/investments/securities",
        json={"name": "Again", "ticker": "vti", "security_type": "etf", "currency": "USD"},
    )
    assert resp.status_code == 409, resp.text


async def test_a_member_can_read_but_not_write(client):
    """A security is household-wide, so defining one changes every other member's
    allocation view — that is an owner decision, like the owner CRUD it mirrors."""
    from app.main import create_app

    await _signup(client)
    async with Client(create_app()) as member:
        joined = await member.post(
            "/auth/signup",
            json={"email": f"{uuid.uuid4().hex[:8]}@example.com", "display_name": "Beth",
                  "password": "password123", "household_name": "Elsewhere"},
        )
        assert joined.status_code == 201, joined.text
        assert joined.json()["role"] == "member"

        # Reads are open to any member: the pickers and filters need the list.
        assert (await member.get("/investments/securities")).status_code == 200
        assert (await member.get("/investments/holdings")).status_code == 200
        assert (await member.get("/investments/portfolio")).status_code == 200

        resp = await member.post(
            "/investments/securities",
            json={"name": "Sneaky", "ticker": "SNK", "security_type": "stock",
                  "currency": "USD"},
        )
        assert resp.status_code == 403, resp.text


async def test_the_writes_require_a_csrf_token(client):
    """The gate is on the class of method, not on the route, so a new router is
    protected by existing rather than by remembering."""
    await _signup(client)
    csrf = client._csrf
    client._csrf = None
    try:
        resp = await client.post(
            "/investments/securities",
            json={"name": "X", "ticker": "X", "security_type": "stock", "currency": "USD"},
        )
        assert resp.status_code == 403, resp.text
        assert "CSRF" in resp.json()["detail"]
    finally:
        client._csrf = csrf
