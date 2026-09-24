"""Agent token administration (ADR-0048).

Session-authenticated like the rest of the app, and limited to administrators and
the household's owner (``deps.require_admin``). The token itself is in the
response to ``POST`` and nowhere else, ever: the server keeps only its hash.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.agent import tokens as svc
from app.db import unscoped_session
from app.deps import RequestContext, require_admin
from app.logging import get_logger
from app.models import User
from app.models.agent import AgentToken
from app.schemas.agent import AgentTokenCreate, AgentTokenCreated, AgentTokenOut

router = APIRouter(prefix="/admin/agent-tokens", tags=["agent"])
log = get_logger("agent")


def _out(token: AgentToken, created_by: str) -> AgentTokenOut:
    return AgentTokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        scopes=list(token.scopes),
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
        created_by=created_by,
        status=svc.status(token),
    )


@router.get("", response_model=list[AgentTokenOut])
async def list_tokens(ctx: RequestContext = Depends(require_admin)):
    """Every token issued in this household, newest first."""
    async with unscoped_session() as session:
        rows = await svc.list_for_household(session, ctx.household_id)
    return [_out(t, name) for t, name in rows]


@router.post("", response_model=AgentTokenCreated, status_code=201)
async def create_token(data: AgentTokenCreate, ctx: RequestContext = Depends(require_admin)):
    """Issue a token. Its value is in this response and never again."""
    async with unscoped_session() as session:
        user = (await session.execute(select(User).where(User.id == ctx.user.id))).scalar_one()
        raw, token = await svc.create(
            session,
            user=user,
            name=data.name,
            scopes=list(data.scopes),
            expires_in_days=data.expires_in_days,
        )
        out = _out(token, user.display_name)
    log.info("agent.token_issued", token_id=str(token.id), scopes=list(token.scopes))
    return AgentTokenCreated(**out.model_dump(), token=raw)


@router.delete("/{token_id}", response_model=AgentTokenOut)
async def revoke_token(token_id: uuid.UUID, ctx: RequestContext = Depends(require_admin)):
    """Revoke a token. It stops working on its next request."""
    async with unscoped_session() as session:
        token, name = await svc.revoke(session, ctx.household_id, token_id)
        out = _out(token, name)
    log.info("agent.token_revoked", token_id=str(token_id))
    return out
