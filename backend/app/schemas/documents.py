"""Public file metadata, never embedded contents."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int
    created_at: datetime
    #: Whether ``…/content?preview=true`` shows it in place: ``pdf``, ``image`` or
    #: ``text``. ``None`` means the file is a download only.
    preview: str | None = None
