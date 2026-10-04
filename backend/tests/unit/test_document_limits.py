"""The file quota and the import ceiling are one decision (ADR-0060).

Every stored file travels in the export as base64. If the household could keep
more than fits under ``MAX_IMPORT_BYTES`` it could make a backup it cannot
restore, and would find out at restore time.
"""

from app.services import documents, portability

#: The least room the ledger itself — everything that is not a file — must keep.
LEDGER_ROOM = 16 * 1024 * 1024


def _encoded(size: int) -> int:
    return (size + 2) // 3 * 4


def test_a_full_quota_of_files_still_fits_in_an_importable_export():
    assert documents.MAX_FILE_BYTES <= documents.MAX_HOUSEHOLD_BYTES
    assert _encoded(documents.MAX_HOUSEHOLD_BYTES) + LEDGER_ROOM <= portability.MAX_IMPORT_BYTES
