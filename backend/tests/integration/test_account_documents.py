"""Attachment RLS, content fidelity and portable round trips."""

import base64
import copy

import pytest
from sqlalchemy import select, text

from app.db import scoped_session
from app.models import Account, AccountDocument, Owner
from app.services import documents, portability
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration


async def test_files_are_private_and_round_trip(household_factory):
    source = await household_factory()
    target = await household_factory()
    content = b"%PDF-1.7\nprivate bytes\x00\xff"
    async with scoped_session(household_id=source) as session:
        owner = Owner(household_id=source, name="File owner", kind="person", sort=0)
        session.add(owner)
        await session.flush()
        account = Account(
            household_id=source,
            name="Documents",
            type="depository",
            currency="USD",
            is_asset=True,
            owner_id=owner.id,
        )
        session.add(account)
        await session.flush()
        account_id = account.id
        row = await documents.create_document(
            session, source, account_id, "../statement.pdf", "application/pdf", content
        )
        row_id = row.id
        await documents.create_document(
            session, source, account_id, "statement.pdf", "application/pdf", content
        )
        doc = await portability.export_document(session, source)
        assert base64.b64decode(doc["account_documents"][0]["content_base64"]) == content
        assert doc["version"] == 3
    async with scoped_session(household_id=target) as session:
        assert (await session.execute(select(AccountDocument))).scalars().all() == []
        with pytest.raises(LedgerError, match="Account not found"):
            await documents.create_document(session, target, account_id, "hidden", None, content)
        result = await portability.import_document(session, target, portability.dumps(doc))
        assert result.created["account_documents"] == 2
        result = await portability.import_document(session, target, portability.dumps(doc))
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


async def test_invalid_and_oversized_files_refused(household_factory):
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
        for content in (b"", b"x" * (documents.MAX_FILE_BYTES + 1)):
            with pytest.raises(LedgerError):
                await documents.create_document(session, hh, account.id, "file", None, content)
        await documents.create_document(session, hh, account.id, "file", "text/html", b"hello")
        doc = await portability.export_document(session, hh)
        for field, value in (("content_base64", "bad!"), ("sha256", "0" * 64), ("size_bytes", 999)):
            bad = copy.deepcopy(doc)
            bad["account_documents"][0][field] = value
            with pytest.raises(LedgerError):
                await portability.import_document(session, hh, portability.dumps(bad))


async def test_the_household_total_keeps_every_export_importable(household_factory, monkeypatch):
    hh = await household_factory()
    monkeypatch.setattr(documents, "MAX_HOUSEHOLD_BYTES", 10)
    async with scoped_session(household_id=hh) as session:
        owner = Owner(household_id=hh, name="File owner", kind="person", sort=0)
        session.add(owner)
        await session.flush()
        accounts = [
            Account(household_id=hh, name=name, type="depository", currency="USD",
                    is_asset=True, owner_id=owner.id)
            for name in ("One", "Two")
        ]
        session.add_all(accounts)
        await session.flush()
        first = await documents.create_document(session, hh, accounts[0].id, "a", None, b"123456")
        # The total is the household's, not the account's.
        with pytest.raises(LedgerError) as refused:
            await documents.create_document(session, hh, accounts[1].id, "b", None, b"12345")
        assert refused.value.status == 413
        await documents.create_document(session, hh, accounts[1].id, "b", None, b"1234")
        # An import is held to the same total, and refused whole.
        doc = await portability.export_document(session, hh)
        extra = copy.deepcopy(doc["account_documents"][0])
        extra["filename"] = "c"
        doc["account_documents"].append(extra)
        with pytest.raises(LedgerError):
            await portability.import_document(session, hh, portability.dumps(doc))
        # Deleting makes room again.
        await session.delete(first)
        await session.flush()
        await documents.create_document(session, hh, accounts[0].id, "c", None, b"123456")


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
        await delete_document(account.id, row.id, ctx)
        await session.flush()
        assert len(await list_documents(account.id, ctx)) == 1
        with pytest.raises(LedgerError, match="Document not found"):
            await download_document(account.id, row.id, False, ctx)
