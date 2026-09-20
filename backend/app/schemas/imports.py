"""Request/response models for CSV import (WS-IMP, M1a).

The vocabulary of mappable fields lives here rather than in the service, because
it is a contract with the client: the mapping UI offers exactly these names, and
the service validates the confirmed mapping against the same tuple.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

MappableField = Literal[
    "date", "amount", "debit", "credit", "description", "category", "owner", "notes"
]

MAPPABLE_FIELDS: tuple[MappableField, ...] = (
    "date", "amount", "debit", "credit", "description", "category", "owner", "notes",
)


class CsvPreviewOut(BaseModel):
    """What the mapping UI is built from — and nothing else (preview writes nothing)."""

    headers: list[str]
    # The first few data rows, verbatim, so the user can see what they are mapping.
    sample: list[list[str]]
    # header -> field, or null for "ignore this column". A suggestion, not a decision.
    suggested: dict[str, MappableField | None]


class CsvRowError(BaseModel):
    """One row the importer refused to guess at.

    ``line`` is the line number in the file as a text editor would show it: the
    header is line 1, so a data row's line is its file position, not its index in
    the parsed rows. That is the number a human needs to go and fix the cell.
    """

    line: int
    message: str


class CsvCommitOut(BaseModel):
    """The three counts are the contract; ``errors`` is the row-level extension.

    ``suspects`` is a subset of ``inserted`` — those rows landed, flagged
    ``needs_review`` as possible duplicates. ``inserted + skipped + len(errors)``
    equals the number of non-blank data rows in the file.
    """

    inserted: int
    skipped: int
    suspects: int
    errors: list[CsvRowError] = []
