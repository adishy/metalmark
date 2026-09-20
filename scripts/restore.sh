#!/usr/bin/env bash
#
# Restore an encrypted backup into a database you name.
#
#   ./scripts/restore.sh dump.mmbak metalmark_restore_drill
#
# The target is a required argument and has no default: the one thing this
# script must never do is decide on its own to restore over the live database.
# Naming it is the deliberate act (restoring the live one after a disk failure
# is a legitimate use — it warns when that is what you named, and proceeds).
#
# Needs METALMARK_BACKUP_KEY in the environment, the same one the archive was
# written with; a wrong key fails before a single row is touched.
#
# This is the "restore" half. scripts/restore_drill.sh is the half that proves
# it: it runs this, then compares every table's row count against the source.
# Restoring once, by hand, is not the drill.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
usage: scripts/restore.sh <archive.mmbak> <database>

Restores the archive into <database>, creating it if it does not exist. Requires
the compose stack up (db and api) and METALMARK_BACKUP_KEY set to the key the
archive was written with.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
if [ "$#" -ne 2 ]; then
  usage >&2
  die "expected an archive and a database name (the database has no default, on purpose)"
fi

ARCHIVE="$1"
TARGET="$2"

[ -f "$ARCHIVE" ] || die "$ARCHIVE does not exist"

# The name is interpolated into container-side commands and SQL, and it comes
# off the command line. A leading `-` would be read as an option by createdb or
# dropdb, and anything else outside [A-Za-z0-9_] is a quoting problem waiting to
# happen; Postgres identifiers need no more than this, so nothing legitimate is
# being refused.
case "$TARGET" in
  -*|*[!A-Za-z0-9_]*) die "refusing to use $TARGET as a database name: [A-Za-z0-9_] only, and not starting with -" ;;
esac

command -v docker >/dev/null || die "docker not found"

running=$(docker compose ps --status running --services 2>/dev/null || true)
for service in db api; do
  grep -qx "$service" <<<"$running" || die "the ${service} container is not running. Start the stack first:
  docker compose up -d db api"
done

if [ -z "${METALMARK_BACKUP_KEY:-}" ]; then
  die "METALMARK_BACKUP_KEY is not set. Restoring needs the key the archive was written with."
fi
# Exported, and passed to the container by name below, so it stays out of argv.
export METALMARK_BACKUP_KEY

# Every database command runs inside the db container, where POSTGRES_* are the
# compose-resolved values (compose hands .env to the container, host-side
# defaults and all). The container's environment is the same one the application
# got, which is the only version of "the credentials" worth trusting — parsing
# .env a second time, in a different language, is how the script and the app end
# up connecting as different users.
psql_db() {  # psql_db <database> [psql args...]
  local db="$1"; shift
  docker compose exec -T db sh -c \
    'db="$1"; shift; exec psql -U "$POSTGRES_USER" -d "$db" -v ON_ERROR_STOP=1 "$@"' sh "$db" "$@"
}

# The decrypted dump, on the host, in cleartext, for as long as the restore
# takes. Removing it on a failure is not tidiness: `set -e` will abort on a
# failed createdb or pg_restore, and those are precisely the runs that would
# otherwise leave the whole ledger sitting in /tmp.
TMP="$(mktemp "${TMPDIR:-/tmp}/metalmark-restore.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

say "decrypt $ARCHIVE"
docker compose exec -T -e METALMARK_BACKUP_KEY api \
  python /scripts/backup.py decrypt < "$ARCHIVE" > "$TMP" \
  || die "the archive did not decrypt (wrong key, or corrupt). Nothing was restored."

say "create $TARGET if it does not exist"
# The name is interpolated straight into the SQL, which is safe only because of
# the [A-Za-z0-9_] check above — that check is the reason this can be a one-liner
# instead of a psql variable or a doubled-quote dance.
if [ -z "$(psql_db postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$TARGET'")" ]; then
  docker compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" "$1"' sh "$TARGET"
  printf '  created %s\n' "$TARGET"
else
  printf '  %s already exists; --clean below replaces its contents\n' "$TARGET"
fi

live=$(docker compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')
if [ "$TARGET" = "$live" ]; then
  warn "  $TARGET is the live database. --clean drops its objects before recreating them,"
  warn "  so the instance is down for the length of the restore. That is what a real"
  warn "  recovery looks like; if you meant to rehearse, pass a scratch name instead."
fi

say "restore into $TARGET"
# --no-owner, and deliberately NOT --role=<app role>. The two are often written
# together as if --role were the safe version of ownership; here it simply fails.
# The dump records that `metalmark` owns the tables, --no-owner creates them
# owned by whichever role pg_restore is running as, and --role sets that role
# mid-restore — to metalmark_app, which holds USAGE but not CREATE on schema
# public (0001_initial_schema.py:71, and PG16 revokes CREATE on public from
# PUBLIC). The restore stops at the first table with "permission denied for
# schema public".
#
# Connecting as the superuser (POSTGRES_USER) is not a convenience either: the
# dump carries ENABLE ROW LEVEL SECURITY, CREATE POLICY and the schema's ACL, and
# those may only be executed by the table owner or a superuser. Restoring as the
# app role would produce a database with the right rows and no policies — which
# is ARCHITECTURE §5's "biggest ongoing risk" installed by the backup itself.
# With --no-owner the objects come out owned by metalmark, exactly as they are in
# the database this dump came from.
#
# --exit-on-error because a restore that half-succeeded and exited 0 is a
# database that looks restored. The drill's row counts would catch it; an
# operator's restore at 3am would not.
docker compose exec -T db sh -c \
  'db="$1"; shift; exec pg_restore -U "$POSTGRES_USER" -d "$db" --clean --if-exists --no-owner --exit-on-error "$@"' \
  sh "$TARGET" < "$TMP" \
  || die "pg_restore failed; $TARGET is not a usable copy of the backup"

tables=$(psql_db "$TARGET" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
printf '\nrestored %s tables into %s from %s (%s bytes encrypted)\n' \
  "$tables" "$TARGET" "$ARCHIVE" "$(wc -c < "$ARCHIVE" | tr -d ' ')"
