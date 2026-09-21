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

# The deployment's own compose project, so the `prod` gate can boot a full
# deployment *beside* the dev stack instead of replacing it — and on its own
# database volume, so what it tests is the fresh-install path rather than an
# upgrade of whatever dev left behind.
PROD_PROJECT="${PROD_PROJECT_NAME:-metalmark-prod}"
# Must track deploy/docker-compose.yaml's own defaults, or the `prod` gate probes a door
# the deployment never opened.
PROD_HTTPS_PORT="${METALMARK_HTTPS_PORT:-8790}"
PROD_HTTP_PORT="${METALMARK_HTTP_PORT:-8791}"

GATES=(secrets lint pytest frontend contract drill e2e walkthrough prod reset)
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
  secrets      no credentials in the worktree, in any commit, or in the image
  lint         ruff
  pytest       backend suite against a real Postgres
  frontend     tsc --noEmit, vitest, production build
  contract     protected routes 401 unauthenticated; openapi.yaml vs the live spec
  drill        back up the live database and restore it into a scratch one
  e2e          Playwright against the compose stack (in the pinned image)
  walkthrough  M1a paths the Playwright suite does not reach, over real HTTP
  prod         boot the deployment in its own project and probe it in a browser
  reset        drop the volume, migrate, seed demo data

`secrets` needs no stack and no data — it reads files. It is first because it is
the cheapest and because the accident it catches happens at `git add`, long
before anything is started.

e2e and walkthrough write to the dev database, and the seed never deletes, so
`reset` runs automatically after them. The database is demo data again when the
run ends — and therefore when the next one starts. Pass `reset` explicitly to
reset without running anything else.

`prod` needs no stack running and touches no dev data: it boots deploy/docker-compose.yaml
— the standalone deployment file, the same one a target machine fetches — under
its own project name (metalmark-prod), builds the production images, waits for
the file's own secret generation and migration, seeds a fresh volume, and checks
what nginx and Caddy serve. Then it does the same on the *other* kind of backing
store: `METALMARK_DB_DIR`, `METALMARK_SECRETS_DIR` and `METALMARK_CADDY_DIR`
pointed at directories under a scratch tree in this checkout, booted again from
nothing, asserting that the ledger, the credentials and Caddy's CA really are on
those host paths, that a restart reuses them, and cleaning up afterwards through
a container because the directories are no longer readable by your own account
(ADR-0040). It is the only gate that costs a full image build.

  PROD_IMAGE_PREFIX=ghcr.io/adishy/metalmark PROD_IMAGE_TAG=sha-abc123
  ./scripts/verify.sh prod

With those set, `prod` boots the published images instead of building from the
checkout — which is how CI verifies the artifact it just pushed rather than a
second build of the same source.
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

gate_secrets() {
  # No require_stack, deliberately: this one reads files rather than containers,
  # so it is the only gate that runs on a bare checkout with nothing up — which
  # is also the only time it is worth running, since the accident it catches
  # happens at `git add`, before anything has been started. The script exits
  # non-zero on its own, and prints `path:line` and the rule name, never the
  # match.
  "$ROOT/scripts/secret_scan.sh"
}

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

# ------------------------------------------------------- the deployment gate

# The deployment is one standalone file — deploy/docker-compose.yaml — rather than an
# overlay on the dev stack, so this gate boots exactly what a target machine
# boots. Only two things differ, and both are stated here rather than in the
# file: the project name, so this stands up *beside* the dev stack instead of
# replacing it (the file deliberately has no `name:`, so a checkout running it by
# hand lands on `deploy` and cannot collide either), and the images, for CI.
#
# `PROD_IMAGE_PREFIX`/`PROD_IMAGE_TAG` are what make the publish job mean
# something: with them set, this gate boots the images that were just pushed to
# the registry rather than building the same source a second time, so it tests
# the artifact a stranger would pull. Unset, compose uses the `ghcr.io` refs in
# the file, and `--build` still builds from the checkout — which is what a
# developer running this gate on their own branch needs.
if [ -n "${PROD_IMAGE_PREFIX:-}" ]; then
  export METALMARK_API_IMAGE="${PROD_IMAGE_PREFIX}-api:${PROD_IMAGE_TAG:-latest}"
  export METALMARK_WEB_IMAGE="${PROD_IMAGE_PREFIX}-web:${PROD_IMAGE_TAG:-latest}"
fi

prod() {
  docker compose -p "$PROD_PROJECT" -f deploy/docker-compose.yaml "$@"
}

# Which artifact is under test is the one real fork in this gate, so it lives in
# one place and both halves boot through it. Normally it is a build of the
# checkout: compose builds only when asked, so `--build` is what selects it (with
# `image:` and `build:` both present, a plain `up` pulls and never touches the
# builder — measured). When PROD_IMAGE_PREFIX names published images the build is
# the one thing that must *not* happen: it would replace the artifact under test
# with a local build of the same source, and the gate would pass without ever
# having pulled what a stranger pulls.
prod_up() {
  if [ -n "${PROD_IMAGE_PREFIX:-}" ]; then
    prod pull --quiet api worker web || return 1
    prod up -d || return 1
  else
    prod up -d --build || return 1
  fi
}

# Both doors, because Caddy is a separate container that can still be starting
# when nginx is already serving — and a race there would read as "TLS is broken".
# `/healthz` fails closed in the app: it checks the app role rather than only that
# the process is up, and nginx proxies it, so a 200 here has already exercised the
# web container, the proxy and the database.
prod_wait_healthy() {
  local base="http://127.0.0.1:${PROD_HTTP_PORT}" tls="https://localhost:${PROD_HTTPS_PORT}"
  local i code
  for i in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$base/healthz" || true)
    if [ "$code" = "200" ]; then
      if curl -sk -o /dev/null -w '%{http_code}' "$tls/healthz" | grep -q 200; then return 0; fi
    fi
    sleep 2
  done
  warn "  the deployment never became healthy (last /healthz: ${code:-none})"
  return 1
}

# One mount, read off the running container rather than off the file: a resolved
# config is what compose *intends*, and this is what it did.
expect_mount() {  # <container> <container-path> <"type source rw|ro"> <label>
  local got
  got=$(docker inspect -f \
    "{{range .Mounts}}{{if eq .Destination \"$2\"}}{{.Type}} {{.Source}} {{if .RW}}rw{{else}}ro{{end}}{{end}}{{end}}" \
    "$1" 2>/dev/null || true)
  printf '  %-4s %-52s %s\n' "$([ "$got" = "$3" ] && echo ok || echo FAIL)" "$4" "${got:-<absent>}"
  [ "$got" = "$3" ]
}

# One assertion per line, in the same shape scripts/prod_probe.cjs prints, so the
# two halves of the `prod` gate read as one list of what was actually checked.
# Each returns non-zero on mismatch, for the caller to fold into its `fail`.
expect_status() {  # <url> <code> <label>
  local got
  got=$(curl -sk -o /dev/null -w '%{http_code}' "$1" || true)
  printf '  %-4s %-52s %s\n' "$([ "$got" = "$2" ] && echo ok || echo FAIL)" "$3" "$got"
  [ "$got" = "$2" ]
}

expect_header() {  # <url> <header> <value> <label>
  local got
  got=$(curl -skI "$1" | grep -i "^$2:" | tr -d '\r' | sed 's/^[^:]*: *//' || true)
  printf '  %-4s %-52s %s\n' "$([ "$got" = "$3" ] && echo ok || echo FAIL)" "$4" "${got:-<absent>}"
  [ "$got" = "$3" ]
}
expect_cache() { expect_header "$1" 'cache-control' "$2" "$3"; }

# A status code answers "did something reply", not "did the app reply". A 200
# from an nginx default page, a stale image, or a proxy that dropped the body
# all pass `expect_status` and none of them are the SPA — so the two routes that
# are supposed to be the app are checked for the app's own mount point as well.
# `id="root"` is in frontend/index.html and nothing else this stack serves.
expect_body() {  # <url> <fixed-string> <label>
  local body ok
  body=$(curl -sk "$1" 2>/dev/null || true)
  grep -qF -- "$2" <<<"$body" && ok=ok || ok=FAIL
  printf '  %-4s %-52s %s\n' "$ok" "$3" \
    "$(grep -oF -- "$2" <<<"$body" | head -1 || true)"
  [ "$ok" = ok ]
}

# `-k` throughout, and not as a shortcut: the TLS certificate is issued by
# Caddy's own CA, which by design nothing on this host trusts. What is being
# checked is that Caddy terminates TLS and proxies, not that a browser would
# accept the CA — that part is the operator's, and the README says how.

# A deployment, from nothing. Run under its own compose project so it sits beside
# the dev stack rather than replacing it.
#
# What this proves that no other gate can: that a *built* frontend registers a
# service worker. `vite-plugin-pwa` emits `sw.js` only for a production build, so
# on the dev stack `navigator.serviceWorker.ready` never settles and the app
# cannot display a notification at all (ADR-0037) — which is the entire reason
# the prod image exists, and until now the one claim in this repo with no test
# under it. Everything else here is a deployment that boots: a fresh volume,
# migrations, the seed, nginx, the /api proxy and Caddy, none of which any other
# gate exercises.
gate_prod() {
  # A guard rather than a formality: the teardown is `down -v`, and `-v` on the
  # dev project would drop the database every other gate has just been using.
  # Nothing should be able to name it.
  if [ "$PROD_PROJECT" = "$COMPOSE_PROJECT" ]; then
    warn "  PROD_PROJECT_NAME must differ from COMPOSE_PROJECT_NAME (${COMPOSE_PROJECT})"
    return 1
  fi

  # Resolved and discarded first, because the deployment file is also the only
  # place in the repo where a Caddyfile lives inside YAML: an indentation mistake
  # in that block scalar is valid YAML and invalid Caddyfile, and it would
  # otherwise surface minutes later as a container that restarts forever with a
  # config error in a log nobody has looked at yet.
  if ! prod config >/dev/null; then
    warn "  deploy/docker-compose.yaml does not resolve"
    return 1
  fi

  # The default half has to be testing the default. This gate inherits whatever
  # environment it is run from, and `docker compose -f deploy/docker-compose.yaml` also
  # reads `deploy/.env` — the very file the README now tells an operator to create.
  # Either one naming a path would leave the named-volume install, the one thing
  # this half exists to keep working, silently untested.
  #
  # Set to *empty* rather than unset, and that is the point: `${VAR:-default}`
  # reads an empty value as absent, and an exported value outranks `deploy/.env`.
  # Unsetting would defeat the first source and not the second.
  #
  # `METALMARK_HTTP_BIND` is here for the other reason of the two: not a default
  # that would go untested, but a probe that would go to the wrong address. A
  # caller whose environment (or `deploy/.env`) named one interface would have the
  # gate dialling 127.0.0.1 on a door that is not listening there, and it would
  # read as a deployment that never became healthy.
  export METALMARK_DB_DIR= METALMARK_SECRETS_DIR= METALMARK_CADDY_DIR= METALMARK_HTTP_BIND=

  # From nothing, every time: the gate's subject is a deployment booting, and a
  # stack that is already up answers a different question.
  prod down -v --remove-orphans >/dev/null 2>&1 || true

  # The checks leave containers running if they abort, so the status is recorded
  # and the teardown happens either way. The storage half leaves three directories
  # behind that `-v` cannot remove, so it cleans up after itself.
  local rc=0
  prod_checks || rc=1
  prod_path_checks || rc=1
  prod down -v --remove-orphans >/dev/null 2>&1 || true
  return $rc
}

prod_checks() {
  local fail=0
  local base="http://127.0.0.1:${PROD_HTTP_PORT}" tls="https://localhost:${PROD_HTTPS_PORT}"
  local asset jar setcookie authcode web_ip envkey secretfiles dotenv bind

  # No `alembic upgrade head` here any more, and its absence is the assertion: the
  # deployment file generates its own credentials and applies its own migrations,
  # and `up -d` does not return until both have succeeded (they are consumed
  # through `service_completed_successfully`). Running the migration by hand would
  # prove the schema could be applied while leaving the *install* — the thing this
  # gate is for — untested, which is precisely the step a stranger's
  # `docker compose up -d` depends on.
  #
  prod_up || return 1

  if ! prod_wait_healthy; then
    prod logs --tail 40 web api >&2
    return 1
  fi

  prod run --rm \
    -e METALMARK_SEED_EMAIL="$SEED_EMAIL" \
    -e METALMARK_SEED_PASSWORD="$SEED_PASSWORD" \
    -e METALMARK_SEED_HOUSEHOLD="$SEED_HOUSEHOLD" \
    api python -m app.seed --demo >/dev/null || return 1

  # What nginx serves. The two `/api` answers are the same request to the same
  # route as the `contract` gate makes against the dev stack — 401 rather than
  # 404 is what says the prefix was stripped instead of forwarded.
  expect_status "$base/" 200 "the SPA is served at /" || fail=1
  expect_status "$base/transactions" 200 "a client-side route falls back to the SPA" || fail=1
  # …and both are in fact the app, not merely something that answered 200.
  expect_body "$base/" 'id="root"' "…and / is the built index.html" || fail=1
  expect_body "$base/transactions" 'id="root"' "…as is the fallback for a client-side route" || fail=1
  expect_status "$base/healthz" 200 "the api is reachable through the web container" || fail=1
  expect_status "$base/api/accounts" 401 "…and /api reaches it with the prefix stripped" || fail=1
  expect_status "$tls/" 200 "Caddy terminates TLS and proxies the SPA" || fail=1
  expect_status "$tls/api/accounts" 401 "…and /api in front of it" || fail=1

  # The cache headers are load-bearing rather than tidiness. A cached `sw.js`
  # keeps running the old worker and the old precache manifest until the entry
  # expires, which makes a deploy look like it did not ship.
  expect_cache "$base/sw.js" "no-cache" "the service worker is revalidated" || fail=1
  expect_cache "$base/index.html" "no-cache" "index.html is revalidated" || fail=1

  # The Caddyfile has carried these three lines since before there was a Caddy
  # service to run them, so until this gate nothing had ever checked they are
  # served — and a header that is configured but not sent looks exactly like one
  # that is. Over TLS, because Caddy is where they are set.
  expect_header "$tls/" "x-content-type-options" "nosniff" \
    "responses are nosniffed" || fail=1
  expect_header "$tls/" "x-frame-options" "DENY" \
    "…and cannot be framed" || fail=1
  expect_header "$tls/" "referrer-policy" "strict-origin-when-cross-origin" \
    "…and do not leak the path to other origins" || fail=1

  asset=$(curl -s "$base/" | grep -o '/assets/[A-Za-z0-9._-]*\.js' | head -1 || true)
  if [ -n "$asset" ]; then
    expect_cache "$base$asset" "public, max-age=31536000, immutable" \
      "a content-hashed asset is immutable" || fail=1
  else
    printf '  %-4s %-52s\n' "FAIL" "index.html names a hashed asset"
    fail=1
  fi

  # The login round trip, over TLS. This is also the check that the api is
  # running as `prod`: `METALMARK_ENV=prod` is what sets the session cookie's
  # Secure flag, and it is the whole difference between a deployment and the dev
  # api behind nginx — a distinction a `.env` file silently collapses if the
  # overlay interpolates `${METALMARK_ENV}` instead of stating it.
  jar=$(mktemp)
  setcookie=$(curl -k -s -D - -o /dev/null -c "$jar" -X POST "$tls/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$SEED_EMAIL\",\"password\":\"$SEED_PASSWORD\"}" \
    | grep -i '^set-cookie:' | tr -d '\r' || true)
  # Printed from the first `;` onwards — the flags, never the value. A session
  # token in a build log is a session token in a build log, even one minted
  # against a demo household, and this repo goes to some trouble not to leak the
  # other credential it handles. The same discipline costs one parameter
  # expansion here.
  printf '  %-4s %-52s %s\n' \
    "$(grep -qi 'secure' <<<"$setcookie" && echo ok || echo FAIL)" \
    "the session cookie is Secure" "${setcookie:+<value redacted>}${setcookie#*;}"
  grep -qi 'secure' <<<"$setcookie" || fail=1

  authcode=$(curl -k -s -o /dev/null -w '%{http_code}' -b "$jar" "$tls/api/accounts" || true)
  printf '  %-4s %-52s %s\n' \
    "$([ "$authcode" = "200" ] && echo ok || echo FAIL)" \
    "a logged-in session reads accounts over TLS" "$authcode"
  [ "$authcode" = "200" ] || fail=1
  rm -f "$jar"

  # Where the credentials are, and where they are not. scripts/secret_scan.sh
  # makes three claims about secrets; two of them are about files in the repo and
  # it checks those itself, and the third is about the *image*, which is not
  # answerable by reading anything — only by asking a container built from it.
  # That is this block.
  #
  # It matters because the api image is built with `COPY . .`: it is published to
  # a public registry (ADR-0039), so it ships exactly what survives
  # backend/.dockerignore. A `.env` that got past that file would be a credential
  # in a registry *and* a configuration the deployment silently inherits from
  # whoever happened to build the image — which is the failure the file's first
  # line exists to prevent, and the reason this asserts it rather than trusting it.
  dotenv=$(prod exec -T api sh -c 'ls -a /app/.env 2>/dev/null' | tr -d '\r' || true)
  printf '  %-4s %-52s %s\n' \
    "$([ -z "$dotenv" ] && echo ok || echo FAIL)" \
    "the api image carries no .env" "${dotenv:-<absent>}"
  [ -z "$dotenv" ] || fail=1

  # The key is a file in a volume, never an environment variable: an environment
  # variable is readable by anything that can call the Docker API, and lands in
  # `docker inspect` output and in crash dumps. `printenv` rather than a shell
  # expansion so an *empty* variable and an *unset* one both read as absent.
  envkey=$(prod exec -T api printenv METALMARK_SECRET_KEY 2>/dev/null | tr -d '\r' || true)
  printf '  %-4s %-52s %s\n' \
    "$([ -z "$envkey" ] && echo ok || echo FAIL)" \
    "the key is not an environment variable" "${envkey:+<non-empty>}"
  [ -z "$envkey" ] || fail=1

  # …and it did in fact arrive, which nothing above proves: a Fernet key is read
  # lazily — only when an access URL is first encrypted or decrypted — so a
  # deployment whose secret-init silently wrote nothing would boot, serve, log in
  # and pass every check above, then fail at the one moment that matters. Asserted
  # as a set of names, never as contents.
  secretfiles=$(prod exec -T api sh -c 'ls -1 /secrets' 2>/dev/null | tr -d '\r' | sort | tr '\n' ' ' || true)
  printf '  %-4s %-52s %s\n' \
    "$([ "$secretfiles" = "app_db_password metalmark_secret_key postgres_password " ] && echo ok || echo FAIL)" \
    "all three credentials were generated" "${secretfiles:-<none>}"
  [ "$secretfiles" = "app_db_password metalmark_secret_key postgres_password " ] || fail=1

  # Which interface the plain-HTTP door is published on. Nothing else here can see
  # the difference — every probe above dials 127.0.0.1, which a loopback bind and a
  # `0.0.0.0` bind both answer — so without this line, restoring the old
  # loopback-only default would be a silent change (ADR-0041).
  bind=$(docker inspect -f \
    '{{range $c := .NetworkSettings.Ports}}{{range $c}}{{.HostIp}}:{{.HostPort}}{{end}}{{end}}' \
    "${PROD_PROJECT}-web-1" 2>/dev/null || true)
  printf '  %-4s %-52s %s\n' \
    "$([ "$bind" = "0.0.0.0:${PROD_HTTP_PORT}" ] && echo ok || echo FAIL)" \
    "the plain-HTTP door is published on every interface" "${bind:-<none>}"
  [ "$bind" = "0.0.0.0:${PROD_HTTP_PORT}" ] || fail=1

  # The browser half: the only check anywhere that a built frontend registers a
  # service worker. Mounted from the repo rather than /tmp, because /tmp is not a
  # shared path for every Docker host this runs on.
  web_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "${PROD_PROJECT}-web-1" 2>/dev/null || true)
  if [ -z "$web_ip" ]; then
    warn "  could not find the web container's address"
    return 1
  fi
  # `localhost` is *mapped* to that address rather than navigated to directly: a
  # secure context is decided by the hostname, and `http://web:8080` is not one,
  # so a service worker is refused there. The rule's target has to be a literal
  # address — `MAP localhost web` fails to resolve. Both measured; prod_probe.cjs
  # has the details. `npm ci` output is suppressed because it is not the subject.
  #
  # That `8080` is nginx's port *inside* the container and is deliberately not
  # PROD_HTTP_PORT: this request goes to the web container's own address on the
  # compose network, never through the published host door.
  docker run --rm --network "${PROD_PROJECT}_default" \
    -v "$ROOT/frontend":/work -v "$ROOT/scripts":/probe:ro -w /work \
    "$PLAYWRIGHT_IMAGE" \
    sh -c "npm ci --silent >/dev/null 2>&1 && node /probe/prod_probe.cjs http://localhost:8080 localhost $web_ip" \
    || fail=1

  return $fail
}

# The other half of the deployment gate: the same file, with all three persistent
# locations pointed at host paths (ADR-0040).
#
# This is the only thing anywhere that can prove the override, and it has to be a
# real boot, because every claim is about what the containers do to a host
# directory: that Postgres runs `initdb` into one and then re-owns it, that uid 999
# can read `postgres_password` across a *bind* mount rather than a named volume,
# that Caddy writes its CA there, and that a `down`/`up` with no `-v` leaves the
# cluster and the credentials alone — the property an operator is actually buying
# when they move off the volumes.
prod_path_checks() {
  local fail=0 dir
  # Not /tmp: it is not a shared path for every Docker host this runs on, which is
  # the same reason the browser half above mounts from the repo.
  dir=$(mktemp -d "$ROOT/.prod-paths.XXXXXX")

  # A bind mount has no `-v` to undo it, so this half cleans up after itself — and
  # the removal runs in a container. Once Postgres has initialized the directory it
  # is mode 700 owned by uid 999, so `rm -rf` from this shell stops partway through
  # on a Linux host. That is not an annoyance to route around: it is the cost
  # ADR-0040 records, showing up in the gate that proves the feature.
  prod_paths_cleanup() {
    prod down -v --remove-orphans >/dev/null 2>&1 || true
    docker run --rm --entrypoint rm -v "$dir":/scratch \
      caddy:2-alpine -rf /scratch/db /scratch/secrets /scratch/caddy >/dev/null 2>&1 || true
    rm -rf "$dir" 2>/dev/null || true
    # Back to the default before returning, because an export outlives the
    # function. The caller's own teardown is a bare `prod down -v`, and run with
    # a bind path still exported it would be resolving a different deployment —
    # and would re-create the directories as empty ones on its way past.
    export METALMARK_DB_DIR= METALMARK_SECRETS_DIR= METALMARK_CADDY_DIR=
  }

  # The default half's stack is torn down while the variables are still empty, and
  # the order is the assertion: `prod_checks` left a deployment whose state is
  # named volumes, and `down -v` run with a bind path configured resolves a project
  # that no longer declares them — which orphans the volumes instead of removing
  # them. That is the pruning ADR-0040 records, met here as bookkeeping rather than
  # as a surprise.
  prod down -v --remove-orphans >/dev/null 2>&1 || true

  export METALMARK_DB_DIR="$dir/db" \
         METALMARK_SECRETS_DIR="$dir/secrets" \
         METALMARK_CADDY_DIR="$dir/caddy"

  if ! prod_up; then prod_paths_cleanup || true; return 1; fi
  if ! prod_wait_healthy; then
    warn "  the deployment never became healthy on host paths"
    prod logs --tail 40 db api >&2
    prod_paths_cleanup || true
    return 1
  fi

  # Where each container path is actually backed, read off the containers rather
  # than off the file. The `ro` on the secrets mount is asserted rather than
  # assumed: it is the one mount whose read-only-ness is a security property, and
  # an edit that dropped it would be invisible everywhere else.
  expect_mount "${PROD_PROJECT}-db-1" /var/lib/postgresql/data \
    "bind $dir/db rw" "the database is on the host directory" || fail=1
  expect_mount "${PROD_PROJECT}-api-1" /secrets \
    "bind $dir/secrets ro" "…and the credentials, still read-only" || fail=1
  expect_mount "${PROD_PROJECT}-caddy-1" /data \
    "bind $dir/caddy rw" "…and Caddy's CA" || fail=1

  # Asserted on the *host*, because that is the operator's actual question — "is my
  # data where I pointed it" — asked where they would look. `PG_VERSION` is written
  # by initdb and by nothing else, so its presence is the whole claim that a
  # cluster was built here rather than in a volume.
  local name missing=""
  for name in metalmark_secret_key postgres_password app_db_password; do
    [ -f "$dir/secrets/$name" ] || missing="$missing $name"
  done
  printf '  %-4s %-52s %s\n' "$([ -z "$missing" ] && echo ok || echo FAIL)" \
    "all three credentials are on the host" "${missing:-<all present>}"
  [ -z "$missing" ] || fail=1

  # …but the database directory is read differently, and this cost a red CI run to
  # learn. `[ -f "$dir/db/PG_VERSION" ]` is true on Docker Desktop and false on a
  # Linux runner *for the same directory*: Desktop maps the ownership Postgres sets
  # back to the invoking user, and Linux does not. The file is there in both cases;
  # what differs is the account reading it. Measured on the runner — uid 1001,
  # PGDATA `drwx------ postgres postgres` — it surfaced as a bare `<absent>` naming
  # neither the directory nor the permission.
  #
  # That is ADR-0040's first cost met in the gate that proves the feature, so the
  # check moves to the account that pays it: the host directory, mounted into a
  # throwaway root container, which is also how the operator has to reach it. It is
  # deliberately not read out of the running `db` container — this mounts the host
  # path on its own, so it would still report a cluster if the deployment were
  # somehow reading a volume instead. The mode and owner are printed because they
  # are the thing the operator is buying into.
  local hostdb pgver
  hostdb=$(docker run --rm --user 0:0 --entrypoint sh -v "$dir/db":/pgdata:ro caddy:2-alpine \
    -c 'printf "%s " "$(stat -c "%a %u:%g" /pgdata)"; cat /pgdata/PG_VERSION' 2>/dev/null \
    | tr -d '\r' || true)
  pgver="${hostdb##* }"
  printf '  %-4s %-52s %s\n' \
    "$([ "$pgver" = "16" ] && echo ok || echo FAIL)" \
    "Postgres built a cluster on the host" "${hostdb:+mode owner, then PG_VERSION: $hostdb}"
  [ "$pgver" = "16" ] || fail=1

  # The modes are load-bearing rather than cosmetic — 0644 on `postgres_password`
  # is what lets uid 999 read it, and is the reason that one file differs from the
  # other two — and a bind mount is the first backing store where the directory's
  # own permissions become the operator's choice. So the contract is asserted here
  # instead of described.
  local modes
  modes=$(prod exec -T api sh -c 'stat -c "%a %n" /secrets/*' 2>/dev/null | tr -d '\r' | sort | tr '\n' ' ' || true)
  printf '  %-4s %-52s %s\n' \
    "$([ "$modes" = "600 /secrets/app_db_password 600 /secrets/metalmark_secret_key 644 /secrets/postgres_password " ] && echo ok || echo FAIL)" \
    "…with the modes the database needs" "${modes:-<none>}"
  [ "$modes" = "600 /secrets/app_db_password 600 /secrets/metalmark_secret_key 644 /secrets/postgres_password " ] || fail=1

  # What the operator is buying. A `down` with no `-v` and an `up` must not
  # re-initialize the cluster or regenerate the credentials; the inode is the
  # assertion, because a second `initdb` writes a new `PG_VERSION` and cannot
  # preserve the old one. The key is compared and never printed — the same
  # discipline the session cookie above gets, and for the same reason.
  local inode_before inode_after key_before key_after
  inode_before=$(prod exec -T db stat -c %i /var/lib/postgresql/data/PG_VERSION 2>/dev/null | tr -d '\r' || true)
  key_before=$(prod exec -T api sha256sum /secrets/metalmark_secret_key 2>/dev/null | tr -d '\r' | cut -d' ' -f1 || true)

  prod down --remove-orphans >/dev/null 2>&1 || true
  if ! prod_up || ! prod_wait_healthy; then
    warn "  the deployment did not come back up on the same host paths"
    prod logs --tail 40 db api >&2
    prod_paths_cleanup || true
    return 1
  fi

  inode_after=$(prod exec -T db stat -c %i /var/lib/postgresql/data/PG_VERSION 2>/dev/null | tr -d '\r' || true)
  key_after=$(prod exec -T api sha256sum /secrets/metalmark_secret_key 2>/dev/null | tr -d '\r' | cut -d' ' -f1 || true)

  printf '  %-4s %-52s %s\n' \
    "$([ -n "$inode_before" ] && [ "$inode_before" = "$inode_after" ] && echo ok || echo FAIL)" \
    "a restart kept the same cluster" "${inode_before:-<none>} -> ${inode_after:-<none>}"
  { [ -n "$inode_before" ] && [ "$inode_before" = "$inode_after" ]; } || fail=1

  printf '  %-4s %-52s %s\n' \
    "$([ -n "$key_before" ] && [ "$key_before" = "$key_after" ] && echo ok || echo FAIL)" \
    "…and did not regenerate the credentials" "<compared, value not printed>"
  { [ -n "$key_before" ] && [ "$key_before" = "$key_after" ]; } || fail=1

  prod_paths_cleanup || true
  return $fail
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
