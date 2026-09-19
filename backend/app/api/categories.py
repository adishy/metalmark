from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.ledger import (
    CategoryCreate,
    CategoryGroupCreate,
    CategoryGroupOut,
    CategoryOut,
    TagCreate,
    TagOut,
)
from app.services import ledger

router = APIRouter(tags=["categories"])


@router.get("/category-groups", response_model=list[CategoryGroupOut])
async def list_groups(ctx: RequestContext = Depends(get_context)):
    return [CategoryGroupOut.model_validate(g) for g in await ledger.list_category_groups(ctx.session)]


@router.post("/category-groups", response_model=CategoryGroupOut, status_code=201)
async def create_group(data: CategoryGroupCreate, ctx: RequestContext = Depends(get_context)):
    g = await ledger.create_category_group(ctx.session, ctx.household_id, data.name, data.type, data.sort)
    return CategoryGroupOut.model_validate(g)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(ctx: RequestContext = Depends(get_context)):
    return [CategoryOut.model_validate(c) for c in await ledger.list_categories(ctx.session)]


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(data: CategoryCreate, ctx: RequestContext = Depends(get_context)):
    c = await ledger.create_category(
        ctx.session, ctx.household_id, data.group_id, data.name, data.icon, data.color, data.sort
    )
    return CategoryOut.model_validate(c)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(ctx: RequestContext = Depends(get_context)):
    return [TagOut.model_validate(t) for t in await ledger.list_tags(ctx.session)]


@router.post("/tags", response_model=TagOut, status_code=201)
async def create_tag(data: TagCreate, ctx: RequestContext = Depends(get_context)):
    t = await ledger.create_tag(ctx.session, ctx.household_id, data.name, data.color)
    return TagOut.model_validate(t)
