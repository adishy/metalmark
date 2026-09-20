from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import RequestContext, get_context, require_owner
from app.models import Household
from app.schemas.household import HouseholdOut, HouseholdUpdate, MemberOut
from app.services import auth as auth_service

router = APIRouter(prefix="/household", tags=["household"])


def _out(hh: Household, role: str) -> HouseholdOut:
    return HouseholdOut(
        id=hh.id,
        name=hh.name,
        base_currency=hh.base_currency,
        timezone=hh.timezone,
        role=role,
    )


@router.get("", response_model=HouseholdOut)
async def get_household(ctx: RequestContext = Depends(get_context)):
    hh = (
        await ctx.session.execute(select(Household).where(Household.id == ctx.household_id))
    ).scalar_one()
    return _out(hh, ctx.role)


@router.patch("", response_model=HouseholdOut)
async def update_household(
    data: HouseholdUpdate, ctx: RequestContext = Depends(require_owner)
) -> HouseholdOut:
    """Owner-only: the household name is what every member sees as their account."""
    hh = await auth_service.update_household(
        ctx.session, ctx.household_id, name=data.name, timezone=data.timezone
    )
    return _out(hh, ctx.role)


@router.get("/members", response_model=list[MemberOut])
async def list_members(ctx: RequestContext = Depends(get_context)):
    rows = await auth_service.list_members(ctx.session, ctx.household_id)
    return [MemberOut(**r) for r in rows]
