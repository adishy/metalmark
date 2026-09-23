from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, get_context
from app.schemas.ledger import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    NetWorthOut,
)
from app.services import ledger

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _out(acct, stale: dict) -> AccountOut:
    return AccountOut.model_validate(acct).model_copy(
        update={"stale_since": stale.get(acct.id)}
    )


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(data: AccountCreate, ctx: RequestContext = Depends(get_context)):
    acct = await ledger.create_account(ctx.session, ctx.household_id, data)
    return AccountOut.model_validate(acct)


@router.get("", response_model=list[AccountOut])
async def list_accounts(
    ctx: RequestContext = Depends(get_context),
    owner_id: uuid.UUID | None = Query(default=None),
):
    stale = await ledger.stale_since(ctx.session)
    return [_out(a, stale) for a in await ledger.list_accounts(ctx.session, owner_id)]


@router.get("/net-worth", response_model=NetWorthOut)
async def net_worth(
    ctx: RequestContext = Depends(get_context),
    owner_id: uuid.UUID | None = Query(default=None),
):
    # Account-scoped to match the net-worth report: an owner filter here means the
    # accounts that owner holds, never a subset of rows inside an account.
    return NetWorthOut(**await ledger.net_worth(ctx.session, ctx.household_id, owner_id))


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    return _out(
        await ledger.get_account(ctx.session, account_id), await ledger.stale_since(ctx.session)
    )


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(account_id: uuid.UUID, data: AccountUpdate,
                         ctx: RequestContext = Depends(get_context)):
    acct = await ledger.update_account(ctx.session, account_id, data)
    return _out(acct, await ledger.stale_since(ctx.session))


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await ledger.delete_account(ctx.session, account_id)
