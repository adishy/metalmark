#!/usr/bin/env bash
#
# Back up the whole MetalMark database into one encrypted archive.
#
#   ./scripts/backup.sh [out.mmbak]      # default: pg_backups/metalmark-<date>.mmbak
#
# Needs METALMARK_BACKUP_KEY in the environment, and it refuses to run without
# it — an unencrypted dump is cleartext PII, and the key is the one thing that
# must NOT live next to the archive (ARCHITECTURE §5: "a dump and co-located key
# = full compromise"). Take it from wherever you keep secrets:
#
#   METALMARK_BACKUP_KEY="$(cat secrets/backup_key)" ./scripts/backup.sh
#
# This is the "backup" half of the pair the plan separates (PLAN-v0.9 §C): the
# export endpoint is not a backup, because it omits users and sessions and
# cannot rebuild an instance. This can.
#
# Restore it with scripts/restore.sh, and find out whether it works with
# scripts/restore_drill.sh — a backup nobody has restored from is a belief.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
usage: scripts/backup.sh [out.mmbak]

Writes an encrypted, self-contained backup of the whole database. Requires the
compose stack up (db and api) and METALMARK_BACKUP_KEY set.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  -*) die "unknown option: $1 (try --help)" ;;
esac

command -v docker >/dev/null || die "docker not found"

# Both containers are needed and neither is optional: db runs pg_dump, api runs
# the crypto (it has cryptography and no pg_dump; db has pg_dump and no Python).
# Same reasoning as verify.sh's require_stack — say it once, here, rather than
# letting the failure surface as a docker error that names neither the service
# nor the reason.
running=$(docker compose ps --status running --services 2>/dev/null || true)
for service in db api; do
  grep -qx "$service" <<<"$running" || die "the ${service} container is not running. Start the stack first:
  docker compose up -d db api"
done

if [ -z "${METALMARK_BACKUP_KEY:-}" ]; then
  die "METALMARK_BACKUP_KEY is not set. Refusing to write a cleartext dump of the ledger.
  METALMARK_BACKUP_KEY=\"\$(cat secrets/backup_key)\" ./scripts/backup.sh
  (or, for a new key: openssl rand -hex 32)"
fi
# Exported because the child processes need it, and passed to the container as
# a *name* (`-e METALMARK_BACKUP_KEY` below, no `=value`) so the key never
# appears in this process's argv, where any local user could read it out of ps.
export METALMARK_BACKUP_KEY

OUT="${1:-$ROOT/pg_backups/metalmark-$(date -u +%Y-%m-%d).mmbak}"
mkdir -p "$(dirname "$OUT")"

# The dump lands here in cleartext before it is encrypted. The trap is the point:
# this is the entire ledger — every account, transaction and holding — and the
# failure paths (a failed dump, a full disk, an archive that will not
# authenticate) are exactly when a `rm` at the end of the happy path would have
# left it behind for the rest of the machine's uptime.
TMP="$(mktemp "${TMPDIR:-/tmp}/metalmark-dump.XXXXXX")"
wrote=0
cleanup() {
  rm -f "$TMP"
  # A half-written archive is worse than no archive: it has the right name, the
  # right owner and a plausible size, and it is discovered to be undecryptable
  # at the moment it is the only copy left.
  [ "$wrote" = 1 ] || rm -f "$OUT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

say "dump the database (custom format, as the owner)"
# -T is not cosmetic: with a TTY on stdout the pty translates \n to \r\n, and
# the archive would be quietly wrong in a way only a restore reveals.
#
# The dump and the encryption are deliberately two commands joined by a host-side
# temp file rather than one pipeline between two containers. A pipeline has no
# room for an error: a partial dump, or docker printing anything on stdout,
# becomes part of the archive, and nobody finds out until the restore.
#
# -U "$POSTGRES_USER" because the app role is subject to RLS: as metalmark_app,
# with no app.household_id set, a dump would capture a correct dump of *nothing*.
docker compose exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TMP" \
  || die "pg_dump failed; $OUT was not written"

say "encrypt with METALMARK_BACKUP_KEY"
docker compose exec -T -e METALMARK_BACKUP_KEY api \
  python /scripts/backup.py encrypt < "$TMP" > "$OUT" \
  || die "encryption failed; refusing to keep a partial archive at $OUT"

wrote=1
printf '\nbackup written: %s\n' "$OUT"
printf '  %s bytes encrypted, from a %s byte dump\n' "$(wc -c < "$OUT" | tr -d ' ')" "$(wc -c < "$TMP" | tr -d ' ')"
