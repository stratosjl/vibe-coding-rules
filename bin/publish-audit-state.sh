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

  # v1.20.2 (OBS-S67-04): pin the scanner to the CURRENT one.
  #
  # The walk used to check out each commit and run whatever audit script that
  # commit happened to ship. An early leak scrub in this repo's own history
  # replaced two codenames with bracketed placeholders and wrote those
  # placeholders into the DENY arrays of the day. A pattern written as
  # [SOME-NAME] is not a literal: as an ERE it is a character class matching
  # any one of those letters, so every such commit scanned itself with a
  # near-universal matcher and reported floods of hits on ordinary prose,
  # including its own JSON "name" fields. The reported count was noise.
  #
  # It is also the wrong question. "Does this old content violate today's
  # classification" is what the walk is for, not "did the script of the day
  # think so at the time". So the tree varies per commit and the scanner does
  # not. The pin is taken at HEAD, before any checkout moves the tree.
  PINNED_DIR="$TMPDIR/pinned"
  mkdir -p "$PINNED_DIR"
  cp -f "$CLONED_AUDIT" "$PINNED_DIR/publish-audit.sh"
  cp -f "$CLONED_PATTERNS" "$PINNED_DIR/audit-patterns.sh"
  if [ -r "$TMPDIR/repo/bin/audit-patterns.local.sh" ]; then
    cp -f "$TMPDIR/repo/bin/audit-patterns.local.sh" "$PINNED_DIR/audit-patterns.local.sh"
  fi
  hdr "history scanner pinned to public HEAD ($PUBLIC_HEAD_SHA)"

  # v1.20.2 (OBS-S67-04): the accepted-history baseline. Pinning the scanner
  # correctly surfaces content scrubbed at HEAD but still reachable by SHA in
  # older commits. Without a way to say "known and accepted", the walk would
  # be permanently red and would stop being read, which is how a control dies.
  #
  # The baseline is ENUMERATED, never a threshold: a finding is forgiven only
  # if its exact (path, pattern) pair is listed AND the commit is an ancestor
  # of baseline-sha. Every deny hit must be accounted for line-for-line, so a
  # deny that produces no parseable pair (the HEAD-author-email check, say)
  # fails the commit. Fail-closed in every direction.
  BASELINE_PUBLIC="$REPO_ROOT/bin/audit-history-baseline.txt"
  BASELINE_LOCAL="$REPO_ROOT/bin/audit-history-baseline.local.txt"
  BASELINE_MERGED="$TMPDIR/history-baseline.tsv"
  BASELINE_SHA=""
  : > "$BASELINE_MERGED"
  for bf in "$BASELINE_PUBLIC" "$BASELINE_LOCAL"; do
    [ -r "$bf" ] || continue
    grep -vE '^[[:space:]]*(#|$)' "$bf" >> "$BASELINE_MERGED" || true
    if [ -z "$BASELINE_SHA" ]; then
      BASELINE_SHA=$(grep -oE '^#[[:space:]]*baseline-sha:[[:space:]]*[0-9a-f]{7,40}' "$bf" \
        | head -1 | grep -oE '[0-9a-f]{7,40}' || true)
    fi
  done
  baseline_rows=$(wc -l < "$BASELINE_MERGED" | tr -d ' ')
  if [ -n "$BASELINE_SHA" ]; then
    hdr "accepted-history baseline: $baseline_rows row(s), valid at or before $BASELINE_SHA"
  else
    hdr "accepted-history baseline: none (every deny hit in history will be reported)"
  fi

  # Count the deny hit lines whose (path, pattern) pair is baselined.
  baselined_hit_count() {
    awk -v basefile="$BASELINE_MERGED" '
      BEGIN {
        FS = "\t"
        while ((getline l < basefile) > 0) {
          split(l, f, "\t")
          if (f[1] != "" && f[2] != "") accepted[f[1] "\x01" f[2]] = 1
        }
        n = 0
      }
      /^\[publish-audit hit\] DENY pattern / {
        pat = $0
        sub(/^\[publish-audit hit\] DENY pattern ./, "", pat)
        sub(/.:$/, "", pat)
        inblock = 1
        next
      }
      /^\[/ { inblock = 0; next }
      inblock && /^    / {
        rec = substr($0, 5)
        i = index(rec, ":")
        if (i > 1) {
          path = substr(rec, 1, i - 1)
          if ((path "\x01" pat) in accepted) n++
        }
        next
      }
      { inblock = 0 }
      END { print n + 0 }
    '
  }

  hdr "walking history (--history)..."
  hist_accepted=0
  while IFS= read -r commit; do
    [ -z "$commit" ] && continue
    # --force because the previous iteration left the pinned scripts in the
    # tree as local modifications, which a plain checkout would refuse to
    # overwrite. The pinned paths are all inside SCAN_EXCLUDE, so restoring
    # them does not change what the scan sees.
    commit_out=$(
      cd "$TMPDIR/repo" \
        && git checkout -q --force "$commit" \
        && mkdir -p bin \
        && cp -f "$PINNED_DIR/publish-audit.sh" bin/publish-audit.sh \
        && cp -f "$PINNED_DIR/audit-patterns.sh" bin/audit-patterns.sh \
        && { [ ! -r "$PINNED_DIR/audit-patterns.local.sh" ] \
             || cp -f "$PINNED_DIR/audit-patterns.local.sh" bin/audit-patterns.local.sh; } \
        && bash bin/publish-audit.sh 2>/dev/null
    ) && commit_rc=0 || commit_rc=$?
    [ "$commit_rc" -eq 0 ] && continue

    commit_deny=$(printf '%s\n' "$commit_out" \
      | grep -E '^\[publish-audit\][[:space:]]+deny hits:' \
      | head -1 \
      | sed -E 's/.*deny hits:[[:space:]]+([0-9]+).*/\1/' || true)
    [ -z "$commit_deny" ] && commit_deny=0

    accounted=0
    if [ -n "$BASELINE_SHA" ] && [ "$commit_deny" -gt 0 ] \
       && ( cd "$TMPDIR/repo" && git merge-base --is-ancestor "$commit" "$BASELINE_SHA" ) 2>/dev/null; then
      matched=$(printf '%s\n' "$commit_out" | baselined_hit_count)
      if [ "$matched" -eq "$commit_deny" ]; then
        accounted=1
      fi
    fi

    if [ "$accounted" -eq 1 ]; then
      hist_accepted=$((hist_accepted + 1))
    else
      hdr "  HISTORICAL LEAK at $commit ($commit_deny deny hit(s) not covered by the baseline)"
      hist_leaks=$((hist_leaks + 1))
    fi
  done < <(cd "$TMPDIR/repo" && git log --format='%H' main)

  if [ "$hist_accepted" -gt 0 ]; then
    hdr "$hist_accepted commit(s) carried only baselined, previously accepted findings"
  fi
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
