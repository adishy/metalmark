"""Owner income profile and paystubs (ADR-0052).

Open to any household member, like the ledger itself — an owner's income
profile is household data (ADR-0026's framing: owners are attribution labels,
not accounts with their own access boundary), not a control-panel setting.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.income import (
    OwnerIncomeProfileFields,
    OwnerIncomeProfileOut,
    OwnerIncomeProfileUpdate,
    PaystubCreate,
    PaystubOut,
    PaystubPatch,
)
from app.services import income as svc
from app.services import owners as owners_svc

router = APIRouter(prefix="/owners/{owner_id}", tags=["income"])


async def _profile_out(session, owner_id: uuid.UUID) -> OwnerIncomeProfileOut:
    profile = await svc.get_profile(session, owner_id)
    summary = await svc.compute_summary(session, owner_id, profile)
    return OwnerIncomeProfileOut(
        owner_id=owner_id,
        profile=OwnerIncomeProfileFields.model_validate(profile) if profile else None,
        summary=summary,
    )


@router.get("/income-profile", response_model=OwnerIncomeProfileOut)
async def get_income_profile(owner_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await owners_svc.get_owner(ctx.session, owner_id)  # 404 if not this household's
    return await _profile_out(ctx.session, owner_id)


@router.put("/income-profile", response_model=OwnerIncomeProfileOut)
async def put_income_profile(
    owner_id: uuid.UUID, data: OwnerIncomeProfileUpdate,
    ctx: RequestContext = Depends(get_context),
):
    await owners_svc.get_owner(ctx.session, owner_id)
    await svc.upsert_profile(ctx.session, ctx.household_id, owner_id, data)
    return await _profile_out(ctx.session, owner_id)


@router.get("/paystubs", response_model=list[PaystubOut])
async def list_paystubs(owner_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await owners_svc.get_owner(ctx.session, owner_id)
    return [PaystubOut.model_validate(p) for p in await svc.list_paystubs(ctx.session, owner_id)]


@router.post("/paystubs", response_model=PaystubOut, status_code=201)
async def create_paystub(
    owner_id: uuid.UUID, data: PaystubCreate, ctx: RequestContext = Depends(get_context),
):
    await owners_svc.get_owner(ctx.session, owner_id)
    paystub = await svc.create_paystub(ctx.session, ctx.household_id, owner_id, data)
    return PaystubOut.model_validate(paystub)


@router.get("/paystubs/{paystub_id}", response_model=PaystubOut)
async def get_paystub(
    owner_id: uuid.UUID, paystub_id: uuid.UUID, ctx: RequestContext = Depends(get_context),
):
    await owners_svc.get_owner(ctx.session, owner_id)
    return PaystubOut.model_validate(await svc.get_paystub(ctx.session, paystub_id))


@router.patch("/paystubs/{paystub_id}", response_model=PaystubOut)
async def update_paystub(
    owner_id: uuid.UUID, paystub_id: uuid.UUID, data: PaystubPatch,
    ctx: RequestContext = Depends(get_context),
):
    await owners_svc.get_owner(ctx.session, owner_id)
    paystub = await svc.update_paystub(ctx.session, paystub_id, data)
    return PaystubOut.model_validate(paystub)


@router.delete("/paystubs/{paystub_id}", status_code=204)
async def delete_paystub(
    owner_id: uuid.UUID, paystub_id: uuid.UUID, ctx: RequestContext = Depends(get_context),
):
    await owners_svc.get_owner(ctx.session, owner_id)
    await svc.delete_paystub(ctx.session, paystub_id)
