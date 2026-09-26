"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api import (
    accounts,
    agent,
    agent_tokens,
    anon_debug,
    auth,
    categories,
    checks,
    connections,
    fx,
    health,
    household,
    imports,
    income,
    institutions,
    investments,
    owners,
    portability,
    reports,
    rules,
    transactions,
)
from app.logging import configure_logging, get_logger
from app.services.auth import AuthError
from app.services.errors import LedgerError
from app.settings import get_settings

log = get_logger("app")

#: The app's own routes. The agent routes (ADR-0048) read this list to find the
#: route an agent path names, so it is data rather than a run of include calls.
APP_ROUTERS = (
    health.router,
    checks.router,
    auth.router,
    accounts.router,
    categories.router,
    owners.router,
    income.router,
    transactions.router,
    fx.router,
    reports.router,
    rules.router,
    imports.router,
    household.router,
    connections.router,
    investments.router,
    portability.router,
    institutions.router,
)
#: Agent access (ADR-0048): token administration, and the two anonymized surfaces.
AGENT_ROUTERS = (agent_tokens.router, agent.router, anon_debug.router)


class NoStoreForDataMiddleware(BaseHTTPMiddleware):
    """PWA safety: financial API responses must never be cached on-device
    (ARCHITECTURE §5). The service worker caches the shell only."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/") and path not in {"/healthz", "/openapi.json", "/docs"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.env)
    log.info("startup", env=settings.env, base_currency=settings.default_base_currency)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MetalMark API",
        version="0.1.0",
        description="Self-hosted household personal finance. LAN/VPN only.",
        lifespan=lifespan,
    )

    if settings.env == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(NoStoreForDataMiddleware)

    @app.exception_handler(LedgerError)
    async def _ledger_error(_request: Request, exc: LedgerError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(AuthError)
    async def _auth_error(_request: Request, exc: AuthError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        """422 without echoing the request body back.

        FastAPI's default handler includes ``input`` (the offending value) and
        ``ctx`` (the exception it raised) per error. For most routes that is a
        convenience; for ``POST /connections/claim`` it is a credential coming
        back in a response — and one that lands in browser devtools, in any
        client-side error reporter, and in whatever proxy logs the response. A
        wrong-typed setup token would be echoed verbatim.

        Stripping both fields globally rather than special-casing one route,
        because the class of bug is "this endpoint happens to take a secret" and
        the next endpoint with a secret should not have to remember. What is
        kept — ``loc``, ``type``, ``msg`` — is what a form needs to point at the
        offending field.
        """
        errors = [
            {k: v for k, v in error.items() if k not in ("input", "ctx")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    for router in APP_ROUTERS + AGENT_ROUTERS:
        app.include_router(router)
    return app


app = create_app()
