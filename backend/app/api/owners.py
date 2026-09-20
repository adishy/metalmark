from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, get_context, require_owner
from app.schemas.owners import (
    OwnerCreate,
    OwnerDeleteResult,
    OwnerOut,
    OwnerUpdate,
)
from app.services import owners as svc

router = APIRouter(prefix="/owners", tags=["owners"])


@router.get("", response_model=list[OwnerOut])
async def list_owners(ctx: RequestContext = Depends(get_context)):
    return [OwnerOut.model_validate(o) for o in await svc.list_owners(ctx.session)]


@router.post("", response_model=OwnerOut, status_code=201)
async def create_owner(data: OwnerCreate, ctx: RequestContext = Depends(get_context)):
    owner = await svc.create_owner(ctx.session, ctx.household_id, name=data.name, sort=data.sort)
    return OwnerOut.model_validate(owner)


@router.patch("/{owner_id}", response_model=OwnerOut)
async def update_owner(owner_id: uuid.UUID, data: OwnerUpdate,
                       ctx: RequestContext = Depends(get_context)):
    owner = await svc.get_owner(ctx.session, owner_id)
    return OwnerOut.model_validate(
        await svc.update_owner(ctx.session, owner, name=data.name, sort=data.sort)
    )


@router.delete("/{owner_id}", response_model=OwnerDeleteResult)
async def delete_owner(
    owner_id: uuid.UUID,
    reassign_to: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(require_owner),
) -> OwnerDeleteResult:
    """Delete an owner, moving its rows to ``reassign_to`` (default: Shared).

    Owner-only: the operation silently re-attributes existing ledger history, which
    is a household-shape decision, not an everyday edit.
    """
    owner = await svc.get_owner(ctx.session, owner_id)
    counts = await svc.delete_owner(ctx.session, owner, reassign_to=reassign_to)
    return OwnerDeleteResult(**counts)
