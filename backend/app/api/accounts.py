from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.deps import RequestContext, get_context
from app.schemas.ledger import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BalanceIn,
    BalanceOut,
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


@router.get("/{account_id}/balances", response_model=list[BalanceOut])
async def list_balances(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    """The account's balance history, newest first."""
    return [
        BalanceOut.model_validate(b)
        for b in await ledger.list_balances(ctx.session, account_id)
    ]


@router.put("/{account_id}/balances/{on}", response_model=BalanceOut)
async def put_balance(account_id: uuid.UUID, on: date, data: BalanceIn,
                      ctx: RequestContext = Depends(get_context)):
    """Record or correct the balance on one day — history the provider never sent."""
    snap = await ledger.put_balance(ctx.session, account_id, on=on, balance=data.balance)
    return BalanceOut.model_validate(snap)


@router.delete("/{account_id}/balances/{on}", status_code=204)
async def delete_balance(account_id: uuid.UUID, on: date,
                         ctx: RequestContext = Depends(get_context)):
    await ledger.delete_balance(ctx.session, account_id, on)
    # An empty 204 with no content type: nothing here claims to be JSON.
    return Response(status_code=204)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await ledger.delete_account(ctx.session, account_id)
