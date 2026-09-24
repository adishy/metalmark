"""The self-describing catalog an agent starts from (ADR-0048).

``GET /agent`` (and ``/agent/v1``) and ``GET /anon_debug`` answer with every route
an agent can call: its path, parameters, what it returns and what it is for. It is
built from the routes themselves — the path, the parameter types, the handler's
docstring — so it cannot drift from what the app does; ``test_agent_api`` follows
every link it offers and expects each to answer.
"""

from __future__ import annotations

import typing
from datetime import date
from typing import Any

from fastapi.routing import APIRoute

from app.agent import dispatch
from app.schemas.agent import CatalogOut, ParamOut, RouteOut

AGENT_VERSION = "v1"
AGENT_BASE = f"{dispatch.PUBLIC_PREFIX}/agent/{AGENT_VERSION}"
DEBUG_BASE = f"{dispatch.PUBLIC_PREFIX}/anon_debug"

ANONYMIZATION = [
    "Read-only: every request runs in a database transaction that cannot write.",
    "Ids (uuids), amounts, dates, counts and flags are real.",
    "Names and free text (accounts, owners, institutions, securities, merchants, "
    "descriptions, notes, custom categories and tags, people) are pseudonyms like "
    "'Account 3f9a2c': stable for this token, so equal pseudonyms mean equal values, "
    "and different for every token.",
    "Generic category names (Groceries, Rent, …) and the 'Shared' owner are kept.",
    "Sentences the app writes (warnings, check summaries) keep their words; names in "
    "them are replaced by the same pseudonyms, and number shapes like account numbers, "
    "SSNs and e-mail addresses are masked.",
    "E-mail addresses and the session's CSRF token are never returned.",
    "Free-text query parameters (search) are refused: a match would reveal the text.",
]

AUTH = (
    "Authorization: Bearer mmk_… — an agent token, issued by an administrator or the "
    "household's owner under Admin → Agent access. /agent needs the 'agent:read' "
    "scope, /anon_debug needs 'debug:read'."
)


def _type_name(annotation: Any) -> tuple[str, list[str] | None]:
    """A short type for a parameter, and its allowed values if it is a Literal."""
    origin = typing.get_origin(annotation)
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if origin is typing.Literal:
        return "string", [str(a) for a in typing.get_args(annotation)]
    if args and origin is not None:
        if origin is list:
            inner, enum = _type_name(args[0])
            return f"list[{inner}]", enum
        if len(args) == 1:
            return _type_name(args[0])
    name = getattr(annotation, "__name__", str(annotation))
    return {
        "UUID": "uuid",
        "str": "string",
        "int": "integer",
        "bool": "boolean",
        "date": "date",
        "datetime": "datetime",
        "Decimal": "decimal",
    }.get(name, name), None


def _first_paragraph(text: str | None) -> str:
    if not text:
        return ""
    para = text.strip().split("\n\n", 1)[0]
    return " ".join(para.split())


def _params(route: APIRoute) -> list[ParamOut]:
    out = []
    for location, params in (
        ("path", route.dependant.path_params),
        ("query", route.dependant.query_params),
    ):
        for p in params:
            if p.name in dispatch.REFUSED_PARAMS:
                continue
            type_name, enum = _type_name(p.field_info.annotation)
            desc = None
            if p.name in dispatch.ID_SOURCES:
                desc = f"An id from {dispatch.ID_SOURCES[p.name]}."
            elif p.name in dispatch.STRING_PARAMS:
                desc = f"Must match {dispatch.STRING_PARAMS[p.name].pattern}."
            if p.name == "cursor":
                desc = "The next_cursor of the previous page."
            out.append(
                ParamOut(
                    name=p.name,
                    location=location,
                    required=p.field_info.is_required(),
                    type=type_name,
                    description=desc,
                    enum=enum,
                )
            )
    return out


def _returns(route: APIRoute) -> str | None:
    model = route.response_model
    if model is None:
        return None
    origin = typing.get_origin(model)
    if origin is list:
        return f"list[{typing.get_args(model)[0].__name__}]"
    return getattr(model, "__name__", str(model))


def app_routes(base: str) -> list[RouteOut]:
    """Every exposed app route, as reachable under ``base``."""
    out = []
    today = date.today().isoformat()
    for e in dispatch.exposed():
        params = _params(e.route)
        required = [p for p in params if p.location == "path" or p.required]
        href: str | None = f"{base}{e.path}"
        if required:
            # A required date is filled with today, so a report is one click away;
            # anything else required (an id) has to come from another route.
            if all(p.location == "query" and p.type == "date" for p in required):
                href += "?" + "&".join(f"{p.name}={today}" for p in required)
            else:
                href = None
        out.append(
            RouteOut(
                name=e.route.name,
                method="GET",
                path=f"{base}{e.path}",
                href=href,
                description=_first_paragraph(e.route.description) or e.route.name.replace("_", " "),
                params=params,
                returns=_returns(e.route),
            )
        )
    return out


def agent_catalog() -> CatalogOut:
    return CatalogOut(
        name="MetalMark agent API",
        version=AGENT_VERSION,
        description=(
            "A read-only, anonymized mirror of the app's own API. Every route below is "
            f"the app route of the same path (GET {AGENT_BASE}/accounts answers what the "
            "Accounts page reads from /api/accounts), run as the token's issuer. Start "
            "with routes that take no input (they have an href), then follow ids into "
            "the ones that do. Debugging views — a page as the user sees it, why a "
            f"transaction is categorized as it is — are at {DEBUG_BASE}."
        ),
        anonymization=ANONYMIZATION,
        auth=AUTH,
        routes=app_routes(AGENT_BASE),
        links={
            "self": AGENT_BASE,
            "debug": DEBUG_BASE,
            "openapi": f"{dispatch.PUBLIC_PREFIX}/openapi.json",
        },
    )


def _debug_route(
    name: str,
    path: str,
    description: str,
    params: list[ParamOut] | None = None,
    returns: str | None = None,
    href: bool = True,
) -> RouteOut:
    params = params or []
    return RouteOut(
        name=name,
        method="GET",
        path=f"{DEBUG_BASE}{path}",
        href=f"{DEBUG_BASE}{path}"
        if href and "{" not in path and not any(p.required for p in params)
        else None,
        description=description,
        params=params,
        returns=returns,
    )


def debug_catalog(pages: dict[str, str]) -> CatalogOut:
    view_param = ParamOut(
        name="path",
        location="query",
        required=True,
        type="string",
        description=(
            "An app API path with its query string, as the browser requests it "
            "(e.g. /api/reports/net-worth?start=2026-01-01&end=2026-09-30). "
            "The /api prefix is optional."
        ),
    )
    routes = [
        _debug_route(
            "view",
            "/view",
            (
                "Any readable app route, exactly as the page receives it, anonymized. Paste a "
                "request URL from the browser's network tab."
            ),
            [view_param],
            "the route's own schema",
        ),
        _debug_route(
            "pages", "/pages", "The app's pages and the requests each one makes.", returns="list"
        ),
        *[
            _debug_route(
                f"page_{key}",
                f"/pages/{key}",
                desc,
                [
                    ParamOut(
                        name="start",
                        location="query",
                        required=False,
                        type="date",
                        description="Report window start (default: a year before end).",
                    ),
                    ParamOut(
                        name="end",
                        location="query",
                        required=False,
                        type="date",
                        description="Report window end (default: today).",
                    ),
                    ParamOut(
                        name="owner_id",
                        location="query",
                        required=False,
                        type="uuid",
                        description="The owner filter chip, as on the page.",
                    ),
                ],
                "PageOut",
            )
            for key, desc in pages.items()
        ],
        _debug_route(
            "explain_transaction",
            "/transactions/{txn_id}/explain",
            (
                "Why a transaction looks the way it does: provenance of each field, every "
                "rule evaluated condition by condition, owner resolution, transfer link."
            ),
            [
                ParamOut(
                    name="txn_id",
                    location="path",
                    required=True,
                    type="uuid",
                    description="An id from transactions.",
                )
            ],
            "TransactionExplainOut",
        ),
        _debug_route(
            "explain_balance",
            "/accounts/{account_id}/balance",
            (
                "How an account's balance is built: snapshots, transactions since the last "
                "one, holdings, and the data checks that flag it."
            ),
            [
                ParamOut(
                    name="account_id",
                    location="path",
                    required=True,
                    type="uuid",
                    description="An id from accounts.",
                )
            ],
            "BalanceExplainOut",
        ),
        _debug_route(
            "system",
            "/system",
            (
                "Version, schema revision, non-secret settings, row counts, FX coverage, "
                "connection health and the data checks' verdicts."
            ),
            returns="SystemOut",
        ),
    ]
    return CatalogOut(
        name="MetalMark anonymized debug API",
        version=AGENT_VERSION,
        description=(
            "Views for debugging the app: what a page shows, why a row or a balance is "
            f"what it is, and the state of the system. The structured API is at {AGENT_BASE}; "
            f"every route there can also be read through {DEBUG_BASE}/view."
        ),
        anonymization=ANONYMIZATION,
        auth=AUTH,
        routes=routes,
        links={"self": DEBUG_BASE, "agent": AGENT_BASE},
    )
