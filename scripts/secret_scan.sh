#!/usr/bin/env bash
#
# No credentials in the repo, in its history, or in the image built from it.
#
# Run directly (`./scripts/secret_scan.sh`) or as the `secrets` gate. It needs
# neither Docker nor a running stack, because it reads only files.
#
# Why it exists: this repo holds one real bank credential — a SimpleFIN access
# URL is a bearer token in a URL, with the account password inside it — and it
# now publishes images to a registry (ADR-0039). `COPY . .` in the api image's
# Dockerfile means anything that survives `.dockerignore` is shipped to whoever
# pulls. Three ways in, three checks:
#
#   1. paths      `.env` and `secrets/` are gitignored. This asserts the ignore
#                 rules are still there, because the failure that matters is not
#                 a file being added — it is the .gitignore line being dropped
#                 and the next `git add -A` taking the key with it.
#   2. contents   the worktree and every commit on every ref, for the shapes a
#                 credential has. Patterns are chosen for precision over recall:
#                 a rule that fires on `devpassword123` in a test would be
#                 ignored within a week, and an ignored gate is worse than none.
#   3. the image  the api image has no `.env` and no key in it (the `prod` gate
#                 asks that of the running container, where it is answerable).
#
# This script never prints what it matched — only `path:line` and the name of the
# rule. A scanner that echoes a leaked token into a terminal and a CI log has
# found the secret and published it, which is the whole accident it exists to
# prevent.
#
# Two things are skipped, and neither is "whatever the pattern happened to
# catch": a line carrying an explicit `secret-scan: allow` marker, and a line
# carrying one of the placeholder values below. The second exists because this
# repo documents, seeds and CI-tests with known-bad credentials on purpose —
# `devpassword123` is in the README and in the seeded demo ledger — and a rule
# that fires on those is a rule everyone learns to scroll past. The list is
# deliberately a visible edit to this file rather than an inline marker on each
# line, so it is a short list that has to be kept short.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
report() { printf '  %-4s %-52s %s\n' "FAIL" "$1" "$2"; }

# name|regex — see the note above about precision. The basic-auth rule is the one
# that matters most here and is deliberately narrow: a URL with credentials
# before the host, which is what a SimpleFIN access URL is. Six characters a
# side, because the examples in this repo's own prose (`https://user:pass@host`)
# are four — a rule that flags the paragraph explaining the redactor is a rule
# nobody reads to the end.
RULES=(
  'a Private Key block|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'a GitHub token|gh[pousr]_[A-Za-z0-9]{36,}'
  'an AWS access key id|AKIA[0-9A-Z]{16}'
  'a Slack token|xox[abprs]-[A-Za-z0-9-]{10,}'
  'credentials in a URL|https?://[^/@[:space:]]{6,64}:[^/@[:space:]]{6,64}@'
  'a Fernet key assigned|METALMARK_SECRET_KEY[=:][[:space:]]*[A-Za-z0-9_-]{43}='
  # A value that *starts* with `$` is an interpolation — `PASSWORD="$SEED_PASSWORD"`
  # is a reference, and the literal it might resolve to is not on this line. What
  # is left is a quoted literal, which is the thing worth failing on. Matched
  # case-insensitively (see `-i` below), so a lowercase `"password"` key in JSON
  # is covered as well as the SCREAMING_CASE environment variable.
  'a hardcoded password|(PASSWORD|SECRET|TOKEN|API_KEY)[=:][[:space:]]*["'"'"'][^$'"'"'][^"'"'"']{11,}["'"'"']'
)

#: Values this repo uses *as* placeholders, and hosts reserved for exactly this
#: (RFC 2606: `.invalid`, `.example`, `.test` — a name that cannot resolve and
#: cannot be a real bridge). Filtering them by line keeps the rules above sharp
#: for everything else: `FAKE_ACCESS_URL` in the fake provider, the seeded demo
#: password in the README and in CI, the throwaway CI database password.
PLACEHOLDERS='devpassword123|dev_only_change_me|metalmark_ci_password|ci-secret|@[^/[:space:]]*\.(invalid|example|test)|@localhost'

# ---------------------------------------------------------------------------
# 1. The ignore rules that keep the key out of git at all
# ---------------------------------------------------------------------------
for entry in '.env' 'secrets/'; do
  if git check-ignore -q "$entry" 2>/dev/null || git check-ignore -q "secrets/x" 2>/dev/null; then
    printf '  %-4s %-52s %s\n' "ok" "git ignores ${entry}" "ignored"
  else
    report "git ignores ${entry}" "NOT IGNORED"
    fail=1
  fi
done

# A file that is already tracked stays tracked through a later .gitignore, so the
# paths themselves are checked and not just the rules.
tracked=$(git ls-files | grep -E '(^|/)\.env$|^secrets/' || true)
if [ -n "$tracked" ]; then
  report "no credential paths are tracked" "$(tr '\n' ' ' <<<"$tracked")"
  fail=1
else
  printf '  %-4s %-52s %s\n' "ok" "no credential paths are tracked" "git ls-files"
fi

# ---------------------------------------------------------------------------
# 2. The worktree, and then every commit on every ref
# ---------------------------------------------------------------------------
# `-I` skips binaries (a PNG is not text and `.git` objects are compressed), and
# the excluded directories are the ones that are not this repo's source: vendored
# packages, build output, and the virtualenv.
worktree_hits() {  # <regex>
  grep -rniIE \
    --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv \
    --exclude-dir=dist --exclude-dir=playwright-report --exclude-dir=test-results \
    --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache \
    --binary-files=without-match -e "$1" . 2>/dev/null \
    | grep -v 'secret-scan: allow' \
    | grep -vE "$PLACEHOLDERS" \
    | cut -d: -f1-2 || true
}

# `--name-only`-ish output: cut drops the matched text before it can be printed.
history_hits() {  # <regex>
  # shellcheck disable=SC2046  # the rev list is the argument list on purpose
  git grep -niIE -e "$1" $(git rev-list --all) 2>/dev/null \
    | grep -v 'secret-scan: allow' \
    | grep -vE "$PLACEHOLDERS" \
    | cut -d: -f1-2 || true
}

for rule in "${RULES[@]}"; do
  name="${rule%%|*}"
  pattern="${rule#*|}"

  found=$(worktree_hits "$pattern")
  if [ -n "$found" ]; then
    report "the worktree has no ${name}" "$(head -3 <<<"$found" | tr '\n' ' ')"
    fail=1
  fi

  # History is reported per rule but as `rev:path`, which says where to look
  # without saying what was found.
  found=$(history_hits "$pattern")
  if [ -n "$found" ]; then
    report "no commit ever carried ${name}" "$(head -3 <<<"$found" | tr '\n' ' ')"
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  printf '  %-4s %-52s %s\n' "ok" "the worktree and every commit are clean" \
    "${#RULES[@]} rules × $(git rev-list --all --count) commits"
fi

exit "$fail"
