"""``/anon_debug`` — anonymized views for debugging the app (ADR-0048).

* ``GET /anon_debug`` — the catalog of these views.
* ``GET /anon_debug/view?path=…`` — any readable app route, exactly as the page
  receives it (paste a URL from the browser's network tab).
* ``GET /anon_debug/pages[/{page}]`` — what each page of the app loads.
* ``GET /anon_debug/transactions/{id}/explain`` — provenance, the rule trace, the
  owner chain, the transfer link.
* ``GET /anon_debug/accounts/{id}/balance`` — snapshots, drift, holdings, checks.
* ``GET /anon_debug/system`` — versions, settings, counts, FX coverage, health.

Authenticated by an agent token with the ``debug:read`` scope. Read-only, and
anonymized by the same registry as ``/agent``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.agent import catalog, debug, dispatch
from app.agent.tokens import AgentPrincipal
from app.models.agent import SCOPE_DEBUG_READ
from app.schemas.agent import CatalogOut

router = APIRouter(prefix="/anon_debug", tags=["agent"])


async def debug_principal(request: Request) -> AgentPrincipal:
    return await dispatch.principal_for(request, SCOPE_DEBUG_READ)


def _pages() -> dict[str, str]:
    return {key: desc for key, (desc, _calls) in debug.PAGES.items()}


def _public(path: str, query: list[tuple[str, str]]) -> str:
    from urllib.parse import urlencode

    return f"{dispatch.PUBLIC_PREFIX}{path}" + (f"?{urlencode(query)}" if query else "")


@router.get("", response_model=CatalogOut)
async def debug_index(_principal: AgentPrincipal = Depends(debug_principal)) -> CatalogOut:
    """The catalog of the debug views. Start here."""
    return catalog.debug_catalog(_pages())


@router.get("/view")
async def view(
    request: Request,
    path: str = Query(..., description="An app API path with its query string."),
    principal: AgentPrincipal = Depends(debug_principal),
) -> JSONResponse:
    """Any readable app route, anonymized: what the page receives from it."""
    target, query = dispatch.split_target(path)
    result = await dispatch.run_get(request, principal, target, query)
    body = {"path": _public(target, query), "status": result.status, "body": result.body}
    return JSONResponse(body, status_code=result.status)


@router.get("/pages")
async def list_pages(_principal: AgentPrincipal = Depends(debug_principal)) -> JSONResponse:
    """The app's pages, and the requests each one makes."""
    window = debug.page_window(None, None, None)
    return JSONResponse(
        [
            {
                "page": key,
                "description": desc,
                "href": f"{catalog.DEBUG_BASE}/pages/{key}",
                "calls": [_public(p, q) for p, q in debug.page_calls(key, window)],
            }
            for key, (desc, _calls) in debug.PAGES.items()
        ]
    )


@router.get("/pages/{page}")
async def show_page(
    page: str,
    request: Request,
    start: date | None = None,
    end: date | None = None,
    owner_id: uuid.UUID | None = None,
    principal: AgentPrincipal = Depends(debug_principal),
) -> JSONResponse:
    """Everything one page loads, each response anonymized as the page gets it."""
    if page not in debug.PAGES:
        return JSONResponse(
            dispatch.error(
                404, "no_such_page", f"No page {page!r}", f"Pages: {sorted(debug.PAGES)}"
            ),
            status_code=404,
        )
    window = debug.page_window(start, end, owner_id)
    calls = []
    for path, query in debug.page_calls(page, window):
        result = await dispatch.run_get(request, principal, path, query)
        calls.append({"path": _public(path, query), "status": result.status, "body": result.body})
    return JSONResponse(
        {
            "page": page,
            "description": debug.PAGES[page][0],
            "params": {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "owner_id": str(window.owner_id) if window.owner_id else None,
            },
            "calls": calls,
        }
    )


async def _view(request: Request, principal: AgentPrincipal, build) -> JSONResponse:
    try:
        value = await build
    except debug.ViewError as exc:
        return JSONResponse(exc.result.body, status_code=exc.result.status)
    return JSONResponse(await dispatch.anonymize(request, principal, value))


@router.get("/transactions/{txn_id}/explain")
async def explain_transaction(
    txn_id: uuid.UUID, request: Request, principal: AgentPrincipal = Depends(debug_principal)
) -> JSONResponse:
    """Why a transaction looks as it does: provenance, rules, owner, transfer."""
    return await _view(request, principal, debug.explain_transaction(request, principal, txn_id))


@router.get("/accounts/{account_id}/balance")
async def explain_balance(
    account_id: uuid.UUID, request: Request, principal: AgentPrincipal = Depends(debug_principal)
) -> JSONResponse:
    """How an account's balance is built: snapshots, drift, holdings, checks."""
    return await _view(request, principal, debug.explain_balance(request, principal, account_id))


@router.get("/system")
async def system(
    request: Request, principal: AgentPrincipal = Depends(debug_principal)
) -> JSONResponse:
    """Versions, non-secret settings, counts, FX coverage and connection health."""
    return await _view(request, principal, debug.system(request, principal))
