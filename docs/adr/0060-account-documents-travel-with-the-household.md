# ADR 0060: Account documents travel with the household

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** ADR-0014/0025 (RLS), ADR-0036 (portable export), ADR-0048 (agent privacy)

## Context

Keep statements, PDFs, office documents and arbitrary files beside an account, and move those files
with the household without a separate filesystem restore.

## Decision

Store immutable uploaded bytes and metadata in `account_documents`, with household RLS and account
and household foreign keys. Account deletion cascades. Uploads must be nonempty and at most 20 MiB,
and a household's files may total at most 32 MiB. Strip filename path components and control
characters.

Authenticated members can list, upload, download and delete files. The UI confirms deletion. Downloads
have `nosniff` and a sandbox CSP. Only an explicitly requested, signature-checked PDF may open inline;
that one response drops `sandbox` (a browser's built-in PDF viewer does not load in a sandboxed
document) and keeps `default-src 'none'`, the fixed `application/pdf` type and `nosniff`.
Office files download for viewing in the user's chosen application. The server never executes,
extracts or parses uploaded documents.

Portable exports include base64 bytes, SHA-256, size, timestamps and account references. Bump the
format to version 3 so older readers refuse rather than silently dropping files; retain version 1/2
readers. Imports remap accounts, allocate fresh IDs and validate digest and size. A file matches by
account, sanitized filename and digest, with occurrence ordinals preserving duplicate uploads,
leaving existing content untouched.

**The household total exists so that every export can be imported.** ADR-0036's 64 MiB import ceiling
stays, and it is checked before the document is parsed. Files travel as base64, 4/3 their size, so
without a total three 20 MiB files would make every later export unrestorable, discovered only at
restore time. 32 MiB of files is about 43 MiB encoded, leaving about 21 MiB for the ledger. The total
is enforced where a file is stored, so uploads and imports are both held to it, under a per-household
lock so concurrent uploads cannot overshoot; a unit test holds the quota and the ceiling to each other.
Raising the total means raising the import ceiling and the proxy's body limit with it, and accepting
the memory an export of that size takes: a decision for a later ADR, not a constant to edit. All attachment endpoints, including metadata, are excluded from the agent mirror:
filenames and arbitrary file contents cannot be reliably anonymized.

## Consequences

Database backups and household exports carry files without external storage dependencies. Database
size and export memory grow with attachments, bounded by the household total: an export holds every
file in memory at once, several times over while it is encoded. A household that needs more than
32 MiB of files cannot keep them here yet. Downgrading the additive table
migration explicitly discards attachments while preserving the ledger.
