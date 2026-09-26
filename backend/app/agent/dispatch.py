"""Resolving an agent token, and answering from the app's own routes (ADR-0048).

**One code path.** ``/agent/v1/<path>`` and ``/anon_debug/view?path=<path>`` do not
reimplement anything: they run the app's own ``GET <path>`` in-process, as the
token's issuer, in a read-only transaction (``deps.get_context`` and
``AGENT_GRANT_KEY``), then anonymize the response through the policy registry. An
agent therefore sees exactly what the page sees — the same handler, the same
query, the same numbers — minus the names.

**Which routes.** Every ``GET`` route of ``main.APP_ROUTERS`` is exposed except the
ones in ``EXCLUDED``, each with its reason; a test holds the two lists to the app
so a new route is a decision rather than an accident.

**Which parameters.** A query parameter that takes free text is an *oracle*: a
``search=Jane`` that returns one row instead of none says the name is there,
whatever the response then hides. So a string parameter reaches the route only if
it is named in ``STRING_PARAMS`` with the pattern its values must match; the rest
are refused (``REFUSED_PARAMS`` says why, for the ones that exist today).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import compile_path

from app.agent.anonymize import Anonymizer, KnownNames, pseudonym_key
from app.agent.tokens import AgentPrincipal
from app.agent.tokens import resolve as resolve_token
from app.db import get_sessionmaker, scoped_session, set_scope, unscoped_session
from app.deps import AGENT_GRANT_KEY
from app.logging import get_logger
from app.models import (
    Account,
    AccountConnection,
    Category,
    CategoryGroup,
    Household,
    HouseholdMember,
    Owner,
    Rule,
    Security,
    SyncRun,
    Tag,
    User,
)
from app.settings import get_settings

log = get_logger("agent")

#: Where the proxy mounts the API (nginx strips it before the app sees a path),
#: so links written for an agent carry it and paths an agent pastes may.
PUBLIC_PREFIX = "/api"

#: App ``GET`` routes an agent cannot reach, and why.
EXCLUDED: dict[str, str] = {
    "/healthz": "Liveness for the container, not household data; see /anon_debug/system.",
    "/auth/me": "The browser session's own identity, and its CSRF token.",
    "/export": "The whole household as a raw document — not a schema the registry can walk.",
    "/export/transactions.csv": "Raw CSV, not a schema the registry can walk.",
    "/institutions/{key}/logo": "An image, not data; and a bank's logo names the bank.",
}

#: String query parameters an agent may send, with the pattern a value must match.
STRING_PARAMS: dict[str, re.Pattern[str]] = {
    "review_status": re.compile(r"[a-z_]{1,32}"),
    "cursor": re.compile(r"[A-Za-z0-9_\-=]{1,512}"),
    "group_by": re.compile(r"[a-z_]{1,32}"),
    "granularity": re.compile(r"[a-z_]{1,32}"),
    # A closed vocabulary, not text a caller composes: the route's own query
    # declaration allows exactly these two words.
    "direction": re.compile(r"in|out"),
}

#: String query parameters that exist on an exposed route but are refused.
REFUSED_PARAMS: dict[str, str] = {
    "search": (
        "Free-text search is an oracle: whether a name matches is itself the name. "
        "Filter by account, category, owner, date or review status instead."
    ),
    # Same oracle, reached through the ledger's own search box: a series matches
    # on its merchant, and a merchant is a name.
    "q": (
        "Free-text search is an oracle: whether a name matches is itself the name. "
        "Filter by account, category, direction or active state instead."
    ),
}

#: Where the id a path parameter takes can be found, for the catalog.
ID_SOURCES: dict[str, str] = {
    "account_id": "accounts",
    "txn_id": "transactions",
    "group_id": "transactions (a row's transfer_group_id)",
    "run_id": "connections/runs",
    "security_id": "investments/securities",
    "owner_id": "owners",
    "category_id": "categories",
    "connection_id": "connections",
    "since": "connections/notifications (an id from a previous page)",
    "series_id": "recurring",
}


@dataclass(frozen=True)
class Exposed:
    """One app route an agent can read."""

    path: str
    route: APIRoute
    regex: re.Pattern[str] = field(compare=False)

    @property
    def response_model(self) -> Any:
        return self.route.response_model


_EXPOSED: list[Exposed] | None = None


def _app_get_routes() -> list[APIRoute]:
    from app.main import APP_ROUTERS

    return [
        r
        for router in APP_ROUTERS
        for r in router.routes
        if isinstance(r, APIRoute) and "GET" in r.methods
    ]


def exposed() -> list[Exposed]:
    """Every exposed route, literal paths before templated ones so that
    ``/transactions/transfer-candidates`` is not read as a transaction id."""
    global _EXPOSED
    if _EXPOSED is None:
        found = []
        for r in _app_get_routes():
            if r.path in EXCLUDED:
                continue
            regex, _fmt, _conv = compile_path(r.path)
            found.append(Exposed(path=r.path, route=r, regex=regex))
        found.sort(key=lambda e: ("{" in e.path, e.path))
        _EXPOSED = found
    return _EXPOSED


def match(path: str) -> Exposed | None:
    for e in exposed():
        if e.regex.match(path):
            return e
    return None


def strip_prefix(path: str) -> str:
    if path == PUBLIC_PREFIX or path.startswith(PUBLIC_PREFIX + "/"):
        path = path[len(PUBLIC_PREFIX) :] or "/"
    return path


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

NO_TOKEN_HINT = (
    "Send an agent token as `Authorization: Bearer mmk_…`. An administrator or the "
    "household's owner issues one in the app under Admin → Agent access."
)


async def principal_for(request: Request, scope: str) -> AgentPrincipal:
    """The principal behind the request's bearer token, holding ``scope``.

    Reads ``Authorization`` only — never the session cookie, so a browser that
    happens to be logged in cannot drive these routes, and a token cannot drive
    the app's own.
    """
    header = request.headers.get("authorization", "")
    kind, _, raw = header.partition(" ")
    if kind.lower() != "bearer" or not raw.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "code": "unauthenticated",
                "message": "Agent token required",
                "hint": NO_TOKEN_HINT,
            },
            headers={"WWW-Authenticate": 'Bearer realm="metalmark-agent"'},
        )
    async with unscoped_session() as session:
        principal = await resolve_token(session, raw.strip())
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_token",
                "message": (
                    "Token is unknown, revoked or expired, "
                    "or its issuer can no longer issue tokens"
                ),
                "hint": NO_TOKEN_HINT,
            },
            headers={"WWW-Authenticate": 'Bearer realm="metalmark-agent", error="invalid_token"'},
        )
    if scope not in principal.scopes:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "insufficient_scope",
                "message": f"This token does not carry the {scope!r} scope",
                "hint": f"Issue a token with {scope!r}; this one has {sorted(principal.scopes)}.",
            },
        )
    return principal


# ---------------------------------------------------------------------------
# Known names and the anonymizer
# ---------------------------------------------------------------------------


async def load_known_names(session: AsyncSession, household_id: uuid.UUID) -> KnownNames:
    """Every name the household has told the app, by kind. Order is priority:
    where two kinds share a name, the first one's pseudonym is used everywhere."""
    names = KnownNames()

    async def col(*cols) -> list:
        return list((await session.execute(select(*cols))).all())

    names.extend("Account", (r[0] for r in await col(Account.name)))
    names.extend("Owner", (r[0] for r in await col(Owner.name)))
    names.extend("Security", (r[0] for r in await col(Security.name)))
    names.extend("Institution", (r[0] for r in await col(AccountConnection.org_name)))
    names.extend("Institution", (r[0] for r in await col(Account.institution)))
    names.extend("Institution", (r[0] for r in await col(SyncRun.connection_label)))
    names.extend("Category", (r[0] for r in await col(Category.name)))
    names.extend("Group", (r[0] for r in await col(CategoryGroup.name)))
    names.extend("Tag", (r[0] for r in await col(Tag.name)))
    names.extend("Rule", (r[0] for r in await col(Rule.name)))
    # Identity tables are outside RLS: reached through the household id the token
    # already proved, never by scanning.
    household = await session.get(Household, household_id)
    if household is not None:
        names.add("Household", household.name)
    people = (
        await session.execute(
            select(User.display_name, User.email)
            .join(HouseholdMember, HouseholdMember.user_id == User.id)
            .where(HouseholdMember.household_id == household_id)
        )
    ).all()
    for display_name, email in people:
        names.add("Person", display_name)
        names.add("Person", email)
    return names


async def anonymizer_for(request: Request, principal: AgentPrincipal) -> Anonymizer:
    """The request's anonymizer, built once: the token's key, the household's names."""
    cached = getattr(request.state, "agent_anonymizer", None)
    if cached is not None:
        return cached
    async with scoped_session(principal.household_id, principal.user_id) as session:
        names = await load_known_names(session, principal.household_id)
    anon = Anonymizer(pseudonym_key(get_settings().secret_key, principal.token_id), names)
    request.state.agent_anonymizer = anon
    return anon


# ---------------------------------------------------------------------------
# Running a route on the agent's behalf
# ---------------------------------------------------------------------------


@dataclass
class Result:
    status: int
    body: Any
    exposed: Exposed | None = None
    path: str = ""
    query: list[tuple[str, str]] = field(default_factory=list)


def error(status: int, code: str, message: str, hint: str | None = None, **extra: Any) -> dict:
    body: dict[str, Any] = {"code": code, "status": status, "message": message}
    if hint:
        body["hint"] = hint
    body.update(extra)
    return {"error": body}


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
}


def _check_params(e: Exposed, query: list[tuple[str, str]]) -> dict | None:
    """``None`` if the parameters may reach the route, else the error body."""
    allowed = {p.name: p for p in e.route.dependant.query_params}
    for name, value in query:
        if name in REFUSED_PARAMS:
            return error(
                400,
                "parameter_refused",
                f"Parameter {name!r} is not available to agents",
                REFUSED_PARAMS[name],
            )
        param = allowed.get(name)
        if param is None:
            return error(
                400,
                "unknown_parameter",
                f"{e.path} takes no parameter {name!r}",
                f"It takes: {sorted(allowed) or 'none'}. See the catalog.",
            )
        if _is_free_string(param.field_info.annotation):
            pattern = STRING_PARAMS.get(name)
            if pattern is None:
                return error(
                    400,
                    "parameter_refused",
                    f"Parameter {name!r} takes free text, which agents cannot send",
                    "Free text in a query is an oracle for the names it matches.",
                )
            if not pattern.fullmatch(value):
                return error(
                    400, "invalid_parameter", f"Parameter {name!r} does not match {pattern.pattern}"
                )
    return None


def _is_free_string(annotation: Any) -> bool:
    """Whether a parameter's type admits arbitrary strings (``str``, ``str | None``,
    ``list[str]``) — as opposed to a uuid, a date, a number or a ``Literal``."""
    import typing

    if annotation is str:
        return True
    return any(
        _is_free_string(a)
        for a in typing.get_args(annotation)
        if typing.get_origin(annotation) is not typing.Literal
    )


async def _call(
    request: Request, principal: AgentPrincipal, path: str, query: list[tuple[str, str]]
) -> tuple[int, bytes]:
    """``GET path?query`` through the whole app, in-process, as ``principal``."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urlencode(query).encode(),
        "headers": [(b"host", b"agent.internal"), (b"accept", b"application/json")],
        "client": ("agent", 0),
        "server": ("agent.internal", 80),
        "state": {},
        AGENT_GRANT_KEY: principal,
    }
    sent = False
    status = 500
    chunks: list[bytes] = []

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await request.app(scope, receive, send)
    return status, b"".join(chunks)


def _error_from(status: int, raw: bytes) -> dict:
    """An app error, retold without its message: an app message can interpolate
    a value (an owner's name, in a 409), so only a validation error's structure
    (``loc``/``type``/``msg``, which the app already strips of input) survives."""
    code = _STATUS_CODES.get(status, "error")
    if status == 422:
        try:
            detail = json.loads(raw).get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, list):
            cleaned = [
                {"loc": d.get("loc"), "type": d.get("type"), "msg": d.get("msg")}
                for d in detail
                if isinstance(d, dict)
            ]
            return error(status, code, "The request's parameters are invalid", errors=cleaned)
    hints = {
        404: "The id may not exist, or may belong to another household.",
        403: "The token's issuer lacks the role this route needs (owner-only routes).",
    }
    return error(status, code, f"The app answered {status}", hints.get(status))


async def fetch(
    request: Request, principal: AgentPrincipal, path: str, query: Iterable[tuple[str, str]] = ()
) -> Result:
    """Run the app's ``GET path`` for ``principal``. On success ``body`` is the
    parsed JSON — **not yet anonymized**. Anything that leaves this module goes
    through :func:`run_get` (or :func:`anonymize`)."""
    path = strip_prefix(path)
    query = list(query)
    e = match(path)
    if e is None:
        if path in EXCLUDED:
            return Result(
                404,
                error(404, "not_exposed", f"{path} is not available to agents", EXCLUDED[path]),
                None,
                path,
                query,
            )
        return Result(
            404,
            error(
                404,
                "no_such_route",
                f"No readable route at {path}",
                "GET the catalog for every route an agent can read.",
            ),
            None,
            path,
            query,
        )
    refused = _check_params(e, query)
    if refused is not None:
        return Result(400, refused, e, path, query)

    status, raw = await _call(request, principal, path, query)
    log.info("agent.read", token_id=str(principal.token_id), route=e.path, status=status)
    if status != 200:
        return Result(status, _error_from(status, raw), e, path, query)
    try:
        body = json.loads(raw)
    except ValueError:
        # Never fall back to the raw body: an unparsed body is an unanonymized one.
        log.error("agent.response_invalid", route=e.path)
        return Result(
            500,
            error(500, "response_invalid", "The route's response could not be read"),
            e,
            path,
            query,
        )
    return Result(200, body, e, path, query)


async def fetch_model(
    request: Request, principal: AgentPrincipal, path: str, query: Iterable[tuple[str, str]] = ()
) -> Result:
    """:func:`fetch`, with ``body`` validated into the route's schema — for the debug
    views, which compose it into a larger schema and anonymize the whole."""
    result = await fetch(request, principal, path, query)
    if result.status == 200:
        try:
            result.body = TypeAdapter(result.exposed.response_model).validate_python(result.body)
        except Exception:  # noqa: BLE001 - any failure here is a 500, never a leak
            log.error("agent.response_invalid", route=result.exposed.path)
            return Result(
                500,
                error(500, "response_invalid", "The route's response could not be read"),
                result.exposed,
                result.path,
                result.query,
            )
    return result


async def run_get(
    request: Request, principal: AgentPrincipal, path: str, query: Iterable[tuple[str, str]] = ()
) -> Result:
    """Run the app's ``GET path`` for ``principal`` and anonymize what it returns."""
    result = await fetch(request, principal, path, query)
    if result.status == 200:
        anon = await anonymizer_for(request, principal)
        result.body = _checked(anon, anon.walk_json(result.exposed.response_model, result.body))
    return result


async def anonymize(request: Request, principal: AgentPrincipal, value: Any) -> Any:
    """A schema instance composed by a debug view, anonymized."""
    anon = await anonymizer_for(request, principal)
    return _checked(anon, anon.walk(value))


def _checked(anon: Anonymizer, out: Any) -> Any:
    if anon.unclassified:
        # Dropped, so nothing leaked — but a field an agent cannot see is a
        # field somebody forgot to classify. The policy test should have caught it.
        log.warning("agent.unclassified_fields", fields=sorted(anon.unclassified))
    return out


@asynccontextmanager
async def agent_session(principal: AgentPrincipal) -> AsyncIterator[AsyncSession]:
    """A household-scoped session that cannot write — for the debug routes that
    query directly rather than through an app route."""
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await set_scope(session, household_id=principal.household_id, user_id=principal.user_id)
        yield session


def split_target(target: str) -> tuple[str, list[tuple[str, str]]]:
    """``/api/reports/net-worth?end=…`` → ``("/reports/net-worth", [("end", "…")])``."""
    path, _, qs = target.partition("?")
    return strip_prefix(path or "/"), parse_qsl(qs, keep_blank_values=True)
