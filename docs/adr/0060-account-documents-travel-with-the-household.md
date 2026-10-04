# ADR 0060: Account documents travel with the household

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** ADR-0014/0025 (RLS), ADR-0036 (portable export), ADR-0048 (agent privacy), ADR-0062

## Context

Keep statements, PDFs, office documents and arbitrary files beside an account, look at them in the
app, and move them with the household without a separate filesystem restore.

Carrying the bytes inside the JSON export, as base64, tied how much a household could keep to the
size of document an import will parse: the first build had to cap a household at 32 MiB of files to
keep its own exports importable. A personal-finance app has no reason to ration statements.

## Decision

Store immutable uploaded bytes and metadata in `account_documents`, with household RLS and account
and household foreign keys. Account deletion cascades. Strip filename path components and control
characters.

**There is no storage budget.** A household keeps as many files as it likes. The only bound is on one
file, 100 MiB, and it is a performance bound: a file is a `bytea` value, read and written in one piece,
so its size is the memory one upload, one row and one response take. The limit lives in the
application (`documents.MAX_FILE_BYTES`), not in a database constraint, so changing it is not a
migration.

Authenticated members can list, upload, view, download and delete files. The UI confirms deletion.
Downloads have `nosniff` and a sandbox CSP. **Three kinds of file open in the app**, decided by the
server from the file's first bytes and never from the uploader's claim alone:

- a PDF with a PDF signature, served as `application/pdf`. That one response drops `sandbox` (a
  browser's built-in PDF viewer does not load in a sandboxed document) and keeps `default-src 'none'`;
- a PNG, JPEG, GIF or WebP image whose signature matches its type (not SVG, which can carry script);
- text (plain, CSV, TSV, Markdown, JSON, OFX/QFX/QIF) without NUL bytes, always served as
  `text/plain`, so markup in it is shown as its source.

Everything else, office files included, is a download for the user's own application. The viewer
fetches the file and shows its own copy, because the deployment forbids framing its responses
(`X-Frame-Options: DENY`) and that header should stay. A PDF is drawn page by page with pdf.js
(`pdfjs-dist`, loaded only when a PDF is opened, with script evaluation off) rather than handed to
the browser's own viewer in a frame: phone browsers leave a framed PDF blank. The first 40 pages are
drawn; "Open in new tab" and the download give the whole file. The server never executes, extracts or
parses uploaded files.

**Files travel beside the document, not inside it.** The export document (ADR-0036) lists each file —
account, name, type, size, SHA-256, timestamp — and a `path`, `files/<sha256>`, saying where its bytes
are in the archive. It carries no bytes, so it stays a few megabytes of readable JSON however much is
attached, and ADR-0036's 64 MiB ceiling on it stays as it was. Format version 3 marks this, so older
readers refuse rather than silently dropping files; versions 1 and 2 still read.

`GET /export/archive` is a zip holding that document as `export.json` and each distinct file under
its path. It is built one file at a time into a temporary file and streamed, so an export's memory
does not grow with the attachments. Members are named by digest: identical uploads are stored once,
and no uploaded filename becomes a path in the zip. `GET /export` still returns the document alone.

`POST /import` takes either. **An import without the bytes degrades instead of failing:** the ledger
is imported, each file whose bytes are absent (the document was imported alone, or the archive lost a
member) is left out, and the result carries one warning saying how many. Importing the archive later
adds exactly the files left out. Imports remap accounts, allocate fresh IDs, and match a file by
account, sanitized filename and digest, with occurrence ordinals preserving duplicate uploads. A
member is read up to the size the document states plus one byte, then its size and digest are checked,
so a member that inflates past its claim or holds other bytes is refused. A `path` is only ever a name
looked up in the archive.

Files are outside the anonymized agent mirror: names and contents cannot be reliably anonymized.
An agent reaches them only through the separate `documents:read` scope (ADR-0062).

## Consequences

Database backups carry files without external storage dependencies, and so does the archive export.
Database size grows with attachments, and nothing in the app stops it: the household's disk is the
budget. The proxy's body limit is raised to clear one file, and lifted for `/api/import`. That
route does not declare its upload as a parameter: the framework would spool a declared upload to disk
before checking who sent it. It checks the owner's session and CSRF token first, then counts the body
as it arrives against `METALMARK_MAX_IMPORT_UPLOAD_BYTES` (8 GiB by default), a bound on the API's
temporary disk rather than on what a household may keep.

The document on its own is no longer a complete copy of a household that has files. The export
screen says so and offers the archive first. A file over 100 MiB cannot be kept. Downgrading the
additive table migration explicitly discards attachments while preserving the ledger.
