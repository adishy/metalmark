FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# uv for dependency management (not pip).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Reproducible install from the lock, into the system environment (so the dev
# bind-mount over /app does not shadow installed deps). Layer-cached.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --extra dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt

COPY . .
# Editable install of the app package itself (deps already installed above).
RUN uv pip install --system --no-deps -e .

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
