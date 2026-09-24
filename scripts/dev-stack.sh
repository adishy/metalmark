#!/usr/bin/env bash
#
# A throwaway demo stack for looking at the app: screenshots, e2e specs, poking at a
# page. Not the CI gates — those are ./scripts/verify.sh, on its own compose project.
#
#   ./scripts/dev-stack.sh up      # build, start, migrate, seed the demo household
#   ./scripts/dev-stack.sh reset   # drop the database and seed again (e.g. after e2e)
#   ./scripts/dev-stack.sh down    # stop and delete everything, volumes included
#
# The web app is on http://127.0.0.1:5173 (owner@example.com / devpassword123), and the
# compose network is "<project>_default" for the Playwright container to join
# (frontend/AGENTS.md). Everything it seeds is the demo household — made-up names and
# numbers, safe for README screenshots.
#
# Images are built with `docker build` rather than `compose build` so the build can use
# the host's network: on some hosts BuildKit's own resolver cannot reach PyPI/npm and
# `compose build` fails with a DNS error. Set BUILD_NETWORK=default to build normally.
#
# Env: MM_PROJECT (default mm-dev), BUILD_NETWORK (default host).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${MM_PROJECT:-mm-dev}"
NET="${BUILD_NETWORK:-host}"
cd "$ROOT"

compose() { docker compose -p "$PROJECT" "$@"; }

ensure_config() {
  # The dev stack's two local, gitignored inputs. Created once, never overwritten.
  [ -f .env ] || cp .env.example .env
  if [ ! -f secrets/metalmark_secret_key ]; then
    mkdir -p secrets
    python3 -c "import secrets; print(secrets.token_urlsafe(48))" > secrets/metalmark_secret_key
  fi
}

build() {
  echo "[1/4] building images (network: $NET)"
  docker build --network "$NET" -q -t "$PROJECT-api" ./backend >/dev/null
  docker tag "$PROJECT-api" "$PROJECT-worker"
  docker build --network "$NET" -q --target dev -t "$PROJECT-web" ./frontend >/dev/null
}

seed() {
  echo "[3/4] migrating"
  compose run --rm api alembic upgrade head >/dev/null
  echo "[4/4] seeding the demo household"
  compose run --rm \
    -e METALMARK_SEED_EMAIL=owner@example.com \
    -e METALMARK_SEED_PASSWORD=devpassword123 \
    -e METALMARK_SEED_HOUSEHOLD=Home \
    api python -m app.seed --demo >/dev/null
}

wait_for_db() {
  for _ in $(seq 1 30); do
    compose exec -T db pg_isready -q >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "database did not come up" >&2
  return 1
}

case "${1:-up}" in
  up)
    ensure_config
    build
    echo "[2/4] starting $PROJECT"
    compose up -d >/dev/null
    wait_for_db
    seed
    echo "up: http://127.0.0.1:5173  (network ${PROJECT}_default)"
    ;;
  reset)
    compose down -v >/dev/null 2>&1 || true
    compose up -d >/dev/null
    wait_for_db
    seed
    echo "reset: demo household reseeded"
    ;;
  down)
    compose down -v
    docker rmi "$PROJECT-api" "$PROJECT-worker" "$PROJECT-web" >/dev/null 2>&1 || true
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 2
    ;;
esac
