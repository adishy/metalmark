"""The import response (ADR-0036).

Only the *import* side has a schema. The export's shape is the format itself, and
it is written out in ``docs/adr/0036-portable-export.md`` and in
``services.portability``'s column lists rather than declared twice — a second
declaration is a second thing to keep in step with the reader, and the reader is
the one that decides.
"""

from __future__ import annotations

from pydantic import BaseModel


class ImportOut(BaseModel):
    """Per-entity counts, so the UI can say what actually happened.

    ``created`` and ``matched`` are keyed by entity name rather than being fixed
    fields, for the same reason the export's sections are a list of named blocks:
    a new entity is then one entry the UI can already render, not a schema change
    and a client release. The UI lists what is non-zero and ignores the rest.

    ``warnings`` is where a reference that could not be resolved is recorded. It
    is deliberately not an error: a rule pointing at a category the user deleted
    last month is a real, importable document, and refusing the whole file over it
    would make an export from a live household un-importable.
    """

    created: dict[str, int] = {}
    matched: dict[str, int] = {}
    warnings: list[str] = []
