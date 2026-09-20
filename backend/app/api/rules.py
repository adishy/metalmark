"""Rules engine routes.

Mounted (in ``app.main``) ahead of the vertical that fills it in, so the route
seam is fixed before parallel work starts and no two workstreams have to edit
``main.py``. Shapes are frozen by PLAN.md Phase 2: ``GET/POST /rules``,
``PATCH/DELETE /rules/{rule_id}``, ``POST /rules/apply``.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/rules", tags=["rules"])
