"""Recurring series (ADR-0053) — what the Insights → Recurring tab reads and writes.

Open to any household member, like the ledger and like categories: a series is a
statement about the household's own transactions (ADR-0026's framing — owners are
attribution labels, not access boundaries), and confirming one is the same kind of
act as categorizing a transaction by hand.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, get_context
from app.schemas.recurring import (
    RecurringCreate,
    RecurringListOut,
    RecurringOut,
    RecurringSuggestionOut,
    RecurringUpdate,
)
from app.services import recurring as svc

router = APIRouter(prefix="/recurring", tags=["recurring"])


@router.get("", response_model=RecurringListOut)
async def list_recurring(
    ctx: RequestContext = Depends(get_context),
    account_id: uuid.UUID | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    direction: str | None = Query(default=None, pattern="^(in|out)$"),
    q: str | None = Query(default=None, max_length=120),
):
    """The household's series, filtered, each with what it currently matches.

    ``occurrences`` and ``last_seen_date`` come from the ledger on every read
    rather than from a stored counter (ADR-0035's "read once", ADR-0053): a
    series that stopped matching anything shows zero, which is the truth, and
    the one thing a person needs to see before they delete it.
    """
    return await svc.list_series(
        ctx.session,
        ctx.household_id,
        account_id=account_id,
        category_id=category_id,
        is_active=is_active,
        direction=direction,
        q=q,
    )


@router.get("/suggestions", response_model=list[RecurringSuggestionOut])
async def list_suggestions(ctx: RequestContext = Depends(get_context)):
    """What this household's own ledger already looks like it repeats.

    Recomputed on every read and never stored: a suggestion is a question, and
    it stops being asked the moment a series covers it — whether that series was
    accepted from here or entered by hand.
    """
    return await svc.suggestions(ctx.session)


@router.post("", response_model=RecurringOut, status_code=201)
async def create_recurring(data: RecurringCreate, ctx: RequestContext = Depends(get_context)):
    """Add a series — by hand, or from a transaction picked out of the ledger.

    When ``transaction_id`` is sent, whatever the body left blank (name,
    merchant, amount, account, category) is seeded from that transaction; an
    explicitly sent field always wins, so accepting a suggestion is posting it
    straight back.
    """
    series = await svc.create(ctx.session, ctx.household_id, data)
    return await svc.to_out(ctx.session, series)


@router.get("/{series_id}", response_model=RecurringOut)
async def get_recurring(series_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    return await svc.to_out(ctx.session, await svc.get(ctx.session, series_id))


@router.patch("/{series_id}", response_model=RecurringOut)
async def update_recurring(
    series_id: uuid.UUID, data: RecurringUpdate, ctx: RequestContext = Depends(get_context),
):
    """Change a series. Absent fields are unchanged; an explicit ``null`` clears
    one (``schemas/patch.py``) — which is how "stop filtering by merchant" and
    "pause this" are expressed."""
    return await svc.to_out(ctx.session, await svc.update(ctx.session, series_id, data))


@router.delete("/{series_id}", status_code=204)
async def delete_recurring(series_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    """Drop the series, not the transactions: it never owned them (ADR-0053),
    so the ledger is untouched and the charges stay categorized as they were."""
    await svc.delete(ctx.session, series_id)
