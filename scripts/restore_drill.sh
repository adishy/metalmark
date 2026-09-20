#!/usr/bin/env bash
#
# The restore drill: back up the live database, restore it into a scratch
# database, and compare every table's row count between the two.
#
#   ./scripts/restore_drill.sh
#
# PLAN-v0.9 §C makes this the deliverable rather than the backup script: "a
# backup script nobody has restored from is a belief, not a capability". So this
# is the gate that turns the belief into a fact — it runs the real backup.sh and
# the real restore.sh, end to end, and fails on the first difference.
#
# It is safe to run against a live stack: the scratch database is
# metalmark_restore_drill, which is dropped before and after (even on failure),
# and nothing here writes to the live database.
#
# No arguments, and no METALMARK_BACKUP_KEY required: a drill that needs a human
# to export a variable first is a drill that does not run. It generates a
# throwaway key when none is set, and uses the real one when there is one — it
# never replaces a key it was given.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Deliberately not metalmark (the dev database) and not metalmark_test*: the
# test harness drops and recreates every metalmark_test* database it is pointed
# at, so a drill that shared that namespace could have its scratch database
# deleted mid-comparison by a suite running in another terminal — and the
# symptom would be a row count that changed for no reason.
SCRATCH="metalmark_restore_drill"

# The db container is not told the app role — docker-compose.yml gives
# APP_DB_USER to api and worker only — so it comes from the environment here,
# with the same default compose uses.
APP_ROLE="${APP_DB_USER:-metalmark_app}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) printf 'usage: scripts/restore_drill.sh\n\nRuns the backup and restore scripts end to end and compares row counts.\n'; exit 0 ;;
esac

command -v docker >/dev/null || die "docker not found"

running=$(docker compose ps --status running --services 2>/dev/null || true)
for service in db api; do
  grep -qx "$service" <<<"$running" || die "the ${service} container is not running. Start the stack first:
  docker compose up -d db api"
done

# Every database command runs in the db container, where POSTGRES_* are the
# compose-resolved values, and as POSTGRES_USER — the owner. Not as the app
# role: with no app.household_id set, RLS would hide every household's rows and
# both sides of the comparison would be a tidy, meaningless zero. (Same helper
# as restore.sh, spelled out there at more length.)
psql_db() {  # psql_db <database> [psql args...]
  local db="$1"; shift
  docker compose exec -T db sh -c \
    'db="$1"; shift; exec psql -U "$POSTGRES_USER" -d "$db" -v ON_ERROR_STOP=1 "$@"' sh "$db" "$@"
}

# The live database's name comes from the container, not from this script's idea
# of the default: the drill *drops* a database, and the one mistake that would
# turn a rehearsal into an incident is pointing that at the ledger.
LIVE_DB=$(docker compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')
[ "$SCRATCH" != "$LIVE_DB" ] || die "the scratch database name collides with the live database ($LIVE_DB); refusing to run"

# <database> -> "table|rows" per line, for every table in the public schema.
# One query, built from pg_tables, so a table added by a future migration is
# covered without anyone remembering to edit this script — the drill's value
# depends on it not having a list to fall out of date.
counts() {
  local db="$1" sql
  sql=$(psql_db "$db" -Atc "SELECT string_agg(format('SELECT %L, count(*) FROM %I.%I', tablename, schemaname, tablename), ' UNION ALL ' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public'")
  [ -n "$sql" ] || return 1
  psql_db "$db" -At -F'|' -c "$sql"
}

drop_scratch() {
  # stderr is dropped because `--if-exists` makes "database does not exist,
  # skipping" a NOTICE on every second call, and the trap calls this on every
  # exit path — including the successful one, where it would print after the
  # verdict. A real failure still returns non-zero, and the callers say so.
  docker compose exec -T db sh -c \
    'exec dropdb -U "$POSTGRES_USER" --if-exists --force "$1"' sh "$SCRATCH" >/dev/null 2>&1
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/metalmark-drill.XXXXXX")"
ARCHIVE="$WORK/drill.mmbak"
cleanup() {
  # The scratch database is dropped on every path out of here, including the
  # ones that failed: a leftover metalmark_restore_drill is a database someone
  # will eventually mistake for the real one.
  drop_scratch || warn "  could not drop $SCRATCH (leaving it for you to inspect: docker compose exec db dropdb -U \"\$POSTGRES_USER\" $SCRATCH)"
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -z "${METALMARK_BACKUP_KEY:-}" ]; then
  METALMARK_BACKUP_KEY="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  export METALMARK_BACKUP_KEY
  printf 'no METALMARK_BACKUP_KEY in the environment: generated a throwaway one for this run.\n'
  printf 'The archive is deleted with the scratch database, so nothing outlives the drill.\n'
else
  printf 'using the METALMARK_BACKUP_KEY from the environment (the drill does not replace a real key).\n'
fi

say "rehearse against the live database ($LIVE_DB): dump, encrypt, restore"
# The scratch database first. restore.sh creates it if absent, so this is only
# to make a leftover from an interrupted run impossible to mistake for a copy
# made by this one — the counts are compared against *this* dump or not at all.
drop_scratch || warn "  could not drop a leftover $SCRATCH; continuing"

"$ROOT/scripts/backup.sh" "$ARCHIVE" || die "backup.sh failed; there is nothing to restore"
"$ROOT/scripts/restore.sh" "$ARCHIVE" "$SCRATCH" || die "restore.sh failed; the archive did not come back"

say "compare row counts, table by table (as the owner, so RLS does not hide anything)"
SOURCE_COUNTS="$WORK/source.counts"
RESTORED_COUNTS="$WORK/restored.counts"
if ! counts "$LIVE_DB" > "$SOURCE_COUNTS" 2>"$WORK/source.err"; then
  warn "$(cat "$WORK/source.err")"
  die "could not count rows in the live database"
fi
if ! counts "$SCRATCH" > "$RESTORED_COUNTS" 2>"$WORK/restored.err"; then
  warn "$(cat "$WORK/restored.err")"
  die "could not count rows in the restored database"
fi

# A comparison of nothing passes vacuously, and a `match` summary for a database
# this script never managed to query is the worst possible green.
[ -s "$SOURCE_COUNTS" ] || die "the live database has no tables in schema public; nothing was compared"

printf '\n%-38s %8s %9s  %s\n' table source restored
printf '%s\n' "-------------------------------------------------------------------------"
fail=0
compared=0
source_rows=0
restored_rows=0
while read -r table; do
  src=$(awk -F'|' -v t="$table" '$1 == t { print $2 }' "$SOURCE_COUNTS")
  dst=$(awk -F'|' -v t="$table" '$1 == t { print $2 }' "$RESTORED_COUNTS")
  if [ -z "$src" ]; then
    status="only in the restored database"
  elif [ -z "$dst" ]; then
    status="MISSING from the restored database"
  elif [ "$src" != "$dst" ]; then
    # The columns say by how much; the status does not try to ("1 rows" is what
    # spelling it out gets you).
    status="DIFFERENT"
  else
    status="ok"
  fi
  [ "$status" = "ok" ] || fail=1
  # ${src:-0} so a table missing from one side still contributes the other's
  # rows: a total that silently skipped the differing tables is the number an
  # operator would have quoted.
  source_rows=$((source_rows + ${src:-0}))
  restored_rows=$((restored_rows + ${dst:-0}))
  printf '%-38s %8s %9s  %s\n' "$table" "${src:--}" "${dst:--}" "$status"
  compared=$((compared + 1))
done < <(cut -d'|' -f1 "$SOURCE_COUNTS" "$RESTORED_COUNTS" | sort -u)
printf '%s\n' "-------------------------------------------------------------------------"
if [ "$fail" -eq 0 ]; then
  printf '%s tables, %s rows, every count identical\n' "$compared" "$source_rows"
else
  printf '%s tables, %s rows in the source, %s in the restored copy, DIFFERENCES FOUND\n' \
    "$compared" "$source_rows" "$restored_rows"
fi

say "tenant isolation in the restored database"
# The counts are identical whether or not the RLS policies survived the round
# trip, and a restored database without them is ARCHITECTURE §5's "biggest
# ongoing risk" shipped by the backup: any session reads every household. So the
# drill asks the restored database the one question a row count cannot: as the
# app role, with no app.household_id set, how much of the ledger is visible?
# Fail-closed means zero.
rls_out=$(psql_db "$SCRATCH" -Atc "SET ROLE $APP_ROLE; SELECT count(*) FROM accounts" 2>&1) || fail=1
# psql prints the SET command's tag as well, so the answer is the last line.
rls_count=$(printf '%s\n' "$rls_out" | tail -n 1)
if [ "$rls_count" != "0" ]; then
  printf '  %s sees %s rows in accounts with no household set — RLS did not survive the restore\n' \
    "$APP_ROLE" "$rls_count" >&2
  fail=1
else
  printf '  as %s with no app.household_id: 0 rows visible (fails closed)\n' "$APP_ROLE"
fi

say "drop $SCRATCH"
drop_scratch || warn "  could not drop $SCRATCH"

if [ "$fail" -ne 0 ]; then
  printf '\033[31mDRILL FAILED\033[0m — the backup did not come back as the database it was taken from\n' >&2
  exit 1
fi
printf '\033[32mDRILL PASSED\033[0m — %s tables and %s rows came back from %s, and the restored copy still fails closed\n' \
  "$compared" "$source_rows" "$(basename "$ARCHIVE")"
