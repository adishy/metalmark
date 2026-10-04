"""Bounded account uploads and safe display metadata (ADR-0060)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import AccountDocument
from app.models.ledger import Account
from app.services.errors import LedgerError

MAX_FILE_BYTES = 20 * 1024 * 1024
#: Every file the household keeps, together. The export carries the bytes as
#: base64 (4/3 the size) inside one JSON document, and the import refuses a
#: document over ``portability.MAX_IMPORT_BYTES`` (64 MiB). Holding the total here
#: is what keeps "an export this household made can be imported again" true:
#: 32 MiB of files is ~43 MiB encoded, which leaves ~21 MiB for the ledger itself.
#: ``tests/unit/test_document_limits.py`` holds the two numbers to each other.
MAX_HOUSEHOLD_BYTES = 32 * 1024 * 1024


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


def _mb(size: int, places: int = 0) -> str:
    return f"{size / (1024 * 1024):.{places}f}"


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
        raise LedgerError("Files must be 20 MB or smaller", 413)
    # One upload at a time per household, so two concurrent requests cannot each
    # see room for themselves and together overshoot the total.
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"documents:{household_id}", 0)))
    )
    used = (
        await session.execute(select(func.coalesce(func.sum(AccountDocument.size_bytes), 0)))
    ).scalar_one()
    if used + len(content) > MAX_HOUSEHOLD_BYTES:
        raise LedgerError(
            f"This would take the household's files past {_mb(MAX_HOUSEHOLD_BYTES)} MB in "
            f"total ({_mb(used, 1)} MB is in use). Delete a file you no longer need first.",
            413,
        )
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
