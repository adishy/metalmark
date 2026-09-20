"""`/healthz` has to be able to fail.

Every consumer reads it as a readiness gate — the Dockerfile HEALTHCHECK,
compose's ``depends_on``, and CI's wait loops — so the property that matters is
that an unusable database produces a non-2xx. It once answered
``200 {"status": "degraded", "db": false}`` instead, so CI reported a stack with
no schema and no app role as healthy and the real failure surfaced several steps
later as a misleading authentication error.
"""

from __future__ import annotations

import httpx
import pytest

from app.api import health

pytestmark = pytest.mark.integration


async def _get_healthz() -> httpx.Response:
    # Imported here, not at module scope: `create_app()` reads settings, and the
    # test database env is only set once the session fixture has run.
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get("/healthz")


async def test_healthz_is_ok_when_the_database_answers():
    resp = await _get_healthz()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}


async def test_healthz_is_503_when_the_database_is_unreachable(monkeypatch):
    """A health check that cannot fail is not a health check."""

    def _boom():
        raise RuntimeError('password authentication failed for user "metalmark_app"')

    monkeypatch.setattr(health, "get_engine", _boom)
    resp = await _get_healthz()

    assert resp.status_code == 503
    # The reason is logged rather than returned: this route is unauthenticated.
    assert "password" not in resp.text.lower()
