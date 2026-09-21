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
# Prerequisites: Docker, and the stack up in the compose project. One command does
# all of it, and it is the same reset that runs after the mutating gates:
#
#   ./scripts/verify.sh reset
#
# `walkthrough` and `contract` need that seeded stack. `lint`, `pytest` and
# `frontend` need the containers running but no particular data. `pytest` uses a
# single shared test database, so do not run two of these at once.
#
# `e2e` and `walkthrough` claim a bank, so they run the stack on the fake provider
# (see `use_fake_provider`) and the reset afterwards puts the compose default back
# — the stack you are left holding is the one a real setup token works against.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Must match the @playwright/test version in frontend/package.json, or the
# committed *-linux.png baselines will not match what the image renders.
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.63.0-noble"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-metalmark}"
NETWORK="${COMPOSE_PROJECT}_default"

GATES=(lint pytest frontend contract drill e2e walkthrough reset)
RESULTS=()
FAILED=0
MUTATED=0
# Every mutating gate runs against the same dev database, which is demo data plus
# whatever the last run left behind. `reset` is how it gets back to demo data.
MUTATING=" e2e walkthrough "

# Seeded by `reset`; the same credentials CI seeds and the walkthrough logs in with.
SEED_EMAIL="${SEED_EMAIL:-owner@example.com}"
SEED_PASSWORD="${SEED_PASSWORD:-devpassword123}"
SEED_HOUSEHOLD="${SEED_HOUSEHOLD:-Home}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  # Quoted delimiter: the text below is prose, and backticks in an unquoted heredoc
  # are command substitution — `reset` here would run /usr/bin/reset and reset the
  # terminal instead of printing.
  cat <<'EOF'
usage: scripts/verify.sh [--list] [gate ...]

gates (default: all, in this order):
  lint         ruff
  pytest       backend suite against a real Postgres
  frontend     tsc --noEmit, vitest, production build
  contract     protected routes 401 unauthenticated; openapi.yaml vs the live spec
  drill        back up the live database and restore it into a scratch one
  e2e          Playwright against the compose stack (in the pinned image)
  walkthrough  M1a paths the Playwright suite does not reach, over real HTTP
  reset        drop the volume, migrate, seed demo data

e2e and walkthrough write to the dev database, and the seed never deletes, so
`reset` runs automatically after them. The database is demo data again when the
run ends — and therefore when the next one starts. Pass `reset` explicitly to
reset without running anything else.
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
#
# The worker is checked alongside the api, and for a sharper reason than
# tidiness: it is the one service whose absence nothing downstream reports. A
# stack with a dead worker serves every page and accepts every "Sync now", so
# `e2e` and `walkthrough` fail minutes later on a timeout that points at the
# browser. One line here is the difference between that and knowing.
require_stack() {
  local running
  running=$(docker compose ps --status running --services 2>/dev/null || true)
  for service in api worker; do
    grep -qx "$service" <<<"$running" || die "the ${service} container is not running. Start the stack first:
  docker compose up -d --build"
  done
}

# The provider the running api holds, as the container itself reports it.
#
# `exec` rather than reading `.env` or the compose file, because those say what
# the *next* `up` would use — and the question here is what the process answering
# requests is actually running. Silent on an error (a restarting container has no
# answer) and empty when the variable is unset, both of which are "not the value
# we want" rather than a special case.
current_provider() {
  docker compose exec -T api printenv METALMARK_SIMPLEFIN_PROVIDER 2>/dev/null | tr -d '\r'
}

# Put the running stack on the fake provider, for the two gates whose subject is
# a *claim*.
#
# CI never has to do this: the `e2e` job sets `METALMARK_SIMPLEFIN_PROVIDER: fake`
# in its own `env:` and every `up` in that job inherits it. A local run is handed
# whatever stack was last started, and both of these gates POST a made-up setup
# token to `/connections/claim`. On the real provider that token is decoded as a
# bridge claim URL and refused — "setup token is not valid base64 of a claim URL"
# — and the two gates then fail in the two ways that hide it best:
#
#   * `sync.spec.ts` fails three tests on a message about the *token*, which
#     points at the spec rather than at the stack it was run against.
#   * the walkthrough **skips** its whole bank-sync section and still exits 0, so
#     the gate reports PASS in two seconds with the M2 vertical unexercised. A
#     green gate covering nothing is the worse of the two.
#
# `sync.spec.ts` says as much in its header and tells the reader to
# `METALMARK_SIMPLEFIN_PROVIDER=fake docker compose up -d`, which is right and is
# exactly the step a script exists to perform.
#
# Deliberately **not** exported for the whole run. `verify.sh` ends with a reset,
# and the stack it leaves behind is the one a person is holding: leaving them on
# the fake would mean the first real setup token they paste into the UI becomes a
# fake connection full of demo data that looks like it worked. The provider is
# switched for the gates that need it, and the reset puts the default back.
use_fake_provider() {
  if [ "$(current_provider)" = "fake" ]; then return 0; fi
  printf '  switching the stack to METALMARK_SIMPLEFIN_PROVIDER=fake\n'
  METALMARK_SIMPLEFIN_PROVIDER=fake docker compose up -d api worker || return 1
  # `up -d` returns as soon as the new containers are started and the old api
  # answers /healthz until it is replaced, so a health wait alone would pass
  # against the very container this call exists to get rid of. Asking the
  # container for the variable is the check that cannot race: it cannot answer
  # `fake` before it is the one that will serve the tests.
  local i
  for i in $(seq 1 60); do
    if [ "$(current_provider)" = "fake" ] && curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  warn "  the api never came up on the fake provider"
  return 1
}

run_gate() {
  local name="$1"; shift
  case "$MUTATING" in *" $name "*) MUTATED=1 ;; esac
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
  # Kept identical to the list in .github/workflows/ci.yml — the whole point of
  # this file is that a green run here and a green run there mean the same thing,
  # and a path added to one of them is the drift that quietly breaks that.
  for path in /accounts /transactions /categories /reports/net-worth /owners \
              /household /rules /fx-rates \
              /connections /connections/runs /connections/jobs \
              /connections/notifications; do
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

gate_drill() {
  require_stack
  # A backup nobody has restored from is a belief, not a capability, so the drill
  # is a gate rather than a script beside the backup one. It reads the live
  # database and writes only to its own scratch database, which it drops on the
  # way out — so it needs no `reset` afterwards and is not in MUTATING.
  "$ROOT/scripts/restore_drill.sh"
}

gate_e2e() {
  require_stack
  use_fake_provider || return 1
  docker run --rm --network "$NETWORK" \
    -e E2E_BASE_URL=http://web:5173 -e CI=1 \
    -v "$ROOT/frontend":/work -w /work \
    "$PLAYWRIGHT_IMAGE" \
    sh -c "npm ci && npx playwright test"
}

gate_walkthrough() {
  require_stack
  # Its section claims a bank too, and it gets the same provider `e2e` left in
  # place — this call is a no-op unless the gate is run on its own.
  use_fake_provider || return 1
  # Inside the compose network the api is `api`, not localhost — and the backend
  # image already has httpx (it installs the dev extra), so there is nothing to
  # set up. scripts/ is outside the api bind-mount, hence the extra volume.
  docker compose run --rm -T \
    -e WALKTHROUGH_BASE_URL=http://api:8000 \
    -e WALKTHROUGH_EMAIL="${WALKTHROUGH_EMAIL:-$SEED_EMAIL}" \
    -e WALKTHROUGH_PASSWORD="${WALKTHROUGH_PASSWORD:-$SEED_PASSWORD}" \
    -v "$ROOT/scripts":/scripts:ro \
    api python /scripts/walkthrough.py
}

# Back to demo data. Deliberately a volume drop rather than deleting rows: the
# volume is where the app role, the RLS policies and the schema live, so this is
# the only reset that is complete, and it is exactly the path CI takes from a
# fresh checkout — the fresh-volume path stays exercised instead of rotting.
#
# No require_stack: this is the gate that recreates the stack.
gate_reset() {
  docker compose down -v || return 1
  docker compose up -d --build || return 1
  docker compose run --rm api alembic upgrade head || return 1
  # /healthz fails closed, so this loop is a real readiness gate rather than a
  # formality — seeding before the role and schema exist is the failure this
  # ordering exists to prevent.
  local i
  for i in $(seq 1 60); do
    curl -fsS http://localhost:8000/healthz >/dev/null 2>&1 && break
    sleep 2
  done
  if ! curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
    warn "  api never became healthy after reset"
    return 1
  fi
  docker compose run --rm \
    -e METALMARK_SEED_EMAIL="$SEED_EMAIL" \
    -e METALMARK_SEED_PASSWORD="$SEED_PASSWORD" \
    -e METALMARK_SEED_HOUSEHOLD="$SEED_HOUSEHOLD" \
    api python -m app.seed --demo
}

# --------------------------------------------------------------------- main

for name in "${REQUESTED[@]}"; do
  run_gate "$name" "gate_$name" || true
done

# e2e and walkthrough write to the dev database, and the seed is idempotent: it adds
# demo data but never removes what a test left behind. Without this the next run
# starts on top of the last one's debris, and review.spec.ts — which drains a queue —
# is the first thing to break. Resetting at the end means the database holds demo
# data both after a run and before the next one, so there is no state to remember.
# Runs on failure too: a broken run leaves the most debris.
if [ "$MUTATED" -eq 1 ]; then
  wants_reset=0
  for g in "${REQUESTED[@]}"; do
    if [ "$g" = "reset" ]; then wants_reset=1; fi
  done
  if [ "$wants_reset" -eq 0 ]; then
    run_gate reset gate_reset || true
  fi
fi

printf '\n%s\n' "--------------------------------------------"
printf '%s\n' "${RESULTS[@]}"
printf '%s\n' "--------------------------------------------"

if [ "$FAILED" -ne 0 ]; then
  printf '\033[31mSOME GATES FAILED\033[0m\n'
  exit 1
fi
printf '\033[32mALL GATES PASSED\033[0m\n'
