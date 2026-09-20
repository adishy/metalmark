from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import get_engine
from app.logging import get_logger

router = APIRouter(tags=["health"])

log = get_logger("health")

# Kept as a literal rather than pulled from the docstring: the docstring is the
# published description in contracts/openapi.yaml, and this is a note to whoever
# has to debug it, not something an API consumer needs.
UNAVAILABLE = {
    "description": (
        "The database is unreachable or the app role cannot authenticate. The "
        "process is up but the stack is not usable — most often because "
        "`alembic upgrade head` has not been run against this database."
    )
}


@router.get("/healthz", responses={503: UNAVAILABLE})
async def healthz() -> dict:
    """Readiness: 503 while the database is unreachable, 200 once it answers."""
    # Every caller reads this as a readiness gate — the Dockerfile HEALTHCHECK,
    # compose's `depends_on`, and CI's wait loops — so it has to be able to fail.
    # An earlier version answered `200 {"status": "degraded", "db": false}` while
    # the database was unreachable, so `curl -fsS` reported a stack with no schema
    # and no app role as healthy, and the real failure surfaced several steps later
    # as a confusing authentication error from whatever ran first.
    #
    # The exception is logged rather than returned: this route is unauthenticated,
    # so it says nothing about *why* the database is unreachable.
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        log.warning("healthz_db_unreachable", error=repr(exc))
        raise HTTPException(status_code=503, detail="database unreachable") from exc
    return {"status": "ok", "db": True}
