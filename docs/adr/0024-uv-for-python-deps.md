# ADR 0024: uv for Python dependency management

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0003 (boring stack), backend/Dockerfile, backend/uv.lock

## Context

The backend needs reproducible, fast dependency installs in Docker and for local dev. The project owner
asked to use **uv** (Astral) rather than pip.

## Decision

Use **uv** for all Python dependency management. Dependencies and their lock live in `backend/pyproject.toml`
+ `backend/uv.lock`. The image installs **from the lock** into the *system* environment:
`uv export --frozen --extra dev --no-emit-project | uv pip install --system -r -`, then an editable
`uv pip install --system --no-deps -e .` for the app package.

Installing `--system` (not into a project `.venv`) is deliberate: the dev compose bind-mounts `./backend`
over `/app`, which would shadow a `/app/.venv`. Keeping deps in the system site-packages (outside `/app`)
survives the mount, and `uvicorn`/`pytest`/`alembic` stay on `PATH` so compose commands are unchanged.

## Consequences

- **Positive:** fast, reproducible builds from a committed lock; single toolchain; no pip.
- **Negative / costs:** `uv sync`'s managed-venv workflow isn't used in the container (bind-mount shadowing);
  local devs who prefer a venv can still `uv sync` in `backend/`. Lock must be regenerated (`uv lock`) when
  `pyproject.toml` changes.
