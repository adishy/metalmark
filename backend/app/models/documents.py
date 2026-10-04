"""Account files live with their metadata in the RLS-protected database (ADR-0060)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class AccountDocument(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "account_documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520", name="document_size"),
        CheckConstraint("octet_length(content) = size_bytes", name="document_content_size"),
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("households.id", ondelete="CASCADE"),
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
