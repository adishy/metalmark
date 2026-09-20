from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import RequestContext, get_context
from app.models import Household
from app.schemas.household import HouseholdOut, MemberOut
from app.services import auth as auth_service

router = APIRouter(prefix="/household", tags=["household"])


@router.get("", response_model=HouseholdOut)
async def get_household(ctx: RequestContext = Depends(get_context)):
    hh = (
        await ctx.session.execute(select(Household).where(Household.id == ctx.household_id))
    ).scalar_one()
    return HouseholdOut(
        id=hh.id,
        name=hh.name,
        base_currency=hh.base_currency,
        timezone=hh.timezone,
        role=ctx.role,
    )


@router.get("/members", response_model=list[MemberOut])
async def list_members(ctx: RequestContext = Depends(get_context)):
    rows = await auth_service.list_members(ctx.session, ctx.household_id)
    return [MemberOut(**r) for r in rows]
