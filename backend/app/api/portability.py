"""Export and import routes (ADR-0036).

Three endpoints, and the split between them is the design:

* ``GET /export`` — the whole household as one versioned JSON document.
* ``GET /export/archive`` — that document and the account files it lists, as a zip.
* ``GET /export/transactions.csv`` — one account's rows as a spreadsheet.
* ``POST /import`` — load a document, or an archive, back in.

Export is available to any member, because reading the household's ledger is what
being a member already is; import is owner-only, because it can change the
household's base currency and merge a second history into the household's own.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from python_multipart.exceptions import MultipartParseError
from sqlalchemy import select
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.deps import RequestContext, get_context, require_owner
from app.models import Account, Category, Owner, Transaction
from app.schemas.portability import ImportOut
from app.services import portability as svc
from app.services.errors import LedgerError
from app.settings import get_settings

router = APIRouter(tags=["portability"])


def _attachment(kind: str, extension: str) -> str:
    """A dated filename, so two exports of one household do not silently
    overwrite each other in a Downloads folder — and so a re-import dialog
    suggests the name the file already has."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return f'attachment; filename="metalmark-{kind}-{stamp}.{extension}"'


@router.get("/export")
async def export_household(ctx: RequestContext = Depends(get_context)) -> Response:
    """The household as one ``metalmark.export`` document.

    Served as a download rather than as a JSON body so a browser saves it instead
    of rendering several megabytes of ledger into a tab.
    """
    body = svc.dumps(await svc.export_document(ctx.session, ctx.household_id))
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": _attachment("export", "json")},
    )


@router.get("/export/archive")
async def export_archive(ctx: RequestContext = Depends(get_context)) -> StreamingResponse:
    """The document and every account file it lists, as one zip (ADR-0060).

    Built into a temporary file first and streamed from it, so neither the build
    nor the response holds the household's files in memory, and the database is
    done with before the first byte is sent.
    """
    spool = tempfile.TemporaryFile()  # noqa: SIM115 - closed by the response below
    try:
        await svc.write_archive(ctx.session, ctx.household_id, spool)
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise

    def chunks():
        try:
            while block := spool.read(1024 * 1024):
                yield block
        finally:
            spool.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _attachment("export", "zip"),
            "Content-Length": str(size),
        },
    )


@router.get("/export/transactions.csv")
async def export_transactions_csv(
    account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)
) -> Response:
    """One account's transactions as a CSV the importer can read back.

    ``account_id`` is required, not a filter. The CSV importer lands every row of
    a file into **one** account, so a file holding several accounts' rows would
    re-import them all into whichever account the user picked — a file that says
    one thing and does another. Exporting one account at a time is the only shape
    in which the round trip means what it looks like.

    The whole household is in ``GET /export``. This one is the spreadsheet.
    """
    account = (
        await ctx.session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        # Named as the account path rather than the account, because the id is
        # what the caller supplied and another household's id is exactly what
        # this looks like from here.
        raise LedgerError("Account not found", 404)

    rows = list((
        await ctx.session.execute(
            select(Transaction)
            .where(Transaction.account_id == account_id)
            .order_by(Transaction.transacted_at, Transaction.id)
        )
    ).scalars().all())
    owners = dict((await ctx.session.execute(select(Owner.id, Owner.name))).all())
    categories = dict((await ctx.session.execute(select(Category.id, Category.name))).all())
    return Response(
        content=svc.transactions_csv(rows, owners, categories),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _attachment("transactions", "csv")},
    )


def _bounded(request: Request, limit: int) -> Request:
    """The same request, refusing a body longer than ``limit`` as it arrives.

    Counted on the wire rather than trusted from ``Content-Length``, which a
    chunked upload does not send and a hostile one can misstate.
    """
    received = 0
    too_large = LedgerError(
        f"This upload is larger than {limit // (1024 * 1024)} MB, the most an import can be.",
        413,
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large

    async def receive():
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise too_large
        return message

    return Request(request.scope, receive)


@router.post(
    "/import",
    response_model=ImportOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "The export's JSON document or its .zip archive.",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def import_household(
    request: Request, ctx: RequestContext = Depends(require_owner)
) -> ImportOut:
    """Load an exported document, or the archive holding it, into this household.

    The JSON document on its own imports the ledger and leaves the account files
    out, with a warning saying how many. The archive restores those too.

    Idempotent against natural keys: importing the same document twice creates
    nothing the second time. See ``services.portability`` for what "the same" means
    per entity — and for why a credential cannot be in the document at all, so an
    imported connection lands ``auth_error`` and needs reconnecting.

    The body is not a declared parameter, on purpose. FastAPI reads a declared
    upload to disk *before* it resolves dependencies, so an unauthenticated
    request could spool as much as the proxy lets through. Here the owner and
    CSRF checks have already passed when the first byte is read, and the body is
    counted against ``max_import_upload_bytes`` as it arrives.
    """
    bounded = _bounded(request, get_settings().max_import_upload_bytes)
    try:
        form = await bounded.form(max_files=1, max_fields=1)
    except (MultiPartException, MultipartParseError) as exc:
        raise LedgerError(f"Not a readable upload: {exc}", 400) from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise LedgerError('Send the export as a multipart upload in a field named "file".', 400)
        files = None
        if await file.read(4) == b"PK\x03\x04":
            # An archive: the document, with the files it lists beside it. Opened
            # where the upload was spooled, so the files are read one at a time.
            raw, files = await run_in_threadpool(svc.open_archive, file.file)
        else:
            await file.seek(0)
            raw = await file.read(svc.MAX_IMPORT_BYTES + 1)
            if len(raw) > svc.MAX_IMPORT_BYTES:
                raise LedgerError(
                    f"File is larger than {svc.MAX_IMPORT_BYTES // (1024 * 1024)} MB, the "
                    f"most a household document can be. Check that you picked the export's "
                    f"JSON document or its .zip archive and not something else.",
                    400,
                )
        result = await svc.import_document(ctx.session, ctx.household_id, raw, files)
    finally:
        await form.close()
    return ImportOut(**result.as_dict())
