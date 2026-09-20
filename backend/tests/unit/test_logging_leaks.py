"""The credential reaches no log *record*, at any level.

The other half of the ADR-0016 leak test — that the access URL reaches no
``sync_run_events`` row and no ``last_error`` — is in
``tests/integration/test_sync.py``, because it is a test of ``sync.py``'s write
path. This half belongs here: it is a test of ``app/logging.py``, and the failure
it guards against happens one layer below the application entirely.

**The bug this file exists for was live, not hypothetical.** ``httpx`` logs
``HTTP Request: GET https://user:pass@host/…`` at INFO — its own default level,
and the application's. ``configure_logging`` set the root logger and nothing else,
so on a deployment running at INFO the household's bank credential went to stdout
on every sync, every retry, and every claim. The M1 configuration was a function
that did one correct thing and therefore looked finished.

Both tests below assert the leak **and** the fix in one capture, because the
tempting version of either test passes for the wrong reason:

* "the credential is not in the log" passes on a machine where nothing was
  captured, where the request never happened, or where the logger was silenced by
  a config file that had nothing to do with the pin.
* "the credential is in the log, so the pin matters" proves the library's
  behaviour and not the application's.

So each test drives the same request twice — pinned and unpinned — and asserts
the two outcomes *differ*. ``_probe`` is the sentinel that proves the capture
itself was live for both halves.
"""

from __future__ import annotations

import logging

import httpx

from app.logging import NOISY_LOGGERS, configure_logging
from app.services.fake_simplefin import FAKE_ACCESS_URL
from app.settings import get_settings

#: ``fake-password`` is the secret inside ``FAKE_ACCESS_URL`` — the placeholder
#: that exists (RFC 2606 domain, obvious credentials) to be grepped for.
CREDENTIAL = "fake-password"
USERNAME = "fake-user"


async def _fetch(url: str = FAKE_ACCESS_URL) -> httpx.Response:
    """One request through the real ``httpx`` client, over no network at all.

    The handler is a bare 403 because the point is the *logging*, not the client:
    what matters is that a real request carries a real credentialed URL through
    ``_send_single_request``, which is the frame that logs it.
    """
    transport = httpx.MockTransport(lambda _request: httpx.Response(403, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.get(url)


def _unpin() -> None:
    """Put the noisy loggers back the way the libraries ship them.

    ``disabled = False`` is load-bearing here and not ceremony: this test session
    has already run ``alembic upgrade head``, and ``alembic/env.py`` calls
    ``fileConfig``, whose default ``disable_existing_loggers=True`` silences every
    logger that existed at that moment. httpx is one of them. Leaving that in
    place would make *both* halves of the assertion below silently true and the
    test worthless — which is how this was found.
    """
    for name in NOISY_LOGGERS:
        logger = logging.getLogger(name)
        logger.disabled = False
        logger.setLevel(logging.NOTSET)


def _logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


#: Sent by ``_probe`` from a logger nobody pins. Without it, ``not any(...)``
#: cannot distinguish "the credential was not logged" from "nothing was logged at
#: all", and the second is what a broken capture looks like.
_PROBE = "capture-is-live"


def _probe() -> None:
    logging.getLogger("probe").info(_PROBE)


def _restore_logging() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)


async def test_configure_logging_keeps_the_credential_out_of_the_log(caplog) -> None:
    """The fix, at the level that makes it a fix, next to the thing it prevents.

    Run at **DEBUG**, because the tempting version of this setting is the one
    that breaks it: ``METALMARK_LOG_LEVEL=DEBUG`` is a reasonable thing to want
    while chasing a ledger bug, and "debugging" must not be the switch that turns
    the household's bank credential into log output. The pin is unconditional for
    exactly that reason.
    """
    configure_logging("DEBUG", "test")
    try:
        with caplog.at_level(logging.DEBUG):
            _probe()
            await _fetch()
        pinned = _logged(caplog)

        _unpin()  # the fix removed, with everything else held constant
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            _probe()
            await _fetch()
        unpinned = _logged(caplog)
    finally:
        _restore_logging()

    assert any(line == _PROBE for line in pinned)  # capture is live
    assert any(line == _PROBE for line in unpinned)
    assert not any(CREDENTIAL in line for line in pinned), pinned
    assert not any(FAKE_ACCESS_URL in line for line in pinned), pinned
    # ... and the same request, with the pin removed, does leak it.
    assert any(CREDENTIAL in line for line in unpinned), unpinned
    assert any(FAKE_ACCESS_URL in line for line in unpinned), unpinned


def test_every_library_that_logs_a_request_is_pinned() -> None:
    """``httpx`` and ``httpcore`` print the same request one layer apart.

    Quieting only httpx would leave the credential in the log through the
    connection pool's own logger — the exact "fixed the symptom in the frame I
    happened to read" mistake this module is here to prevent. ``disabled`` is
    asserted too: a logger that is silenced by another config file is quiet by
    accident, and the next ``fileConfig`` with the opposite default un-silences
    it without touching anything we wrote.
    """
    _restore_logging()
    for name in NOISY_LOGGERS:
        logger = logging.getLogger(name)
        assert not logger.disabled, name
        assert logger.level >= logging.WARNING, name


def test_the_pin_survives_a_reconfiguration() -> None:
    """``configure_logging`` runs at startup *and* is called again by anything
    that reconfigures logging; the pin is inside the function rather than a
    one-time import side effect, so a second call cannot lose it."""
    configure_logging("DEBUG", "test")
    try:
        assert logging.getLogger("httpx").level >= logging.WARNING
        configure_logging("INFO", "prod")
        assert logging.getLogger("httpcore").level >= logging.WARNING
    finally:
        _restore_logging()
