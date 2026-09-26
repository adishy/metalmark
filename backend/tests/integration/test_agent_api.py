"""Agent access over HTTP, against a household full of PII (ADR-0048).

The fixture builds a household the way a person and a bank would: an owner whose
name is on their accounts, a transaction whose description carries an SSN and a
card number, a note with an e-mail address, a custom category named for a child,
and a real sync (through the fake provider) whose account names, institution and
error messages carry the same details. Then:

* **The canary test crawls everything** an agent can reach — starting from the
  catalog and following every link, filling ids from what it has seen, plus
  every debug view — and asserts that none of those details appears anywhere.
* **Parity**: the numbers an agent sees are the numbers the page sees.
* **The boundary**: tokens, scopes, cookies, read-only, tenant isolation, and the
  free-text parameters that are refused.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text, update

from app.deps import SESSION_COOKIE
from app.services.default_categories import DEFAULT_CATEGORIES

pytestmark = pytest.mark.integration

CSRF = "X-CSRF-Token"

# ---- the person -------------------------------------------------------------

EMAIL = "zelda.q@example.com"
OWNER = "Zelda Quixote"
CHILD = "Yorick Quixote"
SSN = "123-45-6789"
CARD = "4111 1111 1111 1111"
ACCOUNT_NO = "000123456789"
DOB = "1961-07-14"
HOUSEHOLD = "Quixote Family Home"
INSTITUTION = "Quixote Credit Union"

#: Substrings that must never appear in anything an agent receives.
CANARIES = [
    "zelda",
    "quixote",
    "yorick",
    SSN,
    "4111 1111",
    "4111111111111111",
    ACCOUNT_NO,
    EMAIL,
    DOB,
    "0123456789",
]


def _leaks(body) -> list[str]:
    dumped = (body if isinstance(body, str) else json.dumps(body)).lower()
    return [c for c in CANARIES if c.lower() in dumped]


# ---- clients ----------------------------------------------------------------


class Session:
    """A browser: cookie + CSRF."""

    def __init__(self, app):
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.csrf: str | None = None
        self.cookie: str | None = None

    async def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.csrf and method != "GET":
            headers[CSRF] = self.csrf
        # Carried by hand: the cookie may be `Secure`, which httpx will not send
        # to http://test on its own.
        if self.cookie:
            self.http.cookies.set(SESSION_COOKIE, self.cookie)
        resp = await self.http.request(method, url, headers=headers, **kw)
        if SESSION_COOKIE in resp.cookies:
            self.cookie = resp.cookies[SESSION_COOKIE]
        if (
            resp.headers.get("content-type", "").startswith("application/json")
            and resp.status_code < 400
        ):
            body = resp.json()
            if isinstance(body, dict) and body.get("csrf_token"):
                self.csrf = body["csrf_token"]
        return resp

    async def ok(self, method, url, status=None, **kw):
        resp = await self.request(method, url, **kw)
        assert resp.status_code in ((status,) if status else (200, 201)), (
            url,
            resp.status_code,
            resp.text,
        )
        return resp.json() if resp.content else None


class Agent:
    """An agent: a bearer token and nothing else."""

    def __init__(self, app, token: str | None):
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.token = token
        #: Every body this agent has received, for the canary sweep.
        self.seen: list[tuple[str, object]] = []

    async def get(self, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        if url.startswith("/api/"):
            url = url[len("/api") :]
        resp = await self.http.get(url, headers=headers, **kw)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        self.seen.append((url, body))
        return resp


def _pg_dsn() -> str:
    return (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )


@pytest.fixture(autouse=True)
def _clean_identity():
    import psycopg

    with psycopg.connect(_pg_dsn(), autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE users, households CASCADE")
    yield


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


# ---- the household ---------------------------------------------------------


async def _sync(household_id, org_name, provider_account_name, now):
    """A real sync through the fake provider, whose payload carries PII the way a
    bank's does, followed by one that fails with PII in the error."""
    from app.db import scoped_session
    from app.models import AccountConnection
    from app.security.crypto import SecretBox
    from app.services import sync
    from app.services.aggregator import (
        AccountSet,
        ProviderAccount,
        ProviderConnection,
        ProviderError,
        ProviderTransaction,
    )
    from app.services.fake_simplefin import FAKE_ACCESS_URL, FakeProvider
    from app.settings import get_settings

    async with scoped_session(household_id) as session:
        conn = AccountConnection(
            household_id=household_id,
            provider="fake",
            org_name=org_name,
            access_url_encrypted=SecretBox(get_settings().secret_key).encrypt(FAKE_ACCESS_URL),
        )
        session.add(conn)
        await session.flush()
        connection_id = conn.id

    posted = now - timedelta(days=2)
    account = ProviderAccount(
        external_id=f"ACT-{ACCOUNT_NO}",
        name=provider_account_name,
        currency="USD",
        balance=Decimal("1500.00"),
        balance_date=now.date(),
        org_name=org_name,
        transactions=(
            ProviderTransaction(
                external_id="T-1",
                amount=Decimal("-42.00"),
                transacted_at=posted,
                posted_at=posted,
                currency="USD",
                description=f"ACH DEBIT {OWNER.upper()} SSN {SSN}",
                payee=f"{OWNER} Payroll",
                memo=f"card {CARD}",
            ),
        ),
    )
    first = AccountSet(
        accounts=(account,),
        connections=(ProviderConnection(conn_id="c1", org_id="o1", org_name=org_name),),
        errlist=(f"gen.api: {OWNER} card 4111111111111111 needs attention",),
    )
    provider = FakeProvider(script=[first])
    await sync.run_connection_sync(household_id, connection_id, provider=provider, now=now)
    provider.raise_on_fetch = ProviderError(
        f"Login failed for {OWNER} acct {ACCOUNT_NO} ({EMAIL})", kind="auth", status=403
    )
    await sync.run_connection_sync(
        household_id, connection_id, provider=provider, now=now + timedelta(hours=1)
    )
    return connection_id


@pytest.fixture
async def world(app):
    """A household full of PII, and an owner's browser session on it."""
    s = Session(app)
    me = await s.ok(
        "POST",
        "/auth/signup",
        json={
            "email": EMAIL,
            "display_name": OWNER,
            "password": "password123",
            "household_name": HOUSEHOLD,
        },
    )
    today = date.today()
    now = datetime.now(UTC)
    ids: dict[str, object] = {"household_id": me["household_id"]}

    child = await s.ok("POST", "/owners", json={"name": CHILD})
    group = await s.ok(
        "POST", "/category-groups", json={"name": f"{OWNER}'s pots", "type": "expense"}
    )
    allowance = await s.ok(
        "POST", "/categories", json={"group_id": group["id"], "name": f"{CHILD}'s allowance"}
    )
    groceries = await s.ok(
        "POST", "/categories", json={"group_id": group["id"], "name": "Groceries"}
    )
    tag = await s.ok("POST", "/tags", json={"name": f"{OWNER} trip"})

    checking = await s.ok(
        "POST",
        "/accounts",
        json={
            "name": f"{OWNER} Checking {ACCOUNT_NO}",
            "type": "depository",
            "currency": "USD",
            "subtype": f"{OWNER}'s own",
            "institution": INSTITUTION,
            "current_balance": "2500.00",
            "balance_date": today.isoformat(),
            "owner_id": child["id"],
        },
    )
    card = await s.ok(
        "POST",
        "/accounts",
        json={
            "name": f"{CHILD} Visa ending 4821",
            "type": "credit",
            "currency": "USD",
            "institution": INSTITUTION,
            "current_balance": "-300.00",
            "balance_date": today.isoformat(),
        },
    )
    euro = await s.ok(
        "POST",
        "/accounts",
        json={
            "name": f"{OWNER} Euro Savings",
            "type": "depository",
            "currency": "EUR",
            "current_balance": "900.00",
            "balance_date": today.isoformat(),
        },
    )
    brokerage = await s.ok(
        "POST",
        "/accounts",
        json={
            "name": f"{OWNER} Brokerage",
            "type": "investment",
            "currency": "USD",
        },
    )

    when = (now - timedelta(days=5)).isoformat()
    bakery1 = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": checking["id"],
            "amount": "-12.50",
            "transacted_at": when,
            "description": f"ZELLE TO {OWNER.upper()} SSN {SSN} DOB {DOB}",
            "merchant": f"{OWNER}'s Bakery",
            "notes": f"call {EMAIL} about card {CARD}",
        },
    )
    bakery2 = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": checking["id"],
            "amount": "-7.25",
            "transacted_at": when,
            "description": "POS 99812 BAKERY",
            "merchant": f"{OWNER}'s Bakery",
        },
    )
    grocery = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": checking["id"],
            "amount": "-80.00",
            "transacted_at": when,
            "description": "SAFEWAY 1234",
            "merchant": "Safeway",
        },
    )
    out_leg = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": checking["id"],
            "amount": "-100.00",
            "transacted_at": when,
            "description": f"PAYMENT TO VISA {ACCOUNT_NO}",
        },
    )
    in_leg = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": card["id"],
            "amount": "100.00",
            "transacted_at": when,
            "description": f"PAYMENT FROM {OWNER.upper()}",
        },
    )
    transfer = await s.ok(
        "POST",
        "/transactions/transfers",
        json={"from_txn_id": out_leg["id"], "to_txn_id": in_leg["id"]},
    )
    euro_txn = await s.ok(
        "POST",
        "/transactions",
        json={
            "account_id": euro["id"],
            "amount": "-20.00",
            "transacted_at": when,
            "description": f"CAFE {CHILD.upper()}",
        },
    )
    # A person's category: provenance "user", which blocks the rule below.
    await s.ok("PATCH", f"/transactions/{grocery['id']}", json={"category_id": groceries["id"]})

    rule = await s.ok(
        "POST",
        "/rules",
        json={
            "name": f"{OWNER} bakery rule",
            "conditions": {"merchant_contains": f"{OWNER}'s Bakery", "amount_max": "-1"},
            "actions": {
                "set_category_id": allowance["id"],
                "rename_merchant": f"{CHILD} Bakery",
                "add_tag_ids": [tag["id"]],
            },
        },
    )
    await s.ok("POST", "/rules/apply")
    await s.ok(
        "POST",
        "/rules",
        json={
            "name": "Safeway",
            "conditions": {"merchant_contains": "Safeway"},
            "actions": {"set_category_id": allowance["id"]},
        },
    )

    security = await s.ok(
        "POST",
        "/investments/securities",
        json={
            "name": f"{OWNER} Family Trust",
            "ticker": "QXT",
            "security_type": "mutual_fund",
            "currency": "USD",
        },
    )
    await s.ok(
        "PUT",
        f"/investments/securities/{security['id']}/prices",
        json={"price_date": today.isoformat(), "price": "10.00"},
    )
    await s.ok(
        "POST",
        "/investments/holdings",
        json={
            "account_id": brokerage["id"],
            "security_id": security["id"],
            "quantity": "5",
        },
    )
    # A buy, so the position comes from its history (ADR-0034) and stays open.
    await s.ok(
        "POST",
        "/investments/transactions",
        json={
            "account_id": brokerage["id"],
            "security_id": security["id"],
            "type": "buy",
            "trade_date": today.isoformat(),
            "quantity": "5",
            "price": "10.00",
            "amount": "-50.00",
            "description": f"BUY {OWNER}",
            "notes": f"for {CHILD}, SSN {SSN}",
        },
    )

    connection_id = await _sync(
        uuid.UUID(me["household_id"]), INSTITUTION, f"{OWNER} Joint {ACCOUNT_NO}", now
    )

    paystub = await s.ok(
        "POST",
        f"/owners/{child['id']}/paystubs",
        json={
            "pay_date": today.isoformat(),
            "employer": f"{OWNER}'s Bakery",
            "gross": "100.00",
            "net": "100.00",
            "lines": [
                {"kind": "earning", "label": "Regular pay", "amount": "100.00"},
            ],
        },
    )

    ids.update(
        child=child["id"],
        paystub=paystub["id"],
        allowance=allowance["id"],
        groceries=groceries["id"],
        tag=tag["id"],
        checking=checking["id"],
        card=card["id"],
        euro=euro["id"],
        brokerage=brokerage["id"],
        bakery1=bakery1["id"],
        bakery2=bakery2["id"],
        grocery=grocery["id"],
        out_leg=out_leg["id"],
        in_leg=in_leg["id"],
        transfer=transfer["transfer_group_id"],
        euro_txn=euro_txn["id"],
        rule=rule["id"],
        security=security["id"],
        connection=str(connection_id),
        user=me["user"]["id"],
    )
    yield s, ids
    await s.http.aclose()


async def _issue(
    s: Session, scopes=("agent:read", "debug:read"), name="test agent", days=30
) -> dict:
    return await s.ok(
        "POST",
        "/admin/agent-tokens",
        status=201,
        json={"name": name, "scopes": list(scopes), "expires_in_days": days},
    )


@pytest.fixture
async def agent(app, world):
    s, ids = world
    issued = await _issue(s)
    a = Agent(app, issued["token"])
    yield a, s, ids, issued
    await a.http.aclose()


# ---- tokens ------------------------------------------------------------------


async def test_issuing_returns_the_token_once_and_lists_only_its_prefix(world):
    s, _ids = world
    issued = await _issue(s, name="Claude")
    assert issued["token"].startswith("mmk_")
    assert issued["prefix"] == issued["token"][:12]
    assert issued["status"] == "active"
    assert issued["scopes"] == ["agent:read", "debug:read"]
    assert issued["created_by"] == OWNER

    listed = await s.ok("GET", "/admin/agent-tokens")
    assert [t["id"] for t in listed] == [issued["id"]]
    assert "token" not in listed[0]
    assert issued["token"] not in json.dumps(listed)


async def test_the_server_keeps_only_a_hash(world):
    s, _ids = world
    issued = await _issue(s)
    from app.db import unscoped_session
    from app.models import AgentToken

    async with unscoped_session() as session:
        row = await session.get(AgentToken, uuid.UUID(issued["id"]))
    assert issued["token"] not in (row.token_hash, row.prefix)
    assert len(row.token_hash) == 64


async def test_a_token_has_to_expire_within_a_year(world):
    s, _ids = world
    for days in (0, 366):
        resp = await s.request(
            "POST",
            "/admin/agent-tokens",
            json={"name": "x", "scopes": ["agent:read"], "expires_in_days": days},
        )
        assert resp.status_code == 422
    resp = await s.request("POST", "/admin/agent-tokens", json={"name": "x", "scopes": []})
    assert resp.status_code == 422
    resp = await s.request("POST", "/admin/agent-tokens", json={"name": "x", "scopes": ["admin"]})
    assert resp.status_code == 422


async def test_a_member_cannot_issue_list_or_revoke(app, world):
    s, _ids = world
    issued = await _issue(s)
    member = Session(app)
    await member.ok(
        "POST",
        "/auth/signup",
        json={
            "email": "member@example.com",
            "display_name": "Member",
            "password": "password123",
        },
    )
    assert (await member.request("GET", "/admin/agent-tokens")).status_code == 403
    assert (
        await member.request(
            "POST", "/admin/agent-tokens", json={"name": "x", "scopes": ["agent:read"]}
        )
    ).status_code == 403
    assert (
        await member.request("DELETE", f"/admin/agent-tokens/{issued['id']}")
    ).status_code == 403
    await member.http.aclose()


async def test_an_admin_who_is_not_the_owner_may_issue(app, world):
    s, ids = world
    member = Session(app)
    joined = await member.ok(
        "POST",
        "/auth/signup",
        json={
            "email": "admin2@example.com",
            "display_name": "Second",
            "password": "password123",
        },
    )
    from app.db import unscoped_session
    from app.models import User

    async with unscoped_session() as session:
        await session.execute(
            update(User).where(User.id == uuid.UUID(joined["user"]["id"])).values(is_admin=True)
        )
    issued = await _issue(member)
    resp = await Agent(member.http._transport.app, issued["token"]).get("/agent/v1/accounts")
    assert resp.status_code == 200
    await member.http.aclose()


async def test_revoking_stops_the_token_on_its_next_request(app, agent):
    a, s, _ids, issued = agent
    assert (await a.get("/agent/v1/accounts")).status_code == 200
    revoked = await s.ok("DELETE", f"/admin/agent-tokens/{issued['id']}")
    assert revoked["status"] == "revoked"
    resp = await a.get("/agent/v1/accounts")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_token"
    # Revoking twice keeps the first revocation.
    again = await s.ok("DELETE", f"/admin/agent-tokens/{issued['id']}")
    assert again["revoked_at"] == revoked["revoked_at"]


async def test_another_households_token_cannot_be_revoked(app, agent, household_factory):
    _a, s, _ids, _issued = agent
    from app.db import unscoped_session
    from app.models import HouseholdMember, User

    other_hh = await household_factory(name="Other")
    async with unscoped_session() as session:
        other_user = (
            await session.execute(
                select(User)
                .join(HouseholdMember, HouseholdMember.user_id == User.id)
                .where(HouseholdMember.household_id == other_hh)
            )
        ).scalar_one()
        from app.agent import tokens

        _raw, token = await tokens.create(
            session, user=other_user, name="theirs", scopes=["agent:read"], expires_in_days=5
        )
    assert (await s.request("DELETE", f"/admin/agent-tokens/{token.id}")).status_code == 404
    assert str(token.id) not in json.dumps(await s.ok("GET", "/admin/agent-tokens"))


async def test_an_expired_token_is_refused(agent):
    a, _s, _ids, issued = agent
    from app.db import unscoped_session
    from app.models import AgentToken

    async with unscoped_session() as session:
        await session.execute(
            update(AgentToken)
            .where(AgentToken.id == uuid.UUID(issued["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await a.get("/agent/v1/accounts")).status_code == 401


async def test_a_demoted_issuer_switches_their_tokens_off(agent):
    a, _s, ids, _issued = agent
    from app.db import unscoped_session
    from app.models import HouseholdMember, User

    async with unscoped_session() as session:
        uid = uuid.UUID(ids["user"])
        await session.execute(update(User).where(User.id == uid).values(is_admin=False))
        await session.execute(
            update(HouseholdMember).where(HouseholdMember.user_id == uid).values(role="member")
        )
    assert (await a.get("/agent/v1/accounts")).status_code == 401


async def test_use_is_recorded(agent):
    a, s, _ids, issued = agent
    assert (await s.ok("GET", "/admin/agent-tokens"))[0]["last_used_at"] is None
    await a.get("/agent/v1/accounts")
    assert (await s.ok("GET", "/admin/agent-tokens"))[0]["last_used_at"] is not None


# ---- the boundary ------------------------------------------------------------


async def test_no_token_is_a_401_that_says_how_to_get_one(app, world):
    resp = await Agent(app, None).get("/agent")
    assert resp.status_code == 401
    assert "Bearer" in resp.headers["www-authenticate"]
    assert "Admin" in resp.json()["detail"]["hint"]


@pytest.mark.parametrize(
    "header", ["mmk_abc", "Basic mmk_abc", "Bearer ", "Bearer mmk_wrong", "Bearer x"]
)
async def test_a_malformed_or_unknown_token_is_a_401(app, world, header):
    resp = await Agent(app, None).get("/agent", headers={"Authorization": header})
    assert resp.status_code == 401


async def test_the_session_cookie_does_not_open_the_agent_routes(world):
    s, _ids = world
    for path in ("/agent", "/agent/v1/accounts", "/anon_debug", "/anon_debug/system"):
        assert (await s.request("GET", path)).status_code == 401, path


async def test_a_token_does_not_open_the_apps_own_routes(app, agent):
    a, _s, _ids, issued = agent
    plain = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    for path in ("/accounts", "/transactions", "/auth/me", "/admin/agent-tokens", "/export"):
        resp = await plain.get(path, headers={"Authorization": f"Bearer {issued['token']}"})
        assert resp.status_code == 401, path
    await plain.aclose()


async def test_a_scope_opens_only_its_own_surface(app, world):
    s, _ids = world
    only_agent = Agent(app, (await _issue(s, scopes=["agent:read"]))["token"])
    only_debug = Agent(app, (await _issue(s, scopes=["debug:read"]))["token"])
    assert (await only_agent.get("/agent/v1/accounts")).status_code == 200
    assert (await only_agent.get("/anon_debug/system")).status_code == 403
    assert (await only_debug.get("/anon_debug/system")).status_code == 200
    resp = await only_debug.get("/agent/v1/accounts")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "insufficient_scope"


async def test_writes_are_not_routes(agent):
    a, _s, _ids, _issued = agent
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        resp = await a.http.request(
            method, "/agent/v1/accounts", headers={"Authorization": f"Bearer {a.token}"}, json={}
        )
        assert resp.status_code == 405, method


async def test_the_agent_transaction_cannot_write(agent):
    _a, _s, ids, issued = agent
    from app.agent.dispatch import agent_session
    from app.agent.tokens import AgentPrincipal

    principal = AgentPrincipal(
        token_id=uuid.UUID(issued["id"]),
        user_id=uuid.UUID(ids["user"]),
        household_id=uuid.UUID(ids["household_id"]),
        role="owner",
        scopes=frozenset({"agent:read"}),
    )
    with pytest.raises(Exception, match="read-only"):
        async with agent_session(principal) as session:
            await session.execute(text("UPDATE accounts SET name = 'x'"))


async def test_the_grant_refuses_an_unsafe_method_even_in_process(agent):
    """The in-process grant is GET-only whatever the route; drive it directly."""
    _a, _s, ids, issued = agent
    from app.agent.tokens import AgentPrincipal
    from app.deps import AGENT_GRANT_KEY
    from app.main import create_app

    principal = AgentPrincipal(
        token_id=uuid.UUID(issued["id"]),
        user_id=uuid.UUID(ids["user"]),
        household_id=uuid.UUID(ids["household_id"]),
        role="owner",
        scopes=frozenset(),
    )
    app = create_app()
    sent: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "DELETE",
        "scheme": "http",
        "path": f"/accounts/{ids['euro']}",
        "raw_path": b"",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"x")],
        "client": ("t", 0),
        "server": ("x", 80),
        "state": {},
        AGENT_GRANT_KEY: principal,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    assert sent[0]["status"] == 405


async def test_free_text_search_is_refused(agent):
    a, _s, _ids, _issued = agent
    resp = await a.get("/agent/v1/transactions", params={"search": "Zelda"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "parameter_refused"
    view = await a.get("/anon_debug/view", params={"path": "/api/transactions?search=Zelda"})
    assert view.status_code == 400


async def test_unknown_and_malformed_parameters_are_refused(agent):
    a, _s, _ids, _issued = agent
    resp = await a.get("/agent/v1/accounts", params={"bogus": "1"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "unknown_parameter"
    resp = await a.get("/agent/v1/transactions", params={"review_status": "Zelda Quixote"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_parameter"


async def test_excluded_and_unknown_routes_say_so(agent):
    a, _s, _ids, _issued = agent
    for path in ("/agent/v1/auth/me", "/agent/v1/export", "/agent/v1/healthz"):
        resp = await a.get(path)
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_exposed", path
    resp = await a.get("/agent/v1/nothing/here")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "no_such_route"
    resp = await a.get("/agent/v1/admin/agent-tokens")
    assert resp.status_code == 404


async def test_another_households_ids_are_404(agent, household_factory):
    a, _s, _ids, _issued = agent
    from app.db import scoped_session
    from app.models import Account

    other = await household_factory(name="Other")
    async with scoped_session(other) as session:
        from app.services.owners import ensure_shared_owner

        shared = await ensure_shared_owner(session, other)
        acct = Account(
            household_id=other,
            name="Theirs",
            type="depository",
            currency="USD",
            is_asset=True,
            owner_id=shared.id,
        )
        session.add(acct)
        await session.flush()
        foreign = acct.id
    resp = await a.get(f"/agent/v1/accounts/{foreign}")
    assert resp.status_code == 404
    assert "Theirs" not in resp.text
    resp = await a.get(f"/anon_debug/accounts/{foreign}/balance")
    assert resp.status_code == 404
    listed = (await a.get("/agent/v1/accounts")).json()
    assert str(foreign) not in json.dumps(listed)


async def test_an_error_does_not_echo_the_apps_message(agent):
    a, _s, _ids, _issued = agent
    resp = await a.get(f"/agent/v1/transactions/{uuid.uuid4()}")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "not_found" and "hint" in body
    resp = await a.get("/agent/v1/accounts/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["error"]["errors"][0]["loc"][-1] == "account_id"


# ---- discovery ---------------------------------------------------------------


def _fill(path: str, ids: dict) -> str:
    return (
        path.replace("{account_id}", ids["checking"])
        .replace("{txn_id}", ids["bakery1"])
        .replace("{group_id}", ids["transfer"])
        .replace("{security_id}", ids["security"])
        .replace("{run_id}", ids["run"])
        .replace("{owner_id}", ids["child"])
        .replace("{paystub_id}", ids["paystub"])
    )


async def _run_id(a: Agent) -> str:
    runs = (await a.get("/agent/v1/connections/runs")).json()
    return runs[0]["id"]


async def test_the_index_lists_every_readable_route(agent):
    a, _s, _ids, _issued = agent
    from app.agent import dispatch

    index = (await a.get("/agent")).json()
    assert index == (await a.get("/agent/v1")).json()
    listed = {r["path"].removeprefix("/api/agent/v1") for r in index["routes"]}
    assert listed == {e.path for e in dispatch.exposed()}
    assert index["links"]["debug"] == "/api/anon_debug"
    assert index["auth"] and index["anonymization"]
    # The free-text parameter is not advertised.
    txns = next(r for r in index["routes"] if r["path"].endswith("/transactions"))
    assert "search" not in {p["name"] for p in txns["params"]}


async def test_every_catalog_route_answers(agent):
    """Crawl: follow each href, and fill each template from ids the agent has seen."""
    a, _s, ids, _issued = agent
    ids = {**ids, "run": await _run_id(a)}
    for catalog in ("/agent", "/anon_debug"):
        index = (await a.get(catalog)).json()
        for route in index["routes"]:
            url = route["href"]
            if url is None:
                url = _fill(route["path"], ids)
                required = [
                    p for p in route["params"] if p["required"] and p["location"] == "query"
                ]
                values = {
                    "end": date.today().isoformat(),
                    "txn_id": ids["bakery1"],
                    "path": "/api/accounts",
                }
                if required:
                    url += "?" + "&".join(f"{p['name']}={values[p['name']]}" for p in required)
            resp = await a.get(url)
            assert resp.status_code == 200, (url, resp.text[:500])


async def test_the_link_header_points_back_at_the_index(agent):
    a, _s, _ids, _issued = agent
    resp = await a.get("/agent/v1/accounts")
    assert '</api/agent/v1>; rel="index"' in resp.headers["link"]


# ---- the canary -------------------------------------------------------------


async def test_nothing_an_agent_can_reach_carries_the_households_pii(agent):
    a, _s, ids, _issued = agent
    ids = {**ids, "run": await _run_id(a)}

    # Everything in both catalogs.
    for catalog in ("/agent", "/anon_debug"):
        for route in (await a.get(catalog)).json()["routes"]:
            url = route["href"] or _fill(route["path"], ids)
            if "/reports/" in url and "end=" not in url:
                url += f"?end={date.today().isoformat()}"
            if url.endswith("/view"):
                continue
            if url.endswith("/transfer-candidates"):
                url += f"?txn_id={ids['out_leg']}"
            await a.get(url)
    # Every transaction and account explained, every page, and the report windows
    # the pages use, and every one of those through /view too.
    for key in ("bakery1", "bakery2", "grocery", "out_leg", "in_leg", "euro_txn"):
        await a.get(f"/anon_debug/transactions/{ids[key]}/explain")
    accounts = (await a.get("/agent/v1/accounts")).json()
    for acct in accounts:
        await a.get(f"/anon_debug/accounts/{acct['id']}/balance")
        await a.get(
            "/agent/v1/transactions", params={"account_id": acct["id"], "include_hidden": "true"}
        )
    start = (date.today() - timedelta(days=30)).isoformat()
    for page in (
        "accounts",
        "transactions",
        "review",
        "reports",
        "investments",
        "settings",
        "admin",
    ):
        await a.get(f"/anon_debug/pages/{page}", params={"start": start})
        await a.get(f"/anon_debug/pages/{page}", params={"start": start, "owner_id": ids["child"]})
    for path in (
        "/api/reports/net-worth",
        "/api/reports/cash-flow",
        "/api/reports/spending",
        "/api/reports/cash-flow/sankey",
    ):
        await a.get("/anon_debug/view", params={"path": f"{path}?start={start}&end={date.today()}"})
    for group_by in ("security", "type", "account", "currency"):
        await a.get("/agent/v1/investments/allocation", params={"group_by": group_by})
    await a.get("/agent/v1/connections/notifications")

    assert len(a.seen) > 80
    failures = {url: _leaks(body) for url, body in a.seen if _leaks(body)}
    assert failures == {}, json.dumps(failures, indent=1)
    # And the crawl actually reached the data: nothing above is vacuous.
    assert all(isinstance(b, (dict, list)) for _u, b in a.seen)


async def test_the_pii_is_really_in_the_database_the_agent_read(world):
    """The canary test is only as good as its seed: the cookie API shows it all."""
    s, ids = world
    everything = json.dumps(
        [
            await s.ok("GET", "/accounts"),
            await s.ok("GET", "/transactions?include_hidden=true"),
            await s.ok("GET", "/owners"),
            await s.ok("GET", "/categories"),
            await s.ok("GET", "/rules"),
            await s.ok("GET", "/connections"),
            await s.ok("GET", "/connections/runs"),
            await s.ok("GET", "/investments/securities"),
            await s.ok("GET", "/household"),
        ]
    ).lower()
    for canary in ("zelda", "quixote", "yorick", SSN, ACCOUNT_NO, EMAIL, DOB, "4111 1111"):
        assert canary.lower() in everything, canary


# ---- parity and stability -----------------------------------------------------


async def test_the_numbers_are_the_numbers_the_page_sees(agent):
    a, s, _ids, _issued = agent
    page = await s.ok("GET", "/accounts")
    seen = (await a.get("/agent/v1/accounts")).json()
    fields = ("id", "type", "currency", "current_balance", "balance_date", "is_asset", "owner_id")

    def strip(rows):
        return [{k: r[k] for k in fields} for r in rows]

    assert strip(seen) == strip(page)
    assert all(r["name"].startswith("Account ") for r in seen)

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=60)).isoformat()
    for path in ("/reports/net-worth", "/reports/cash-flow", "/reports/spending"):
        mine = await s.ok("GET", f"{path}?start={start}&end={end}")
        theirs = (await a.get(f"/agent/v1{path}", params={"start": start, "end": end})).json()
        for key in ("delta_net_worth", "net_cash_flow", "total", "points"):
            if key == "points":
                assert [p.get("net_worth", p.get("net")) for p in theirs.get("points", [])] == [
                    p.get("net_worth", p.get("net")) for p in mine.get("points", [])
                ]
            elif key in mine:
                assert theirs[key] == mine[key], (path, key)


async def test_view_and_agent_answer_the_same(agent):
    a, _s, _ids, _issued = agent
    direct = (await a.get("/agent/v1/transactions")).json()
    viewed = (await a.get("/anon_debug/view", params={"path": "/api/transactions"})).json()
    assert viewed["status"] == 200
    assert viewed["body"] == direct


async def test_equal_values_share_a_pseudonym_and_tokens_do_not(app, agent):
    a, s, ids, _issued = agent
    rows = {t["id"]: t for t in (await a.get("/agent/v1/transactions")).json()["items"]}
    b1, b2 = rows[ids["bakery1"]], rows[ids["bakery2"]]
    assert b1["merchant"] == b2["merchant"] and b1["merchant"].startswith(
        ("Merchant ", "Account ", "Owner ")
    )
    assert b1["description"] != b2["description"]

    # The account's pseudonym is the same in the account list and in a sentence.
    accounts = {x["id"]: x for x in (await a.get("/agent/v1/accounts")).json()}
    checks = (await a.get("/agent/v1/checks")).json()
    names_in_checks = {i["account_id"]: i["name"] for c in checks["checks"] for i in c["items"]}
    for account_id, name in names_in_checks.items():
        assert accounts[account_id]["name"] == name

    other = Agent(app, (await _issue(s))["token"])
    other_accounts = {x["id"]: x for x in (await other.get("/agent/v1/accounts")).json()}
    assert other_accounts[ids["checking"]]["name"] != accounts[ids["checking"]]["name"]
    assert (
        other_accounts[ids["checking"]]["current_balance"]
        == accounts[ids["checking"]]["current_balance"]
    )


async def test_generic_labels_and_the_shared_owner_survive(agent):
    a, _s, _ids, _issued = agent
    names = {c["name"] for c in (await a.get("/agent/v1/categories")).json()}
    assert "Groceries" in names
    # The starter set is generic by construction (it ships in the repository);
    # everything the household named itself is a pseudonym.
    starter = {n for _g, _t, cats in DEFAULT_CATEGORIES for n, _i in cats}
    assert all(n in starter or n.startswith("Category ") for n in names)
    assert not any(OWNER in n or CHILD in n for n in names)
    owners = {o["name"] for o in (await a.get("/agent/v1/owners")).json()}
    assert "Shared" in owners


async def test_email_and_csrf_never_appear(agent):
    a, _s, _ids, _issued = agent
    members = (await a.get("/agent/v1/household/members")).json()
    assert all(m["email"] is None for m in members)
    assert all(m["display_name"].startswith("Person ") for m in members)


# ---- debug views -------------------------------------------------------------


async def test_explain_runs_the_rule_engine_condition_by_condition(agent):
    a, _s, ids, _issued = agent
    bakery = (await a.get(f"/anon_debug/transactions/{ids['bakery1']}/explain")).json()
    trace = {t["rule_id"]: t for t in bakery["rules"]}
    mine = trace[ids["rule"]]
    # Both conditions hold, evaluated by the engine's own evaluator. The rule wrote
    # the category — but not the merchant: the person typed that one in, and
    # provenance (ADR-0007) keeps a rule off it. Explain says exactly that.
    assert mine["conditions"] == {"merchant_contains": True, "amount_max": True}
    assert mine["matched"] is True
    assert ids["rule"] in bakery["matched_rule_ids"]
    assert set(mine["writes"]) == {"category", "merchant", "tags"}
    assert mine["blocked_by_user"] == ["merchant"]
    assert bakery["provenance"]["category"] == "rule"
    assert bakery["provenance"]["merchant"] == "user"
    assert bakery["category"]["id"] == ids["allowance"]
    assert bakery["rules"][0]["rule_conditions"]["merchant_contains"].startswith(
        ("Pattern ", "Merchant ", "Owner ")
    )

    grocery = (await a.get(f"/anon_debug/transactions/{ids['grocery']}/explain")).json()
    safeway = next(t for t in grocery["rules"] if t["rule_id"] != ids["rule"])
    assert safeway["matched"] is True
    assert safeway["blocked_by_user"] == ["category"]
    assert any("set by a person" in n for n in grocery["notes"])
    assert grocery["matched_rule_ids"] == [safeway["rule_id"]]


async def test_explain_shows_the_transfer_and_the_owner_chain(agent):
    a, _s, ids, _issued = agent
    out = (await a.get(f"/anon_debug/transactions/{ids['out_leg']}/explain")).json()
    assert out["transfer"]["transfer_group_id"] == ids["transfer"]
    assert {leg["id"] for leg in out["transfer"]["legs"]} == {ids["out_leg"], ids["in_leg"]}
    assert out["owner"]["decided_by"] == "account"
    assert out["owner"]["effective_owner_id"] == ids["child"]
    assert any("transfer" in n for n in out["notes"])


async def test_balance_explain_counts_snapshots_and_drift(agent):
    a, _s, ids, _issued = agent
    body = (await a.get(f"/anon_debug/accounts/{ids['checking']}/balance")).json()
    assert body["account"]["id"] == ids["checking"]
    assert body["snapshot_count"] >= 1
    assert body["transaction_count"] == 4
    assert body["snapshots"][0]["currency"] == "USD"
    brokerage = (await a.get(f"/anon_debug/accounts/{ids['brokerage']}/balance")).json()
    assert len(brokerage["holdings"]) == 1
    assert brokerage["holdings"][0]["security"]["ticker"] == "QXT"


async def test_system_reports_counts_settings_and_health(agent):
    a, _s, ids, _issued = agent
    body = (await a.get("/anon_debug/system")).json()
    assert body["counts"]["accounts"] == 5
    assert body["household_id"] == ids["household_id"]
    assert body["settings"]["env"] == "test"
    assert set(body["settings"]) >= {"open_signup", "fx_fetch", "cookie_secure"}
    dumped = json.dumps(body).lower()
    for secret in ("password", "secret", "dsn", "postgres"):
        assert secret not in dumped, secret
    eur = next(c for c in body["currencies"] if c["currency"] == "EUR")
    assert eur["latest_rate_date"] is None
    conn = body["connections"][0]
    assert conn["status"] == "auth_error"
    # The bank's message survives as a diagnostic; the owner's name and e-mail are
    # household names, so they become the same pseudonyms as everywhere else, and
    # the account number is masked.
    assert conn["last_error"].startswith("Login failed for Person ")
    assert "[number]" in conn["last_error"]
    assert _leaks(conn["last_error"]) == []


async def test_a_page_is_every_request_the_page_makes(agent):
    a, _s, _ids, _issued = agent
    listing = (await a.get("/anon_debug/pages")).json()
    assert {p["page"] for p in listing} >= {"accounts", "transactions", "reports", "admin"}
    page = (await a.get("/anon_debug/pages/reports")).json()
    assert [c["path"].split("?")[0] for c in page["calls"]] == [
        "/api/reports/net-worth",
        "/api/reports/cash-flow",
        "/api/reports/cash-flow/sankey",
        "/api/reports/spending",
    ]
    assert all(c["status"] == 200 for c in page["calls"])
    assert (await a.get("/anon_debug/pages/nope")).status_code == 404


async def test_the_sync_log_is_anonymized_but_still_readable(agent):
    a, _s, _ids, _issued = agent
    runs = (await a.get("/agent/v1/connections/runs")).json()
    failed = next(r for r in runs if r["status"] == "error")
    assert failed["http_status"] == 403
    detail = (await a.get(f"/agent/v1/connections/runs/{runs[-1]['id']}")).json()
    events = [e["event"] for e in detail["events"]]
    assert "account.created" in events
    created = next(e for e in detail["events"] if e["event"] == "account.created")
    assert created["detail"]["name"].startswith(("Account ", "Value "))
