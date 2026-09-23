from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import RequestContext, get_context
from app.schemas.checks import ChecksOut
from app.services import checks

router = APIRouter(prefix="/checks", tags=["checks"])


@router.get("", response_model=ChecksOut)
async def run_checks(ctx: RequestContext = Depends(get_context)):
    """The data checks, run now for this household — the same ones the worker runs
    on every start (ADR-0047), with the accounts behind each finding."""
    return ChecksOut(
        schema_version=await checks.schema_version(ctx.session),
        checks=checks.as_dicts(await checks.run(ctx.session, ctx.household_id)),
    )
