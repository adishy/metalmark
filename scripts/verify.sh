#!/usr/bin/env bash
#
# Every gate for this repo, behind one entry point.
#
#   ./scripts/verify.sh                 # all of them, in order
#   ./scripts/verify.sh pytest lint     # just these
#   ./scripts/verify.sh --list          # what exists
#
# The gates are the same checks CI runs, so a green run here and a green run
# there mean the same thing. Two of them (contract, walkthrough) only existed
# inside .github/workflows/ci.yml before; they live in scripts/ now and both
# callers use the same files.
#
# Prerequisites: Docker, and the stack up in the compose project:
#
#   docker compose up -d --build
#   docker compose run --rm api alembic upgrade head
#   docker compose run --rm -e METALMARK_SEED_EMAIL=owner@example.com \
#     -e METALMARK_SEED_PASSWORD=devpassword123 -e METALMARK_SEED_HOUSEHOLD=Home \
#     api python -m app.seed --demo
#
# `walkthrough` and `contract` need that seeded stack. `lint`, `pytest` and
# `frontend` need the containers running but no particular data. `pytest` uses a
# single shared test database, so do not run two of these at once.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Must match the @playwright/test version in frontend/package.json, or the
# committed *-linux.png baselines will not match what the image renders.
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.63.0-noble"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-metalmark}"
NETWORK="${COMPOSE_PROJECT}_default"

GATES=(lint pytest frontend contract e2e walkthrough)
RESULTS=()
FAILED=0

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
usage: scripts/verify.sh [--list] [gate ...]

gates (default: all, in this order):
  lint         ruff
  pytest       backend suite against a real Postgres
  frontend     tsc --noEmit, vitest, production build
  contract     protected routes 401 unauthenticated; openapi.yaml vs the live spec
  e2e          Playwright against the compose stack (in the pinned image)
  walkthrough  M1a paths the Playwright suite does not reach, over real HTTP
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --list)    printf '%s\n' "${GATES[@]}"; exit 0 ;;
esac

if [ "$#" -gt 0 ]; then
  REQUESTED=("$@")
  for g in "${REQUESTED[@]}"; do
    case " ${GATES[*]} " in *" $g "*) ;; *) die "unknown gate: $g (try --list)";; esac
  done
else
  REQUESTED=("${GATES[@]}")
fi

command -v docker >/dev/null || die "docker not found"

# `docker compose exec` needs the service running; say so plainly rather than
# letting every gate fail one at a time with the same message.
require_stack() {
  docker compose ps --status running --services 2>/dev/null | grep -qx api \
    || die "the api container is not running. Start the stack first:
  docker compose up -d --build"
}

run_gate() {
  local name="$1"; shift
  say "$name"
  local start=$SECONDS
  if "$@"; then
    RESULTS+=("PASS  $name ($((SECONDS - start))s)")
  else
    RESULTS+=("FAIL  $name ($((SECONDS - start))s)")
    FAILED=1
  fi
}

# --------------------------------------------------------------------- gates

gate_lint() {
  require_stack
  docker compose exec -T api ruff check .
}

gate_pytest() {
  require_stack
  # METALMARK_TEST_PG_HOST=db: the suite reaches Postgres over the compose
  # network, not localhost. One shared test DB — never run two at once.
  docker compose exec -e METALMARK_TEST_PG_HOST=db -T api python -m pytest -q
}

gate_frontend() {
  require_stack
  # node_modules in the web container is a Linux install, so these must run
  # there rather than on the host.
  docker compose exec -T web sh -c "npm run typecheck && npm run test && npm run build"
}

gate_contract() {
  require_stack
  local fail=0

  # GET only, deliberately. /import is absent because its routes are all POSTs
  # (a GET there is a 404, not a 401, so it proves nothing); the multipart routes
  # are covered in-process by backend/tests/integration/test_imports.py.
  for path in /accounts /transactions /categories /reports/net-worth /owners \
              /household /rules /fx-rates; do
    # `|| true`: on a refused connection curl exits non-zero and `set -e` would
    # abort the whole run instead of reporting the gate as failed.
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}" || true)
    printf 'GET %-24s -> %s\n' "$path" "$code"
    if [ "$code" != "401" ]; then
      warn "  expected 401 for ${path}, got ${code}"
      fail=1
    fi
  done

  docker compose cp contracts/openapi.yaml api:/tmp/checked-openapi.yaml >/dev/null
  docker compose cp scripts/openapi_drift_check.py api:/tmp/openapi_drift_check.py >/dev/null
  docker compose exec -T api python /tmp/openapi_drift_check.py /tmp/checked-openapi.yaml || fail=1

  return $fail
}

gate_e2e() {
  require_stack
  docker run --rm --network "$NETWORK" \
    -e E2E_BASE_URL=http://web:5173 -e CI=1 \
    -v "$ROOT/frontend":/work -w /work \
    "$PLAYWRIGHT_IMAGE" \
    sh -c "npm ci && npx playwright test"
}

gate_walkthrough() {
  require_stack
  # Inside the compose network the api is `api`, not localhost — and the backend
  # image already has httpx (it installs the dev extra), so there is nothing to
  # set up. scripts/ is outside the api bind-mount, hence the extra volume.
  docker compose run --rm -T \
    -e WALKTHROUGH_BASE_URL=http://api:8000 \
    -e WALKTHROUGH_EMAIL="${WALKTHROUGH_EMAIL:-owner@example.com}" \
    -e WALKTHROUGH_PASSWORD="${WALKTHROUGH_PASSWORD:-devpassword123}" \
    -v "$ROOT/scripts":/scripts:ro \
    api python /scripts/walkthrough.py
}

# --------------------------------------------------------------------- main

for name in "${REQUESTED[@]}"; do
  run_gate "$name" "gate_$name" || true
done

printf '\n%s\n' "--------------------------------------------"
printf '%s\n' "${RESULTS[@]}"
printf '%s\n' "--------------------------------------------"

if [ "$FAILED" -ne 0 ]; then
  printf '\033[31mSOME GATES FAILED\033[0m\n'
  exit 1
fi
printf '\033[32mALL GATES PASSED\033[0m\n'
