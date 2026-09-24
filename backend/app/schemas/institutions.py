from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class InstitutionOut(BaseModel):
    name: str
    key: str
    #: Whether this server knows the institution's website, so "Fetch logos"
    #: can get its icon. The domain itself is not sent: it names the bank.
    fetchable: bool
    has_logo: bool
    logo_source: str | None
    logo_updated_at: datetime | None


class LogoUpload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    #: The image, base64, optionally as a ``data:`` URL.
    data_base64: str = Field(min_length=1, max_length=800_000)


class FetchLogosResult(BaseModel):
    fetched: list[str]
    failed: list[str]
    unknown: list[str]
    kept: int
