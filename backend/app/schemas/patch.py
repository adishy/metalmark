"""Helpers for PATCH semantics: absent ≠ null.

Pydantic records which fields the client actually sent in ``model_fields_set``.
That distinction is the whole point of a PATCH body here — an omitted ``owner_id``
means "leave it alone", an explicit ``null`` means "clear it" (inherit) — and
``is not None`` cannot tell them apart, so every update path that cares must ask
this instead.
"""

from __future__ import annotations

from pydantic import BaseModel


def is_set(model: BaseModel, field: str) -> bool:
    """True when the client sent ``field``, whatever its value — including null."""
    return field in model.model_fields_set
