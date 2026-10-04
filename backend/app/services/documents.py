"""Bounded account uploads and safe display metadata (ADR-0060)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import AccountDocument
from app.models.ledger import Account
from app.services.errors import LedgerError

#: One file. Not a storage budget — there is none (ADR-0060) — but the size up to
#: which holding a whole file in memory, as an upload, a row and a response, stays
#: unremarkable. The column is ``bytea``, which is read and written in one piece.
MAX_FILE_BYTES = 100 * 1024 * 1024


def safe_filename(value: str | None) -> str:
    name = (value or "document").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    return name.strip()[:255] or "document"


def safe_media_type(value: str | None) -> str:
    value = (value or "").lower().split(";", 1)[0].strip()
    return (
        value
        if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", value) and len(value) <= 127
        else "application/octet-stream"
    )


async def require_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    if (
        await session.execute(select(Account.id).where(Account.id == account_id))
    ).scalar_one_or_none() is None:
        raise LedgerError("Account not found", 404)


async def create_document(
    session: AsyncSession,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    filename: str | None,
    media_type: str | None,
    content: bytes,
) -> AccountDocument:
    await require_account(session, account_id)
    if not content:
        raise LedgerError("Choose a file that is not empty", 400)
    if len(content) > MAX_FILE_BYTES:
        raise LedgerError(f"Files must be {MAX_FILE_BYTES // (1024 * 1024)} MB or smaller", 413)
    row = AccountDocument(
        household_id=household_id,
        account_id=account_id,
        filename=safe_filename(filename),
        media_type=safe_media_type(media_type),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )
    session.add(row)
    await session.flush()
    return row


#: What the app will show in place, and how it is served when it does. Everything
#: else is a download. The type is decided here, from the bytes where the format
#: has a signature, and never taken from what the uploader claimed alone: a file
#: served inline under a type the browser would run is the risk.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}
_TEXT_TYPES = frozenset({
    "text/plain", "text/csv", "text/markdown", "text/tab-separated-values", "application/json",
})
_TEXT_SUFFIXES = (".txt", ".csv", ".tsv", ".md", ".json", ".log", ".ofx", ".qfx", ".qif")


def preview_type(filename: str, media_type: str, head: bytes) -> str | None:
    """The type to serve ``inline``, or ``None`` when the file is only a download.

    PDFs and raster images are served as themselves when their first bytes agree
    with their declared type. Text is always served as ``text/plain``, whatever it
    claimed to be, so HTML or SVG uploaded as "text" is shown as its source.
    """
    signatures = _SIGNATURES.get(media_type)
    if signatures is not None:
        if not head.startswith(signatures):
            return None
        if media_type == "image/webp" and head[8:12] != b"WEBP":
            return None
        return media_type
    if media_type in _TEXT_TYPES or (
        media_type in ("application/octet-stream", "application/vnd.ms-excel")
        and filename.lower().endswith(_TEXT_SUFFIXES)
    ):
        return None if b"\x00" in head else "text/plain; charset=utf-8"
    return None
