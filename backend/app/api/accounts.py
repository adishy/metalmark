from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.ledger import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    NetWorthOut,
)
from app.services import ledger

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(data: AccountCreate, ctx: RequestContext = Depends(get_context)):
    acct = await ledger.create_account(ctx.session, ctx.household_id, data)
    return AccountOut.model_validate(acct)


@router.get("", response_model=list[AccountOut])
async def list_accounts(ctx: RequestContext = Depends(get_context)):
    return [AccountOut.model_validate(a) for a in await ledger.list_accounts(ctx.session)]


@router.get("/net-worth", response_model=NetWorthOut)
async def net_worth(ctx: RequestContext = Depends(get_context)):
    return NetWorthOut(**await ledger.net_worth(ctx.session, ctx.household_id))


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    return AccountOut.model_validate(await ledger.get_account(ctx.session, account_id))


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(account_id: uuid.UUID, data: AccountUpdate,
                         ctx: RequestContext = Depends(get_context)):
    return AccountOut.model_validate(
        await ledger.update_account(ctx.session, account_id, data)
    )


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await ledger.delete_account(ctx.session, account_id)
