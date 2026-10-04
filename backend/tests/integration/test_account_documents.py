"""Attachment RLS, content fidelity and portable round trips."""

import copy
import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select, text

from app.db import scoped_session
from app.models import Account, AccountDocument, Owner
from app.services import documents, portability
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration


async def _account(session, household_id, name="Documents"):
    owner = Owner(household_id=household_id, name=f"{name} owner", kind="person", sort=0)
    session.add(owner)
    await session.flush()
    account = Account(
        household_id=household_id,
        name=name,
        type="depository",
        currency="USD",
        is_asset=True,
        owner_id=owner.id,
    )
    session.add(account)
    await session.flush()
    return account


async def _archive(session, household_id) -> io.BytesIO:
    out = io.BytesIO()
    await portability.write_archive(session, household_id, out)
    out.seek(0)
    return out


async def test_files_are_private_and_round_trip_through_the_archive(household_factory):
    source = await household_factory()
    target = await household_factory()
    content = b"%PDF-1.7\nprivate bytes\x00\xff"
    digest = hashlib.sha256(content).hexdigest()
    async with scoped_session(household_id=source) as session:
        account = await _account(session, source)
        account_id = account.id
        row = await documents.create_document(
            session, source, account_id, "../statement.pdf", "application/pdf", content
        )
        row_id = row.id
        await documents.create_document(
            session, source, account_id, "statement.pdf", "application/pdf", content
        )
        doc = await portability.export_document(session, source)
        # The document lists the files and says where their bytes are; it does
        # not carry them.
        entry = doc["account_documents"][0]
        assert entry["path"] == f"files/{digest}" and entry["sha256"] == digest
        assert "content_base64" not in entry
        assert content.hex() not in portability.dumps(doc).hex()
        assert doc["version"] == 3
        archive = await _archive(session, source)
    with zipfile.ZipFile(archive) as z:
        # Two uploads of the same bytes are one member.
        assert sorted(z.namelist()) == ["export.json", f"files/{digest}"]
        assert z.read(f"files/{digest}") == content
        assert json.loads(z.read("export.json"))["account_documents"] == doc["account_documents"]
    async with scoped_session(household_id=target) as session:
        assert (await session.execute(select(AccountDocument))).scalars().all() == []
        with pytest.raises(LedgerError, match="Account not found"):
            await documents.create_document(session, target, account_id, "hidden", None, content)
        archive.seek(0)
        raw, files = portability.open_archive(archive)
        result = await portability.import_document(session, target, raw, files)
        assert result.created["account_documents"] == 2 and result.warnings == []
        result = await portability.import_document(session, target, raw, files)
        assert result.matched["account_documents"] == 2
        rows = (await session.execute(select(AccountDocument))).scalars().all()
        assert len(rows) == 2
        row = rows[0]
        assert row.id != row_id and row.account_id != account_id
        assert row.filename == "statement.pdf"
        assert (await session.execute(select(AccountDocument.content))).scalars().all() == [
            content,
            content,
        ]
        await session.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": row.account_id})
        assert (await session.execute(select(AccountDocument))).scalars().all() == []


async def test_the_document_alone_imports_the_ledger_and_says_what_it_left_out(household_factory):
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as session:
        account = await _account(session, source)
        await documents.create_document(session, source, account.id, "a.pdf", None, b"one")
        await documents.create_document(session, source, account.id, "b.pdf", None, b"two")
        doc = await portability.export_document(session, source)
        archive = await _archive(session, source)
    async with scoped_session(household_id=target) as session:
        result = await portability.import_document(session, target, portability.dumps(doc))
        assert result.created["accounts"] == 1
        assert "account_documents" not in result.created
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith("2 account files were not restored")
        assert (await session.execute(select(AccountDocument))).scalars().all() == []

        # An archive that lost one member restores the other and counts the gap.
        partial = io.BytesIO()
        with zipfile.ZipFile(archive) as z, zipfile.ZipFile(partial, "w") as out:
            names = sorted(n for n in z.namelist() if n.startswith("files/"))
            out.writestr("export.json", z.read("export.json"))
            out.writestr(names[0], z.read(names[0]))
        partial.seek(0)
        raw, files = portability.open_archive(partial)
        result = await portability.import_document(session, target, raw, files)
        assert result.created["account_documents"] == 1
        assert result.warnings[0].startswith("1 account file was not restored")

        # The whole archive, later, adds exactly what was left out.
        archive.seek(0)
        raw, files = portability.open_archive(archive)
        result = await portability.import_document(session, target, raw, files)
        assert result.created["account_documents"] == 1
        assert result.matched["account_documents"] == 1
        assert result.warnings == []
        stored = (await session.execute(select(AccountDocument.content))).scalars().all()
        assert sorted(stored) == [b"one", b"two"]


async def test_invalid_and_oversized_files_refused(household_factory, monkeypatch):
    hh = await household_factory()
    monkeypatch.setattr(documents, "MAX_FILE_BYTES", 64)
    async with scoped_session(household_id=hh) as session:
        account = await _account(session, hh, "Files")
        for content in (b"", b"x" * 65):
            with pytest.raises(LedgerError):
                await documents.create_document(session, hh, account.id, "file", None, content)
        await documents.create_document(session, hh, account.id, "file", "text/html", b"hello")
        # There is no household total: files are bounded one at a time.
        for i in range(4):
            await documents.create_document(session, hh, account.id, f"f{i}", None, b"y" * 64)
        archive = await _archive(session, hh)
    other = await household_factory()
    with zipfile.ZipFile(archive) as z:
        doc = json.loads(z.read("export.json"))
        members = {n: z.read(n) for n in z.namelist() if n.startswith("files/")}
    hello = hashlib.sha256(b"hello").hexdigest()
    index = next(i for i, d in enumerate(doc["account_documents"]) if d["sha256"] == hello)

    def rebuilt(document, files):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("export.json", json.dumps(document))
            for name, data in files.items():
                z.writestr(name, data)
        out.seek(0)
        return portability.open_archive(out)

    async with scoped_session(household_id=other) as session:
        # A member that is not the bytes the document described: longer than it
        # said (a zip that inflates), shorter, or simply different.
        for swapped in (b"hello" * 1000, b"hell", b"HELLO"):
            raw, files = rebuilt(doc, {**members, f"files/{hello}": swapped})
            with pytest.raises(LedgerError, match="does not match"):
                await portability.import_document(session, other, raw, files)
        for field, value in (("sha256", "zz"), ("size_bytes", 0), ("size_bytes", 65)):
            bad = copy.deepcopy(doc)
            bad["account_documents"][index][field] = value
            raw, files = rebuilt(bad, members)
            with pytest.raises(LedgerError):
                await portability.import_document(session, other, raw, files)
        # A path is only ever a name looked up in the archive, never a place on disk.
        elsewhere = copy.deepcopy(doc)
        elsewhere["account_documents"][index]["path"] = "../../etc/passwd"
        raw, files = rebuilt(elsewhere, members)
        result = await portability.import_document(session, other, raw, files)
        assert result.warnings[0].startswith("1 account file was not restored")


def test_an_archive_that_is_not_one_is_refused():
    for body in (b"PK\x03\x04 not really", b""):
        with pytest.raises(LedgerError, match="zip"):
            portability.open_archive(io.BytesIO(body))
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("something-else.json", "{}")
    empty.seek(0)
    with pytest.raises(LedgerError, match="no export.json"):
        portability.open_archive(empty)


@pytest.mark.parametrize(
    ("filename", "media_type", "head", "expected"),
    [
        ("a.pdf", "application/pdf", b"%PDF-1.7", "application/pdf"),
        ("a.pdf", "application/pdf", b"<html>", None),
        ("a.png", "image/png", b"\x89PNG\r\n\x1a\n....", "image/png"),
        ("a.jpg", "image/jpeg", b"\xff\xd8\xff\xe0", "image/jpeg"),
        ("a.webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        ("a.webp", "image/webp", b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
        ("a.svg", "image/svg+xml", b"<svg onload=", None),
        ("a.html", "text/html", b"<html>", None),
        ("a.csv", "text/csv", b"date,amount", "text/plain; charset=utf-8"),
        ("a.csv", "application/vnd.ms-excel", b"date,amount", "text/plain; charset=utf-8"),
        ("notes.txt", "text/plain", b"\x00\x01binary", None),
        ("a.docx", "application/octet-stream", b"PK\x03\x04", None),
    ],
)
def test_only_inert_types_are_shown_in_place(filename, media_type, head, expected):
    assert documents.preview_type(filename, media_type, head) == expected


def test_upload_metadata_sanitized():
    assert documents.safe_filename("C:\\folder\\bad\r\nname.pdf") == "badname.pdf"
    assert documents.safe_filename("../") == "document"
    assert documents.safe_media_type("text/html\r\nX-Header: bad") == "application/octet-stream"


async def test_download_headers_preview_and_delete(household_factory):
    from types import SimpleNamespace

    from app.api.documents import delete_document, download_document, list_documents

    hh = await household_factory()
    async with scoped_session(household_id=hh) as session:
        owner = Owner(household_id=hh, name="File owner", kind="person", sort=0)
        session.add(owner)
        await session.flush()
        account = Account(
            household_id=hh,
            name="Files",
            type="depository",
            currency="USD",
            is_asset=True,
            owner_id=owner.id,
        )
        session.add(account)
        await session.flush()
        ctx = SimpleNamespace(session=session, household_id=hh)
        row = await documents.create_document(
            session, hh, account.id, 'quote" café.pdf', "application/pdf", b"%PDF-1.7\nfile"
        )
        download = await download_document(account.id, row.id, False, ctx)
        assert download.body == b"%PDF-1.7\nfile"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert "%22" in download.headers["content-disposition"]
        assert download.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in download.headers["content-security-policy"]
        preview = await download_document(account.id, row.id, True, ctx)
        assert preview.headers["content-disposition"].startswith("inline;")
        assert preview.headers["content-type"] == "application/pdf"
        # The built-in PDF viewer does not load in a sandboxed document; the
        # preview is locked down by type and signature instead.
        assert "sandbox" not in preview.headers["content-security-policy"]
        assert "default-src 'none'" in preview.headers["content-security-policy"]
        assert preview.headers["x-content-type-options"] == "nosniff"
        fake_pdf = await documents.create_document(
            session, hh, account.id, "fake.pdf", "application/pdf", b"<html>untrusted</html>"
        )
        response = await download_document(account.id, fake_pdf.id, True, ctx)
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-type"] == "application/octet-stream"
        # Text is shown as plain text whatever it claimed, and stays sandboxed.
        notes = await documents.create_document(
            session, hh, account.id, "notes.csv", "text/csv", b"<script>x</script>,1"
        )
        response = await download_document(account.id, notes.id, True, ctx)
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert "sandbox" in response.headers["content-security-policy"]
        listed = {d.filename: d.preview for d in await list_documents(account.id, ctx)}
        assert listed == {'quote" café.pdf': "pdf", "fake.pdf": None, "notes.csv": "text"}
        await delete_document(account.id, row.id, ctx)
        await session.flush()
        assert len(await list_documents(account.id, ctx)) == 2
        with pytest.raises(LedgerError, match="Document not found"):
            await download_document(account.id, row.id, False, ctx)
