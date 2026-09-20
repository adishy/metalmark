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
from app.schemas.transfers import (
    TransferCandidateOut,
    TransferCandidatesOut,
    TransferDetailOut,
)
from app.services import transactions as svc
from app.services.ownership import account_owner_map, effective_owner_id

router = APIRouter(prefix="/transactions", tags=["transactions"])


async def _serialize(session, rows: list[Transaction]) -> list[TransactionOut]:
    """Fill the response fields that are computed rather than stored.

    ``effective_owner_id`` is resolved through one account-owner map for the whole
    page, never a query per row. It is attached to the ORM objects as a transient
    attribute (there is no such column — the value would fan out across every
    transaction whenever an account changed hands, ADR-0026) so the response schema
    can declare it non-nullable.
    """
    owners = await account_owner_map(session, {r.account_id for r in rows})
    tag_map = await svc._tag_ids_for(session, [r.id for r in rows])
    items = []
    for r in rows:
        account_owner_id = owners[r.account_id]
        r.effective_owner_id = effective_owner_id(
            split_owner_id=None,
            transaction_owner_id=r.owner_id,
            account_owner_id=account_owner_id,
        )
        for split in r.splits:
            split.effective_owner_id = effective_owner_id(
                split_owner_id=split.owner_id,
                transaction_owner_id=r.owner_id,
                account_owner_id=account_owner_id,
            )
        model = TransactionOut.model_validate(r)
        model.tag_ids = tag_map.get(r.id, [])
        items.append(model)
    return items


async def _out(ctx: RequestContext, txn: Transaction) -> TransactionOut:
    return (await _serialize(ctx.session, [txn]))[0]


@router.post("", response_model=TransactionOut, status_code=201)
async def create_transaction(data: TransactionCreate, ctx: RequestContext = Depends(get_context)):
    txn = await svc.create_transaction(ctx.session, ctx.household_id, data)
    return await _out(ctx, await svc.get_transaction(ctx.session, txn.id))


@router.get("", response_model=TransactionPage)
async def list_transactions(
    ctx: RequestContext = Depends(get_context),
    account_id: list[uuid.UUID] | None = Query(default=None),
    category_id: list[uuid.UUID] | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
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
        owner_id=owner_id,
        start=start,
        end=end,
        review_status=review_status,
        search=search,
        include_hidden=include_hidden,
        limit=limit,
        cursor=cursor,
    )
    return TransactionPage(
        items=await _serialize(ctx.session, rows), next_cursor=next_cursor
    )


# Declared before ``/{txn_id}`` deliberately. Starlette matches routes in
# declaration order and validates path params afterwards, so the parameterized
# route would match this path first and answer 422 for "transfer-candidates"
# not being a UUID.
@router.get("/transfer-candidates", response_model=TransferCandidatesOut)
async def list_transfer_candidates(
    txn_id: uuid.UUID = Query(...),
    days: int = Query(default=5, ge=1, le=30),
    ctx: RequestContext = Depends(get_context),
):
    """Counterpart legs for ``txn_id``, each priced, for the manual match picker.

    ``days`` is bounded on both sides: it widens a scan over the household's whole
    transaction history, so a caller passing a large number is asking for a query
    whose cost it cannot see. 30 days is already generous for "the other leg
    posted around then".
    """
    candidates = await svc.list_transfer_candidates(
        ctx.session, ctx.household_id, txn_id, days=days
    )
    # Serialized through the ordinary transaction shape so a candidate carries the
    # same fields (currency, base_amount, effective owner) as the row the user is
    # looking at — the picker shows both legs and reads them alike.
    items = await _serialize(ctx.session, [c.txn for c in candidates])
    return TransferCandidatesOut(
        items=[
            TransferCandidateOut(
                transaction=out,
                days_apart=c.days_apart,
                fx_cost_base=c.fx_cost_base,
                within_tolerance=c.within_tolerance,
            )
            for c, out in zip(candidates, items, strict=True)
        ]
    )


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


@router.get("/transfers/{group_id}", response_model=TransferDetailOut)
async def get_transfer(group_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    """Read a link back whole: a leg carries only its group id, so this is how a
    detail view finds the other leg (and the residual the pair cost)."""
    group, legs = await svc.get_transfer_group(ctx.session, ctx.household_id, group_id)
    return TransferDetailOut(
        transfer_group_id=group.id,
        matched_by=group.matched_by,
        fx_cost_base=group.fx_cost_base,
        legs=await _serialize(ctx.session, legs),
    )


@router.delete("/transfers/{group_id}", status_code=204)
async def unlink_transfer(group_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    """Unlink a transfer, returning both legs to cash-flow and spending.

    No body and no 200: the resource the caller named is gone, and the legs'
    restored state is visible by re-reading them (or the reports) like any other
    change.
    """
    await svc.unlink_transfer(ctx.session, ctx.household_id, group_id)
