"""Rules engine routes.

Mounted (in ``app.main``) ahead of the vertical that fills it in, so the route
seam is fixed before parallel work starts and no two workstreams have to edit
``main.py``. Shapes are frozen by PLAN.md Phase 2: ``GET/POST /rules``,
``PATCH/DELETE /rules/{rule_id}``, ``POST /rules/apply``.

**Which routes are owner-only.** Reading the rule list is an ordinary
household-scoped read (``get_context``), exactly like ``GET /owners``. Writing is
not: a rule rewrites ledger history — every future ingest through the on-create
hook, and every existing row through "apply to existing" — so creating, editing,
deleting or applying one is a household-shape decision, the same reasoning that
makes owner deletion owner-only in ``api/owners.py``. A member can still add a
rule of their own later if the household wants that; nothing in the model blocks
it, and relaxing this is a one-line change.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context, require_owner
from app.schemas.rules import RuleApplyResult, RuleCreate, RuleOut, RuleUpdate
from app.services import rules as svc

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
async def list_rules(ctx: RequestContext = Depends(get_context)):
    return [RuleOut.model_validate(r) for r in await svc.list_rules(ctx.session)]


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(data: RuleCreate, ctx: RequestContext = Depends(require_owner)):
    rule = await svc.create_rule(ctx.session, ctx.household_id, data)
    return RuleOut.model_validate(rule)


@router.patch("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: uuid.UUID, data: RuleUpdate,
                      ctx: RequestContext = Depends(require_owner)):
    return RuleOut.model_validate(await svc.update_rule(ctx.session, rule_id, data))


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, ctx: RequestContext = Depends(require_owner)):
    await svc.delete_rule(ctx.session, rule_id)


@router.post("/apply", response_model=RuleApplyResult)
async def apply_rules(ctx: RequestContext = Depends(require_owner)) -> RuleApplyResult:
    """Run the enabled rules over every existing transaction.

    Idempotent: running it twice reports the same ``matched`` and ``updated == 0``
    the second time (PLAN.md's R acceptance bar). ``matched`` and ``updated``
    count transactions, not (transaction, rule) pairs, so they are comparable —
    the gap between them is the fields a human had already set, which the engine
    will not overwrite.
    """
    return await svc.apply(ctx.session, ctx.household_id)
