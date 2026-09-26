"""``worker.failure``: what the log may say about an exception (ADR-0047)."""

from __future__ import annotations

import traceback
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app import worker
from app.core import money


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


def test_an_app_raise_names_the_module_inside_the_package():
    """The place is relative to the app package, so it reads the same everywhere.

    ``money.allocate`` raises from inside the package. The path reported is the one
    inside it — not the absolute path, and not the path with the mount in front of
    it, either of which would say something different from a checkout than from the
    container (ADR-0047 keeps the message out of the log, so this string is all an
    operator has).
    """
    with pytest.raises(ValueError) as caught:
        money.allocate(Decimal("1"), 0)
    assert worker.failure(caught.value)["at"].startswith("core/money.py:")


def test_only_this_package_is_ours():
    """A file *beside* the package is not in it, however much of the path is shared.

    This is the container's shape: the backend is mounted at ``/app``, so the
    package is ``/app/app`` and the suite is ``/app/tests``. ``"/app/" in filename``
    cannot tell those apart — a test's own frame became "ours" and the log named a
    test line as the place an app error was raised. Deciding by directory fixes it,
    and this file is a live example of the pair: ``tests/unit`` beside ``app``.
    """
    assert worker._is_ours(__file__) is False
    assert worker._is_ours(str(Path(worker.__file__).resolve())) is True


def test_a_synthetic_filename_is_never_ours():
    """``<string>`` is not a path, and resolving it would invent one.

    ``exec(compile(...))`` and the REPL produce frames whose filename is a
    placeholder. ``Path("<string>").resolve()`` would make it ``cwd/<string>`` — a
    path under whatever directory the process happens to be in, which is a directory
    this package could be in.
    """
    for name in ("<string>", "<stdin>", "<frozen importlib._bootstrap>", ""):
        assert worker._is_ours(name) is False


def test_a_frame_outside_the_package_keeps_the_path_it_has():
    """The fallback for a traceback with nothing of ours in it, including its line."""
    frame = traceback.FrameSummary("/usr/lib/python3/site-packages/pkg/mod.py", 7, "run")
    assert worker._where(frame) == "/usr/lib/python3/site-packages/pkg/mod.py:7"


def test_a_path_the_os_will_not_resolve_is_not_ours(tmp_path):
    """A symlink loop is not a place in the package, and asking is not an error.

    ``resolve()`` raises rather than returning a path for one (``RuntimeError`` on
    3.12), and ``failure`` must not turn a reportable exception into a second one
    raised from the reporter.
    """
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    assert worker._is_ours(str(loop)) is False
