"""``/agent`` — the structured, anonymized, read-only API for agents (ADR-0048).

``GET /agent`` and ``GET /agent/v1`` return the catalog: every route, its
parameters and what it returns, so an agent can discover the API by calling it.
``GET /agent/v1/<path>`` is the app's own ``GET /<path>``, run as the token's
issuer in a read-only transaction and anonymized (``app/agent/dispatch.py``).

Authenticated by an agent token with the ``agent:read`` scope — never by the
session cookie.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.agent import catalog, dispatch
from app.agent.tokens import AgentPrincipal
from app.models.agent import SCOPE_AGENT_READ
from app.schemas.agent import CatalogOut

router = APIRouter(prefix="/agent", tags=["agent"])


async def agent_principal(request: Request) -> AgentPrincipal:
    return await dispatch.principal_for(request, SCOPE_AGENT_READ)


def _links() -> dict[str, str]:
    return {"Link": f'<{catalog.AGENT_BASE}>; rel="index", <{catalog.DEBUG_BASE}>; rel="debug"'}


@router.get("", response_model=CatalogOut)
async def agent_index(_principal: AgentPrincipal = Depends(agent_principal)) -> CatalogOut:
    """The catalog of every route an agent can read. Start here."""
    return catalog.agent_catalog()


@router.get("/v1", response_model=CatalogOut)
async def agent_v1_index(_principal: AgentPrincipal = Depends(agent_principal)) -> CatalogOut:
    """The catalog of every route in v1 (the same as ``GET /agent``)."""
    return catalog.agent_catalog()


@router.get("/v1/{path:path}")
async def agent_read(
    path: str, request: Request, principal: AgentPrincipal = Depends(agent_principal)
) -> JSONResponse:
    """The app's ``GET /{path}``, anonymized. See the catalog for every path."""
    result = await dispatch.run_get(
        request, principal, "/" + path, request.query_params.multi_items()
    )
    return JSONResponse(result.body, status_code=result.status, headers=_links())
