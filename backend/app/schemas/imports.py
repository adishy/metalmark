"""Request/response models for import (WS-IMP, M1a; OFX per ADR-0030).

The vocabulary of mappable fields lives here rather than in the service, because
it is a contract with the client: the mapping UI offers exactly these names, and
the service validates the confirmed mapping against the same tuple.
"""

from __future__ import annotations

from datetime import datetime
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


class OfxPreviewOut(BaseModel):
    """What the file says about itself before anything is written (ADR-0030 §4).

    The account fields are *reported* and not obeyed: the human picks the ledger
    account at commit, exactly as the CSV path does. They exist so the dialog can
    show "this is the statement for 000111222333" — confirmation that the right
    file was downloaded — rather than so anything can be matched on. ``acct_id``
    is a string because an account number is an identifier, never an amount: it
    may carry leading zeros, and it is never arithmetic.
    """

    org: str | None = None
    acct_id: str | None = None
    acct_type: str | None = None
    currency: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    #: Banking rows this file will offer. Investment rows are counted separately
    #: and left out (§5), so a file can honestly preview as zero of the former.
    transaction_count: int
    investment_count: int


class OfxRowError(BaseModel):
    """One ``<STMTTRN>`` the importer refused to guess at.

    ``position`` is 1-based within the statement, not a file line: OFX has no
    line numbers to point at, and "the third transaction in the file" is what a
    human can go and find.
    """

    position: int
    message: str


class OfxCommitOut(BaseModel):
    """The same three counts as the CSV path, plus what could not come in.

    ``investments_skipped`` is its own number rather than folded into ``skipped``
    (ADR-0030 §5): a skipped row is one the ledger already has, and these are rows
    it cannot hold yet, so an all-investment file reads as "0 imported, 5 skipped"
    instead of as a silent success.
    """

    inserted: int
    skipped: int
    suspects: int
    investments_skipped: int
    errors: list[OfxRowError] = []
