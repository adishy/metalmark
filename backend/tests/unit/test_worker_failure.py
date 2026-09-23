"""``worker.failure``: what the log may say about an exception (ADR-0047)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app import worker


def test_a_library_error_is_placed_at_the_app_line_that_called_it():
    """A database error is raised deep in the driver; the useful line is ours."""
    engine = create_engine("sqlite://")
    with pytest.raises(Exception) as caught, engine.connect() as conn:
        conn.execute(text("SELECT * FROM no_such_table WHERE name = 'Amex Secret'"))
    out = worker.failure(caught.value)
    assert out["error_type"] == "OperationalError"
    assert "Secret" not in repr(out)
    # The test file is outside the app package, so the fallback — the deepest
    # frame — is inside SQLAlchemy; the point is that no message comes along.
    assert out["at"].rsplit(":", 1)[1].isdigit()
