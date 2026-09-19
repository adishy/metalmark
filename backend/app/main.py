"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api import auth, health
from app.logging import configure_logging, get_logger
from app.settings import get_settings

log = get_logger("app")


class NoStoreForDataMiddleware(BaseHTTPMiddleware):
    """PWA safety: financial API responses must never be cached on-device
    (ARCHITECTURE §5). The service worker caches the shell only."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/") and request.url.path not in {"/healthz", "/openapi.json", "/docs"}:
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
        title="Kestrel API",
        version="0.1.0",
        description="Self-hosted personal finance (Monarch-style). LAN/VPN only.",
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

    app.include_router(health.router)
    app.include_router(auth.router)
    return app


app = create_app()
