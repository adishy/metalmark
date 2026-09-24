"""Institution logos: uploaded, or fetched from the bank's own site (session 06, item I)."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.db import scoped_session
from app.schemas.ledger import AccountCreate
from app.services import institutions as svc
from app.services import ledger
from tests.integration.test_api_owners import (  # noqa: F401 - fixtures
    _clean_identity,
    _signup,
    client,
)

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
async def hh(household_factory):
    return await household_factory()


def test_known_institutions_map_to_their_own_sites():
    assert svc.domain_for("Chase Bank") == "chase.com"
    assert svc.domain_for("Capital One") == "capitalone.com"
    assert svc.domain_for("Fidelity Investments") == "fidelity.com"
    assert svc.domain_for("American Express") == "americanexpress.com"
    assert svc.domain_for("Some Credit Union") is None


def test_only_raster_images_by_their_bytes():
    assert svc.sniff(PNG) == "image/png"
    assert svc.sniff(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert svc.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert svc.sniff(SVG) is None
    assert svc.sniff(b"<html>") is None


def test_candidates_prefer_the_touch_icon_and_skip_svg():
    html = """<head>
      <link rel="icon" href="/fav.svg">
      <link rel="icon" sizes="32x32" href="/f32.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/touch.png">
    </head>"""
    urls = svc._candidates("https://www.chase.com/", html)
    assert urls[0] == "https://www.chase.com/touch.png"
    assert "https://www.chase.com/fav.svg" not in urls
    assert urls[-1] == "https://www.chase.com/favicon.ico"


async def test_fetch_stores_the_banks_icon_and_keeps_an_uploaded_one(hh):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(
                200, html='<link rel="apple-touch-icon" href="/touch.png">'
            )
        if request.url.path == "/touch.png":
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)

    async with scoped_session(hh) as s:
        for name, inst in (("Checking", "Chase"), ("Card", "Capital One"), ("Pot", "Tiny CU")):
            await ledger.create_account(
                s, hh,
                AccountCreate(name=name, type="depository", currency="USD", institution=inst),
            )
        await svc.save_logo(s, hh, name="Capital One", data=PNG, source="uploaded")
        result = await svc.fetch_missing(s, hh, transport=httpx.MockTransport(handler))

        assert result.fetched == ["Chase"]
        assert result.unknown == ["Tiny CU"]
        assert result.kept == 1
        # Only the institution's own site was asked, and nobody else.
        assert {httpx.URL(u).host for u in requested} == {"www.chase.com"}
        rows = {r.name: r for r in await svc.in_use(s)}
        assert rows["Chase"].logo.source == "fetched"
        assert rows["Capital One"].logo.source == "uploaded"


async def test_upload_serve_and_delete_through_the_api(client):  # noqa: F811
    await _signup(client)
    await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD", "institution": "Chase Bank",
    })
    rows = (await client.get("/institutions")).json()
    assert rows == [{
        "name": "Chase Bank", "key": "chase bank", "fetchable": True, "has_logo": False,
        "logo_source": None, "logo_updated_at": None,
    }]

    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    body = {"name": "Chase Bank", "data_base64": data_url}
    resp = await client.put("/institutions/chase bank/logo", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_logo"] is True

    img = await client.get("/institutions/chase bank/logo")
    assert img.status_code == 200
    assert img.content == PNG
    assert img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in img.headers["content-security-policy"]

    svg = {"name": "Chase Bank", "data_base64": base64.b64encode(SVG).decode()}
    assert (await client.put("/institutions/chase bank/logo", json=svg)).status_code == 415

    assert (await client.delete("/institutions/chase bank/logo")).status_code == 204
    assert (await client.get("/institutions/chase bank/logo")).status_code == 404


# ---- sync freshness (session 06): a bridge that answers "ok" with nothing new ----


async def test_a_connection_says_when_it_last_brought_new_data(hh):
    from datetime import UTC, datetime, timedelta

    from app.models import AccountConnection, SyncRun
    from app.services import connections as conn_svc

    async with scoped_session(hh) as s:
        conn = AccountConnection(household_id=hh, provider="fake", org_name="Bank",
                                 access_url_encrypted="x")
        s.add(conn)
        await s.flush()
        t0 = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
        rows = [
            (t0, "ok", 164),                          # the first sync
            (t0 + timedelta(hours=6), "ok", 9),       # the last one with anything new
            (t0 + timedelta(hours=12), "ok", 0),
            (t0 + timedelta(hours=18), "error", 0),   # a failure is not "quiet"
            (t0 + timedelta(hours=24), "ok", 0),
            (t0 + timedelta(hours=30), "ok", 0),
        ]
        for started, status, inserted in rows:
            s.add(SyncRun(household_id=hh, connection_id=conn.id, started_at=started,
                          status=status, trigger="cron", txns_inserted=inserted))
        await s.flush()

        fresh = await conn_svc.data_freshness(s)
        assert fresh[conn.id]["last_new_data_at"] == t0 + timedelta(hours=6)
        assert fresh[conn.id]["quiet_syncs"] == 3
