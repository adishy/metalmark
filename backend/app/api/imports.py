"""CSV import routes.

Mounted (in ``app.main``) ahead of the vertical that fills it in, so the route
seam is fixed before parallel work starts and no two workstreams have to edit
``main.py``. Shapes are frozen by PLAN.md Phase 2: ``POST /import/csv/preview``
and ``POST /import/csv/commit``.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/import", tags=["import"])

# The OFX/QFX sibling lives here when Phase 2 grows it (ADR: defused XML).
