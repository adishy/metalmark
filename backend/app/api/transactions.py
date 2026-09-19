from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, get_context
from app.models import Transaction
from app.schemas.transactions import (
    SplitIn,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
    TransferLink,
    TransferOut,
)
from app.services import transactions as svc

router = APIRouter(prefix="/transactions", tags=["transactions"])


async def _out(ctx: RequestContext, txn: Transaction) -> TransactionOut:
    tag_map = await svc._tag_ids_for(ctx.session, [txn.id])
    model = TransactionOut.model_validate(txn)
    model.tag_ids = tag_map.get(txn.id, [])
    return model


@router.post("", response_model=TransactionOut, status_code=201)
async def create_transaction(data: TransactionCreate, ctx: RequestContext = Depends(get_context)):
    txn = await svc.create_transaction(ctx.session, ctx.household_id, data)
    return await _out(ctx, await svc.get_transaction(ctx.session, txn.id))


@router.get("", response_model=TransactionPage)
async def list_transactions(
    ctx: RequestContext = Depends(get_context),
    account_id: list[uuid.UUID] | None = Query(default=None),
    category_id: list[uuid.UUID] | None = Query(default=None),
    start: datetime | None = None,
    end: datetime | None = None,
    review_status: str | None = None,
    search: str | None = None,
    include_hidden: bool = False,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    rows, next_cursor = await svc.list_transactions(
        ctx.session,
        account_ids=account_id,
        category_ids=category_id,
        start=start,
        end=end,
        review_status=review_status,
        search=search,
        include_hidden=include_hidden,
        limit=limit,
        cursor=cursor,
    )
    tag_map = await svc._tag_ids_for(ctx.session, [r.id for r in rows])
    items = []
    for r in rows:
        m = TransactionOut.model_validate(r)
        m.tag_ids = tag_map.get(r.id, [])
        items.append(m)
    return TransactionPage(items=items, next_cursor=next_cursor)


@router.get("/{txn_id}", response_model=TransactionOut)
async def get_transaction(txn_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    return await _out(ctx, await svc.get_transaction(ctx.session, txn_id))


@router.patch("/{txn_id}", response_model=TransactionOut)
async def update_transaction(txn_id: uuid.UUID, data: TransactionUpdate,
                             ctx: RequestContext = Depends(get_context)):
    txn = await svc.update_transaction(ctx.session, ctx.household_id, txn_id, data)
    return await _out(ctx, await svc.get_transaction(ctx.session, txn.id))


@router.delete("/{txn_id}", status_code=204)
async def delete_transaction(txn_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await svc.delete_transaction(ctx.session, txn_id)


@router.put("/{txn_id}/splits", response_model=TransactionOut)
async def replace_splits(txn_id: uuid.UUID, splits: list[SplitIn],
                         ctx: RequestContext = Depends(get_context)):
    txn = await svc.replace_splits(ctx.session, txn_id, splits)
    return await _out(ctx, txn)


@router.post("/transfers", response_model=TransferOut, status_code=201)
async def link_transfer(data: TransferLink, ctx: RequestContext = Depends(get_context)):
    group = await svc.link_transfer(ctx.session, ctx.household_id, data.from_txn_id, data.to_txn_id)
    return TransferOut(
        transfer_group_id=group.id,
        matched_by=group.matched_by,
        fx_cost_base=group.fx_cost_base,
        txn_ids=[data.from_txn_id, data.to_txn_id],
    )
