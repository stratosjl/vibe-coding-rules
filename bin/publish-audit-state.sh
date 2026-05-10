#!/usr/bin/env bash
# publish-audit-state.sh — verify the public repo state contains zero
# DENY-pattern hits across the entire HEAD tree, not just the local
# diff. Run AFTER a push to confirm the public repo is clean from a
# cold clone, OR pre-push for an independent second-eye audit.
#
# Sources bin/audit-patterns.sh (same patterns as the pre-push script,
# bin/publish-audit.sh) so the two audits cannot drift apart.
#
# Usage:
#   bash bin/publish-audit-state.sh             # scan origin/main HEAD
#   bash bin/publish-audit-state.sh --history   # walk every commit on main
#
# Exits non-zero on any DENY hit. Optional --history walk treats every
# historical leak the same way as a current-HEAD leak.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PATTERNS_FILE="$REPO_ROOT/bin/audit-patterns.sh"
PUBLISH_AUDIT="$REPO_ROOT/bin/publish-audit.sh"

if [ ! -r "$PATTERNS_FILE" ]; then
  printf '[publish-audit-state] FATAL: cannot source %s\n' "$PATTERNS_FILE" >&2
  exit 2
fi
if [ ! -r "$PUBLISH_AUDIT" ]; then
  printf '[publish-audit-state] FATAL: cannot find %s\n' "$PUBLISH_AUDIT" >&2
  exit 2
fi

# Resolve the public remote URL. Default to the canonical GitHub URL if
# no origin remote is configured. Translate SSH → HTTPS for the audit
# clone: the audit is read-only, the public repo allows anonymous HTTPS
# read, and HTTPS works without an SSH key in the agent. Push-time auth
# (SSH) is unrelated to audit-time auth (HTTPS).
REMOTE_URL_RAW="$(git -C "$REPO_ROOT" config --get remote.origin.url 2>/dev/null || echo '')"
if [ -z "$REMOTE_URL_RAW" ]; then
  REMOTE_URL_RAW='git@github.com:stratosjl/vibe-coding-rules.git'
fi
case "$REMOTE_URL_RAW" in
  git@github.com:*)
    REMOTE_URL="https://github.com/${REMOTE_URL_RAW#git@github.com:}"
    ;;
  ssh://git@github.com/*)
    REMOTE_URL="https://github.com/${REMOTE_URL_RAW#ssh://git@github.com/}"
    ;;
  *)
    REMOTE_URL="$REMOTE_URL_RAW"
    ;;
esac

WALK_HISTORY=0
if [ "${1:-}" = "--history" ]; then
  WALK_HISTORY=1
fi

TMPDIR=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$TMPDIR'" EXIT

color() {
  if [ -t 1 ] && [ "${TERM:-}" != "dumb" ]; then printf '\033[%sm%s\033[0m' "$1" "$2"
  else printf '%s' "$2"; fi
}
hdr()  { color '36' "[publish-audit-state] "; printf '%s\n' "$*"; }
fail() { color '31' "[publish-audit-state] "; printf '%s\n' "$*"; exit 1; }

if [ "$WALK_HISTORY" -eq 1 ]; then
  hdr "cloning $REMOTE_URL with full history into ephemeral tempdir..."
  git clone --quiet "$REMOTE_URL" "$TMPDIR/repo"
else
  hdr "cloning $REMOTE_URL (shallow) into ephemeral tempdir..."
  git clone --quiet --depth=1 "$REMOTE_URL" "$TMPDIR/repo"
fi

# The cloned tree's bin/publish-audit.sh is what we run, so the audit
# uses the public repo's own pattern list at HEAD. We deliberately do
# NOT run the local-tree publish-audit.sh against the cloned tree —
# that would mix local-uncommitted DENY changes with the published
# state and confuse the post-publish verification semantic.
CLONED_AUDIT="$TMPDIR/repo/bin/publish-audit.sh"
CLONED_PATTERNS="$TMPDIR/repo/bin/audit-patterns.sh"
if [ ! -r "$CLONED_AUDIT" ] || [ ! -r "$CLONED_PATTERNS" ]; then
  fail "cloned repo at $REMOTE_URL HEAD does not contain bin/publish-audit.sh + bin/audit-patterns.sh; cannot self-audit"
fi

# Pin the cloned tempdir's user.email to the canonical public author so the
# inner audit's email check does not inherit the parent shell's global
# config (closes OBS-vcroe-audit-state-emailcheck-fp-01).
# shellcheck disable=SC1090
. "$CLONED_PATTERNS"
git -C "$TMPDIR/repo" config user.email "$PUBLIC_AUTHOR_EMAIL"

state_rc=0

hdr "auditing public HEAD..."
if ! ( cd "$TMPDIR/repo" && bash bin/publish-audit.sh ); then
  hdr "DENY-pattern hits at public HEAD"
  state_rc=1
fi

if [ "$WALK_HISTORY" -eq 1 ]; then
  hdr "walking history (--history)..."
  hist_leaks=0
  while IFS= read -r commit; do
    [ -z "$commit" ] && continue
    if ! ( cd "$TMPDIR/repo" && git checkout -q "$commit" && bash bin/publish-audit.sh 2>/dev/null ); then
      hdr "  HISTORICAL LEAK at $commit"
      hist_leaks=$((hist_leaks + 1))
    fi
  done < <(cd "$TMPDIR/repo" && git log --format='%H' main)

  if [ "$hist_leaks" -gt 0 ]; then
    hdr "$hist_leaks historical leak(s) found"
    state_rc=1
  else
    hdr "history walk clean: no historical leaks"
  fi
fi

if [ "$state_rc" -eq 0 ]; then
  color '32' "[publish-audit-state] "; printf 'public repo state is clean.\n'
else
  fail "public repo state has DENY-pattern hits. Surface as OBS row before further pushes."
fi
