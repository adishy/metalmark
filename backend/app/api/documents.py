"""Account attachment lifecycle (ADR-0060). Agents reach files only through the
separate ``documents:read`` scope (``app/api/agent.py``), never the anonymized mirror."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.deps import RequestContext, get_context
from app.models.documents import AccountDocument
from app.schemas.documents import AccountDocumentOut
from app.services import documents as svc
from app.services.errors import LedgerError

router = APIRouter(prefix="/accounts/{account_id}/documents", tags=["account documents"])


def document_out(row: AccountDocument, head: bytes) -> AccountDocumentOut:
    kind = svc.preview_type(row.filename, row.media_type, head)
    out = AccountDocumentOut.model_validate(row)
    if kind is not None:
        out.preview = (
            "pdf" if kind == "application/pdf" else "image" if kind.startswith("image/") else "text"
        )
    return out


async def list_account_documents(
    session: AsyncSession, account_id: uuid.UUID, *, with_digest: bool = False
):
    await svc.require_account(session, account_id)
    rows = await session.execute(
        # The first bytes only: enough to tell what the file is without loading it.
        select(AccountDocument, func.substring(AccountDocument.content, 1, 16))
        .where(AccountDocument.account_id == account_id)
        .order_by(AccountDocument.created_at.desc(), AccountDocument.id)
    )
    rows = rows.all()
    if with_digest:
        return [{**document_out(row, bytes(head)).model_dump(), "sha256": row.sha256}
                for row, head in rows]
    return [document_out(row, bytes(head)) for row, head in rows]


@router.get("", response_model=list[AccountDocumentOut])
async def list_documents(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    return await list_account_documents(ctx.session, account_id)


@router.post("", response_model=AccountDocumentOut, status_code=201)
async def upload_document(
    account_id: uuid.UUID, file: UploadFile = File(...), ctx: RequestContext = Depends(get_context)
):
    content = await file.read(svc.MAX_FILE_BYTES + 1)
    row = await svc.create_document(
        ctx.session, ctx.household_id, account_id, file.filename, file.content_type, content
    )
    return document_out(row, content[:16])


async def get_document(
    session: AsyncSession, account_id: uuid.UUID, document_id: uuid.UUID, content: bool = False
) -> AccountDocument:
    query = select(AccountDocument).where(
        AccountDocument.account_id == account_id, AccountDocument.id == document_id
    )
    if content:
        query = query.options(undefer(AccountDocument.content))
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise LedgerError("Document not found", 404)
    return row


@router.get("/{document_id}/content")
async def download_document(
    account_id: uuid.UUID,
    document_id: uuid.UUID,
    preview: bool = False,
    ctx: RequestContext = Depends(get_context),
) -> Response:
    row = await get_document(ctx.session, account_id, document_id, content=True)
    return file_response(row, preview=preview)


def file_response(row: AccountDocument, *, preview: bool = False) -> Response:
    """The stored bytes, as a download or — for a type the app shows — in place."""
    inline = svc.preview_type(row.filename, row.media_type, row.content[:16]) if preview else None
    filename = quote(row.filename, safe="")
    if inline is None:
        csp = "sandbox; default-src 'none'"
    elif inline == "application/pdf":
        # No ``sandbox`` here: a browser's built-in PDF viewer does not load inside
        # a sandboxed document, so the one response meant to be looked at would
        # render blank. It is only ever ``application/pdf`` with a checked
        # signature and ``nosniff``, which is what keeps it from being read as
        # HTML. The app's own viewer shows a copy it fetched, so nothing needs to
        # frame this response.
        csp = "default-src 'none'; object-src 'none'; frame-ancestors 'none'"
    else:
        # An image or plain text: nothing in it runs, and the sandbox makes sure.
        csp = "sandbox; default-src 'none'; frame-ancestors 'none'"
    return Response(
        content=row.content,
        media_type=inline or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"{'inline' if inline else 'attachment'}; filename=\"document\"; "
                f"filename*=UTF-8''{filename}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": csp,
        },
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    account_id: uuid.UUID, document_id: uuid.UUID, ctx: RequestContext = Depends(get_context)
) -> Response:
    await ctx.session.delete(await get_document(ctx.session, account_id, document_id))
    return Response(status_code=204)
