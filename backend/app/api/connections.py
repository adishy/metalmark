"""Bank connection routes, plus the control panel's sync surface.

Two audiences, one router, and the split is the Jellyfin one the project asked
for: **credential lifecycle** (paste a setup token, pause, retune, disconnect)
belongs to settings; **operations** (what ran, what is running, cancel it) belongs
to the dashboard. They share a resource — a connection — so they share a module,
and splitting them would mean two routers with two ideas of what a connection is.

**Owner-only, all of it.** Not just the writes: a connection names the household's
banks and its failure messages name them too, and a member has no reason to read
either. ``require_owner`` on every route including the GETs, which is stricter
than most of the ledger and deliberately so.

**Nothing here accepts or returns a credential except ``POST /claim``.** The
claim takes a single-use setup token in the body, and every response goes through
``ConnectionOut``, which has no field that could carry one (``schemas/connections.py``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.deps import RequestContext, require_owner
from app.schemas.connections import (
    ConnectionClaim,
    ConnectionDefaults,
    ConnectionOut,
    ConnectionUpdate,
    NotificationOut,
    SyncJobOut,
    SyncRunDetail,
    SyncRunEventOut,
    SyncRunOut,
)
from app.services import connections as svc
from app.services import notifications

router = APIRouter(prefix="/connections", tags=["connections"])


@router.get("", response_model=list[ConnectionOut])
async def list_connections(ctx: RequestContext = Depends(require_owner)):
    fresh = await svc.data_freshness(ctx.session)
    return [
        ConnectionOut.model_validate(c).model_copy(update=fresh.get(c.id, {}))
        for c in await svc.list_connections(ctx.session)
    ]


@router.get("/defaults", response_model=ConnectionDefaults)
async def connection_defaults(ctx: RequestContext = Depends(require_owner)):
    """The cadence bounds, from the columns' own constants.

    A route rather than a constant in the client: the floor and ceiling are
    enforced by a CHECK constraint, and a client that hard-codes them drifts into
    offering an interval the database will refuse.
    """
    return ConnectionDefaults()


@router.post("/claim", response_model=ConnectionOut, status_code=201)
async def claim_connection(
    data: ConnectionClaim, ctx: RequestContext = Depends(require_owner)
):
    """Exchange a setup token for a stored connection.

    The token is used once and never stored, echoed, or logged. On success the
    household has a connection; on failure it has nothing, and the message says
    why in the provider's own sanitized words.
    """
    connection = await svc.claim(
        ctx.session, ctx.household_id, setup_token=data.setup_token
    )
    return ConnectionOut.model_validate(connection)


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID, data: ConnectionUpdate,
    ctx: RequestContext = Depends(require_owner),
):
    connection = await svc.update_connection(ctx.session, connection_id, data)
    return ConnectionOut.model_validate(connection)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)
):
    """Disconnect. Accounts and transactions stay, and become manual."""
    await svc.delete_connection(ctx.session, connection_id)


@router.post("/{connection_id}/sync", response_model=SyncJobOut, status_code=202)
async def trigger_sync(connection_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)):
    """Queue a run. Returns the job, not the outcome — the work happens in the
    worker, and the panel watches ``GET /sync/runs`` for what it did.

    **202, not 200**: nothing has synced yet. A synchronous response would have
    the HTTP call block for the length of a bank fetch, and the whole queue
    exists so that it does not.
    """
    job = await svc.enqueue_sync(
        ctx.session, ctx.household_id, connection_id,
        trigger="manual", requested_by=ctx.user.id,
    )
    return SyncJobOut.model_validate(job)


# ---- operations -----------------------------------------------------------


@router.get("/jobs", response_model=list[SyncJobOut])
async def list_jobs(
    active_only: bool = Query(default=False),
    ctx: RequestContext = Depends(require_owner),
):
    """The queue. ``active_only`` is what the panel's live table asks for."""
    return [
        SyncJobOut.model_validate(j)
        for j in await svc.list_jobs(ctx.session, active_only=active_only)
    ]


@router.post("/jobs/{job_id}/cancel", response_model=SyncJobOut)
async def cancel_job(job_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)):
    """Cancel a queued or running job.

    Returns the job so the panel can render its terminal state directly. A job
    that already finished is a 409: "cancelled" and "had already finished" are
    different facts and the operator is owed the second one when it is true.
    """
    return SyncJobOut.model_validate(await svc.cancel_job(ctx.session, job_id))


@router.get("/runs", response_model=list[SyncRunOut])
async def list_runs(
    connection_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=svc.DEFAULT_RUN_LIMIT, ge=1, le=svc.MAX_RUN_LIMIT),
    ctx: RequestContext = Depends(require_owner),
):
    return [
        SyncRunOut.model_validate(r)
        for r in await svc.list_runs(ctx.session, connection_id=connection_id, limit=limit)
    ]


@router.get("/runs/{run_id}", response_model=SyncRunDetail)
async def get_run(run_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)):
    """One run with its log — the panel's expanded row, in one request."""
    run = await svc.get_run(ctx.session, run_id)
    events = await svc.list_run_events(ctx.session, run_id)
    return SyncRunDetail(
        run=SyncRunOut.model_validate(run),
        events=[SyncRunEventOut.model_validate(e) for e in events],
    )


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    since: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(require_owner),
):
    """Notices newer than the browser's cursor — polled, never pushed (ADR-0037).

    The app asks for this on a timer while a tab is open, because the decision to
    notify is ``should_notify``'s, in the worker, and a browser left to re-derive
    it would drift towards notifying too often. ``since`` is the id of the last
    notice this browser showed, kept in its ``localStorage``; how it is turned
    into an ordering is ``notifications.list_notices``' business.

    Owner-only like everything else here. A notice names an institution and what is
    wrong with it, and that is the household's business and not a member's.
    """
    return [
        NotificationOut.model_validate(n)
        for n in await notifications.list_notices(ctx.session, since=since)
    ]
