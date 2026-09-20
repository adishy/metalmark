"""Errors shared across the ledger services.

``LedgerError`` lives here rather than in ``services/ledger.py`` because the
account service needs the owner service and vice versa — with the error in one of
them, importing it from the other is a cycle, and the cycle is the signal that
neither module owns it.
"""

from __future__ import annotations


class LedgerError(Exception):
    """A ledger-domain failure that already knows its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
