"""Structured logging via structlog. JSON in prod, pretty in dev."""

from __future__ import annotations

import logging
import sys

import structlog

#: Libraries that log their own requests, pinned above whatever level the
#: application is running at.
#:
#: ``httpx`` logs ``HTTP Request: GET https://user:pass@host/...`` at INFO, and
#: the whole URL — Basic credentials included — at DEBUG. That is a SimpleFIN
#: access URL, the app's single long-lived bank credential, printed to stdout on
#: every sync and every claim. ``httpcore`` logs the same request one layer down,
#: and would still do it if only httpx were quiet.
#:
#: So they are pinned to WARNING unconditionally, not raised to the app's level:
#: ``METALMARK_LOG_LEVEL=DEBUG`` is a reasonable thing to want while debugging the
#: ledger, and it must not be the switch that turns a credential into log output.
#: The cost is that httpx's request line is unavailable at any level; if it is
#: ever genuinely needed, the answer is a per-request log we write ourselves with
#: the URL redacted, not a library's convenience logger.
NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")


def configure_logging(level: str = "INFO", env: str = "dev") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    for name in NOISY_LOGGERS:
        # ``setLevel`` after ``basicConfig``: the root logger's level is already
        # set, and a library logger's own level is the only thing that can lower
        # it again for that library alone.
        logger = logging.getLogger(name)
        # ``disabled`` is cleared as well as the level set, and that is not
        # belt-and-braces — it is the half that survives contact with another
        # library. ``logging.config.fileConfig`` and ``dictConfig`` default to
        # ``disable_existing_loggers=True``, which sets ``disabled = True`` on
        # every logger that already exists; ``alembic/env.py`` calls
        # ``fileConfig``, so any process that migrates and then serves — a
        # management command, a test session, a container entrypoint somebody
        # adds later — would otherwise leave these loggers silenced by a config
        # file that never mentioned them. ``setLevel`` does not clear
        # ``disabled``, so the pin would look applied and be inert.
        #
        # Re-enabling a logger and then pinning it to WARNING is the only
        # combination that means "quiet on purpose, and it stays quiet": a
        # logger left disabled is quiet by someone else's accident, and the next
        # ``fileConfig`` with the opposite default hands the credential straight
        # back.
        logger.disabled = False
        logger.setLevel(logging.WARNING)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if env == "dev":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)
