"""CSV import routes.

Mounted (in ``app.main``) ahead of the vertical that fills it in, so the route
seam is fixed before parallel work starts and no two workstreams have to edit
``main.py``. Shapes are frozen by PLAN.md Phase 2: ``POST /import/csv/preview``
and ``POST /import/csv/commit``.

Both take the file as multipart. Commit takes it *again* rather than an upload
id: the parse is cheap and stateless, so there is nothing to store between the
two calls, and a stale id would be one more thing that can disagree with the
bytes the user is looking at. The mapping rides as a JSON string because its keys
are the CSV's own header names, which cannot be form field names.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.deps import RequestContext, get_context
from app.schemas.imports import CsvCommitOut, CsvPreviewOut, CsvRowError
from app.services import imports as svc
from app.services.errors import LedgerError

router = APIRouter(prefix="/import", tags=["import"])

# The OFX/QFX sibling lives here when Phase 2 grows it (ADR: defused XML).


async def _read(file: UploadFile) -> bytes:
    """Read the upload, bounded by the size limit before it is all in memory
    (ARCHITECTURE §5: user-supplied files are size- and column-limited).

    The limit itself is enforced where the bytes are parsed, so there is one
    message and one place it can come from.
    """
    return await file.read(svc.MAX_FILE_BYTES + 1)


def _parse_mapping(raw: str) -> dict[str, str | None]:
    """Decode the mapping form field into ``header → field | null``.

    A malformed value is a 400 with a readable message rather than a 422 from a
    JSON parser the client cannot see.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"mapping is not valid JSON: {exc.msg}", 400) from exc
    if not isinstance(parsed, dict):
        raise LedgerError("mapping must be a JSON object of column name → field", 400)
    out: dict[str, str | None] = {}
    for key, value in parsed.items():
        if value is not None and not isinstance(value, str):
            raise LedgerError(f"mapping[{key!r}] must be a field name or null", 400)
        out[str(key)] = value
    return out


@router.post("/csv/preview", response_model=CsvPreviewOut)
async def preview_csv(
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(get_context),
):
    """Headers, a sample of rows, and a suggested mapping. Writes nothing.

    A file that cannot be imported at all — too big, too many rows, or with no
    column that could carry a date and an amount — is rejected here, naming what
    is missing, instead of leading the user into a mapping UI that goes nowhere.
    """
    result = svc.preview_csv(await _read(file))
    return CsvPreviewOut(
        headers=result.headers, sample=result.sample, suggested=result.suggested
    )


@router.post("/csv/commit", response_model=CsvCommitOut)
async def commit_csv(
    file: UploadFile = File(...),
    account_id: uuid.UUID = Form(...),
    mapping: str = Form(..., description="JSON object: column name → field, or null"),
    default_category_id: uuid.UUID | None = Form(default=None),
    dayfirst: bool = Form(default=False),
    ctx: RequestContext = Depends(get_context),
):
    """Import the file into one account. One transaction: the file lands whole."""
    result = await svc.commit_csv(
        ctx.session,
        ctx.household_id,
        raw=await _read(file),
        mapping=_parse_mapping(mapping),
        account_id=account_id,
        default_category_id=default_category_id,
        dayfirst=dayfirst,
    )
    return CsvCommitOut(
        inserted=result.inserted,
        skipped=result.skipped,
        suspects=result.suspects,
        errors=[CsvRowError(line=e.line, message=e.message) for e in result.errors],
    )
