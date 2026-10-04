"""Account attachment lifecycle; raw files are never exposed to agent tokens."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.deps import RequestContext, get_context
from app.models.documents import AccountDocument
from app.schemas.documents import AccountDocumentOut
from app.services import documents as svc
from app.services.errors import LedgerError

router = APIRouter(prefix="/accounts/{account_id}/documents", tags=["account documents"])


@router.get("", response_model=list[AccountDocumentOut])
async def list_documents(account_id: uuid.UUID, ctx: RequestContext = Depends(get_context)):
    await svc.require_account(ctx.session, account_id)
    return (
        (
            await ctx.session.execute(
                select(AccountDocument)
                .where(
                    AccountDocument.account_id == account_id,
                )
                .order_by(AccountDocument.created_at.desc(), AccountDocument.id)
            )
        )
        .scalars()
        .all()
    )


@router.post("", response_model=AccountDocumentOut, status_code=201)
async def upload_document(
    account_id: uuid.UUID, file: UploadFile = File(...), ctx: RequestContext = Depends(get_context)
):
    content = await file.read(svc.MAX_FILE_BYTES + 1)
    return await svc.create_document(
        ctx.session, ctx.household_id, account_id, file.filename, file.content_type, content
    )


async def _document(
    ctx: RequestContext, account_id: uuid.UUID, document_id: uuid.UUID, content: bool = False
) -> AccountDocument:
    query = select(AccountDocument).where(
        AccountDocument.account_id == account_id, AccountDocument.id == document_id
    )
    if content:
        query = query.options(undefer(AccountDocument.content))
    row = (await ctx.session.execute(query)).scalar_one_or_none()
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
    row = await _document(ctx, account_id, document_id, content=True)
    pdf = row.media_type == "application/pdf" and row.content.startswith(b"%PDF-")
    disposition = "inline" if preview and pdf else "attachment"
    filename = quote(row.filename, safe="")
    inline = preview and pdf
    return Response(
        content=row.content,
        media_type="application/pdf" if inline else "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"document\"; filename*=UTF-8''{filename}"
            ),
            "X-Content-Type-Options": "nosniff",
            # ``sandbox`` everywhere but the PDF preview: a browser's built-in PDF
            # viewer does not load inside a sandboxed document, so the one response
            # meant to be looked at would render blank. That response is only ever
            # ``application/pdf`` with a checked signature and ``nosniff``, which is
            # what keeps it from being read as HTML; everything else is an
            # attachment and keeps the sandbox.
            "Content-Security-Policy": (
                "default-src 'none'; object-src 'none'; frame-ancestors 'none'"
                if inline
                else "sandbox; default-src 'none'"
            ),
        },
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    account_id: uuid.UUID, document_id: uuid.UUID, ctx: RequestContext = Depends(get_context)
) -> Response:
    await ctx.session.delete(await _document(ctx, account_id, document_id))
    return Response(status_code=204)
