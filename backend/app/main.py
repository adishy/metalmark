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
    auth,
    categories,
    checks,
    connections,
    fx,
    health,
    household,
    imports,
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

    app.include_router(health.router)
    app.include_router(checks.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(categories.router)
    app.include_router(owners.router)
    app.include_router(transactions.router)
    app.include_router(fx.router)
    app.include_router(reports.router)
    app.include_router(rules.router)
    app.include_router(imports.router)
    app.include_router(household.router)
    app.include_router(connections.router)
    app.include_router(investments.router)
    app.include_router(portability.router)
    return app


app = create_app()
