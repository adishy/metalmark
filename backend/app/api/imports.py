"""Import routes: CSV (WS-IMP, M1a) and OFX/QFX (ADR-0030).

Mounted (in ``app.main``) ahead of the vertical that fills it in, so the route
seam is fixed before parallel work starts and no two workstreams have to edit
``main.py``. Shapes are frozen by PLAN.md Phase 2: ``POST /import/csv/preview``
and ``POST /import/csv/commit``, and by ADR-0030: ``POST /import/ofx/preview``
and ``POST /import/ofx/commit``.

Both formats take the file as multipart, and both commits take it *again* rather
than an upload id: the parse is cheap and stateless, so there is nothing to store
between the two calls, and a stale id would be one more thing that can disagree
with the bytes the user is looking at. The CSV mapping rides as a JSON string
because its keys are the CSV's own header names, which cannot be form field
names. The OFX pair carries no mapping at all — the file says what its own fields
are, and the only decision left is which account it lands in.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.deps import RequestContext, get_context
from app.schemas.imports import (
    CsvCommitOut,
    CsvPreviewOut,
    CsvRowError,
    OfxCommitOut,
    OfxPreviewOut,
    OfxRowError,
)
from app.services import imports as svc
from app.services.errors import LedgerError

router = APIRouter(prefix="/import", tags=["import"])


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


@router.post("/ofx/preview", response_model=OfxPreviewOut)
async def preview_ofx(
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(get_context),
):
    """What the OFX/QFX file says about itself. Writes nothing.

    An OFX 1.x file is refused here, by name and with a way out, rather than
    parsed on a guess about where its unquoted SGML values end (ADR-0030 §1). So
    is XML that is malformed or that declares a DTD, and a file with no statement
    in it — every reason the commit would fail is a reason it fails *before* the
    user has chosen an account.
    """
    result = svc.preview_ofx(await _read(file))
    return OfxPreviewOut(
        org=result.org,
        acct_id=result.acct_id,
        acct_type=result.acct_type,
        currency=result.currency,
        start=result.start,
        end=result.end,
        transaction_count=result.transaction_count,
        investment_count=result.investment_count,
    )


@router.post("/ofx/commit", response_model=OfxCommitOut)
async def commit_ofx(
    file: UploadFile = File(...),
    account_id: uuid.UUID = Form(...),
    default_category_id: uuid.UUID | None = Form(default=None),
    ctx: RequestContext = Depends(get_context),
):
    """Import the file's banking rows into one account. One transaction.

    ``account_id`` is explicit and required, because the file's own ``<ACCTID>``
    is reported by the preview and never obeyed (ADR-0030 §4): a ledger account
    belongs to the household, and a file cannot be trusted to name one.
    """
    result = await svc.commit_ofx(
        ctx.session,
        ctx.household_id,
        raw=await _read(file),
        account_id=account_id,
        default_category_id=default_category_id,
    )
    return OfxCommitOut(
        inserted=result.inserted,
        skipped=result.skipped,
        suspects=result.suspects,
        investments_skipped=result.investments_skipped,
        errors=[OfxRowError(position=e.position, message=e.message) for e in result.errors],
    )
