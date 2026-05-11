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
#   bash bin/publish-audit-state.sh                     # scan origin/main HEAD
#   bash bin/publish-audit-state.sh --history           # walk every commit on main
#   bash bin/publish-audit-state.sh --json-out FILE     # HEAD + history; write JSON to FILE
#
# Exits non-zero on any DENY hit. Optional --history walk treats every
# historical leak the same way as a current-HEAD leak.
#
# --json-out FILE implies --history. Atomically writes a single-line JSON
# object to FILE with the shape:
#   {"ts": <epoch>, "head_sha": "<sha>", "deny_count": N, "warn_count": M,
#    "history_walk_clean": true|false}
# Designed for a user crontab broadcast that the SessionStart hook reads at
# session-open to surface a "publish-state:" trace line (v1.4.0,
# OBS-vcroe-coordination-cron-broadcast-01 closure).

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
JSON_OUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --history)
      WALK_HISTORY=1
      shift
      ;;
    --json-out)
      if [ "$#" -lt 2 ]; then
        printf '[publish-audit-state] FATAL: --json-out requires a path argument\n' >&2
        exit 2
      fi
      JSON_OUT="$2"
      shift 2
      ;;
    --json-out=*)
      JSON_OUT="${1#--json-out=}"
      shift
      ;;
    *)
      printf '[publish-audit-state] FATAL: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

# --json-out implies --history: the JSON shape carries history_walk_clean,
# so a non-history run cannot populate that field truthfully.
if [ -n "$JSON_OUT" ]; then
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

# Capture public HEAD sha BEFORE any checkout walks; the --history loop
# below leaves the working tree at the last historical commit, so a late
# `git rev-parse HEAD` would mis-report the broadcast head_sha.
PUBLIC_HEAD_SHA=$( ( cd "$TMPDIR/repo" && git rev-parse HEAD ) 2>/dev/null || echo 'unknown' )
PUBLIC_HEAD_SHA=$(printf '%s' "$PUBLIC_HEAD_SHA" | tr -d '\n')

# Copy operator's local overlay (if present) into the cloned tempdir so
# the inner audit applies operator-flavored DENY patterns against the
# published state. The overlay file itself is gitignored (never
# published) but the pattern coverage is what the post-publish audit
# needs. When run from a fresh public clone with no local overlay, this
# is a no-op and the inner audit applies only the public scaffold's
# generic patterns (introduced at v1.6.0 with the W2 split).
LOCAL_OVERLAY="$REPO_ROOT/bin/audit-patterns.local.sh"
if [ -r "$LOCAL_OVERLAY" ]; then
  cp -f "$LOCAL_OVERLAY" "$TMPDIR/repo/bin/audit-patterns.local.sh"
  hdr "operator-local overlay copied into temp clone (full DENY coverage active)"
else
  hdr "no operator-local overlay present; running with public scaffold patterns only"
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
head_deny_count=0
head_warn_count=0
hist_leaks=0
history_walked=0

hdr "auditing public HEAD..."
head_audit_output=$( ( cd "$TMPDIR/repo" && bash bin/publish-audit.sh ) 2>&1 ) || state_rc=1
printf '%s\n' "$head_audit_output"
if printf '%s\n' "$head_audit_output" | grep -qE '^\[publish-audit\][[:space:]]+deny hits:'; then
  head_deny_count=$(printf '%s\n' "$head_audit_output" \
    | grep -E '^\[publish-audit\][[:space:]]+deny hits:' \
    | head -1 \
    | sed -E 's/.*deny hits:[[:space:]]+([0-9]+).*/\1/')
fi
if printf '%s\n' "$head_audit_output" | grep -qE '^\[publish-audit\][[:space:]]+warn hits:'; then
  head_warn_count=$(printf '%s\n' "$head_audit_output" \
    | grep -E '^\[publish-audit\][[:space:]]+warn hits:' \
    | head -1 \
    | sed -E 's/.*warn hits:[[:space:]]+([0-9]+).*/\1/')
fi
if [ "$state_rc" -ne 0 ]; then
  hdr "DENY-pattern hits at public HEAD"
fi

if [ "$WALK_HISTORY" -eq 1 ]; then
  history_walked=1
  hdr "walking history (--history)..."
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

# v1.4.0: JSON broadcast for the SessionStart trace consumer
# (OBS-vcroe-coordination-cron-broadcast-01 closure). Always written when
# --json-out is set, regardless of state_rc, so the consumer sees the
# bad-state record rather than a stale clean one.
if [ -n "$JSON_OUT" ]; then
  HEAD_SHA="$PUBLIC_HEAD_SHA"
  if [ "$history_walked" -eq 1 ] && [ "$hist_leaks" -eq 0 ]; then
    HIST_CLEAN_JSON=true
  else
    HIST_CLEAN_JSON=false
  fi
  JSON_TMP="${JSON_OUT}.tmp.$$"
  if printf '{"ts": %s, "head_sha": "%s", "deny_count": %s, "warn_count": %s, "history_walk_clean": %s}\n' \
       "$(date +%s)" "$HEAD_SHA" "$head_deny_count" "$head_warn_count" "$HIST_CLEAN_JSON" \
       > "$JSON_TMP" 2>/dev/null; then
    if mv -f "$JSON_TMP" "$JSON_OUT" 2>/dev/null; then
      hdr "wrote JSON broadcast to $JSON_OUT"
    else
      rm -f "$JSON_TMP" 2>/dev/null || true
      hdr "WARNING: could not move JSON broadcast into place at $JSON_OUT"
    fi
  else
    rm -f "$JSON_TMP" 2>/dev/null || true
    hdr "WARNING: could not write JSON broadcast tmpfile $JSON_TMP"
  fi
fi

if [ "$state_rc" -eq 0 ]; then
  color '32' "[publish-audit-state] "; printf 'public repo state is clean.\n'
else
  fail "public repo state has DENY-pattern hits. Surface as OBS row before further pushes."
fi
