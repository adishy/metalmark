"""An institution's logo, as the household stored it (session 06, item I).

Kept in the database rather than on disk so a backup carries it and a restore
brings it back — a logo is a small image the household chose, and the instance
is the one place it lives. Keyed by the institution's name as the ledger spells
it, normalised, so every account at "Chase" shares one row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class InstitutionLogo(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "institution_logos"
    __table_args__ = (UniqueConstraint("household_id", "key"),)

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    #: ``normalise(name)``: lower-case letters and digits, single spaces.
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The name as it was first seen, for display.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: ``fetched`` (from the institution's own site) or ``uploaded`` (by a person).
    source: Mapped[str] = mapped_column(String(10), nullable=False)
