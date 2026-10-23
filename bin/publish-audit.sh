#!/usr/bin/env bash
# publish-audit.sh — pre-publication leak scan.
#
# Run this BEFORE pushing any change from a local working copy back to the
# public repo. Scans for common leak patterns:
#
#   - Personal/employer email addresses other than the public author email
#   - Hard-coded operator-private paths (/home/<user>/Cloud/, cloud-sync folders, etc)
#   - References to internal-firm names or private projects
#   - Internal decision-ID prefixes that may leak project structure
#
# The scan is conservative: false positives are common, especially in
# methodology-content where regulator names + abstract examples are
# legitimate. Treat output as a checklist, not as gospel — a human eyeball
# is required before push.
#
# Usage:
#   bash bin/publish-audit.sh                  # scan from repo root
#   bash bin/publish-audit.sh --strict          # exit non-zero on any hit
#
# Exit codes (v1.27.0 contract):
#   0  ran and clean
#   1  ran and found a defect (deny hits, or warn hits under --strict)
#   2  usage error (unknown argument, or the pattern file is unreadable)
#   3  NOT-RUNNABLE (not inside a git work tree, or git grep could not run)

set -euo pipefail

# The former single equality test discarded every argument that was not
# exactly `--strict`, so a mistyped flag ran the LOOSER scan and reported
# a green. Six wrong spellings were measured exiting 0 with no diagnostic.
# The loop below matches bin/publish-audit-state.sh:64-87 and
# bin/publish-audit-combined.sh:80-104, which already reject unknown args.
STRICT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
    *)
      printf '[publish-audit] FATAL: unknown argument: %s\n' "$1" >&2
      printf '[publish-audit] usage: bash bin/publish-audit.sh [--strict]\n' >&2
      exit 2
      ;;
  esac
done

notrunnable() {
  printf '[publish-audit] NOT-RUNNABLE: %s\n' "$*" >&2
  exit 3
}

# The former `|| pwd` fallback made the not-a-work-tree case silent instead
# of fatal: an unpacked ZIP or a `git archive` export still carries
# bin/audit-patterns.sh, so the FATAL check below passed, git grep scanned
# nothing, and the gate printed "no deny-pattern hits. Safe to push." at
# exit 0 with a leaking file on disk. A gate whose CANNOT-RUN is rendered
# as its PASS is the one property a gate must not have.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  notrunnable "not inside a git work tree, so git grep cannot scan tracked content"
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

color() {
  if [ -t 1 ] && [ "${TERM:-}" != "dumb" ]; then printf '\033[%sm%s\033[0m' "$1" "$2"
  else printf '%s' "$2"; fi
}
hdr()  { color '36' "[publish-audit] "; printf '%s\n' "$*"; }
hit()  { color '33' "[publish-audit hit] "; printf '%s\n' "$*"; }
fail() { color '31' "[publish-audit] "; printf '%s\n' "$*"; exit 1; }

# Source the canonical pattern list so this script and bin/publish-audit-state.sh
# stay in lockstep on what counts as a leak. Defines: DENY_PATTERNS,
# WARN_PATTERNS, PUBLIC_AUTHOR_EMAIL, SCAN_EXCLUDE.
PATTERNS_FILE="$REPO_ROOT/bin/audit-patterns.sh"
if [ ! -r "$PATTERNS_FILE" ]; then
  printf '[publish-audit] FATAL: cannot source %s\n' "$PATTERNS_FILE" >&2
  exit 2
fi
# shellcheck disable=SC1090
. "$PATTERNS_FILE"

# Report the denominator. A clean run against the public scaffold alone and
# a clean run with the gitignored operator overlay loaded used to be
# byte-identical, so no text, no count and no exit code distinguished
# "ran fully armed" from "ran the public subset". The overlay path is
# derived from PATTERNS_FILE so it stays in lockstep with the dirname
# resolution inside bin/audit-patterns.sh, and the wording mirrors
# bin/publish-audit-state.sh:129-133 so the two consumers agree.
# Absence is NOT an error: it is normal for a fresh public clone and for
# unprivileged CI, which is why this reports rather than exits.
OVERLAY_FILE="${PATTERNS_FILE%.sh}.local.sh"
if [ -r "$OVERLAY_FILE" ]; then
  overlay_state='operator-local overlay present, full DENY coverage active'
else
  overlay_state='no operator-local overlay present, public scaffold patterns only'
fi

deny_hits=0
warn_hits=0

# v1.20.1 (OBS-S67-03): SCAN_EXCLUDE is a PATH exclusion list, and it must be
# applied to the path field alone. The previous form piped the whole
# `git grep -n` record (path:lineno:content) through `grep -vE "$SCAN_EXCLUDE"`,
# which also matched on file CONTENT. Any leaking line whose text happened to
# mention an excluded token was therefore dropped before it was counted, and
# `.git` occurs constantly in this repository's own prose. A real DENY hit in
# CHANGELOG.md survived every push for that reason while the gate printed
# "Safe to push". Splitting on the first colon is safe because `git grep -n`
# always emits the path first and repo paths here contain no colon.
#
# The status git already returns is load-bearing and used to be thrown away.
# Under `set -o pipefail` the old form returned 0 on match, 1 on a legitimate
# no-match and 127 when git could not run, three distinct codes, and the
# callers' `|| true` collapsed all three. A git that could not run therefore
# rendered as a clean scan. Keep 0 and 1 as the only acceptable outcomes and
# report anything else as NOT-RUNNABLE; git's own stderr is no longer sent to
# /dev/null, so the reason reaches the operator.
scan_pattern() {
  local raw rc line path
  rc=0
  raw=$(git grep -nE "$1") || rc=$?
  if [ "$rc" -gt 1 ]; then
    printf '[publish-audit] git grep exited %s on pattern: %s\n' "$rc" "$1" >&2
    return 3
  fi
  [ -n "$raw" ] || return 0
  printf '%s\n' "$raw" | while IFS= read -r line; do
    path=${line%%:*}
    printf '%s' "$path" | grep -qE "$SCAN_EXCLUDE" || printf '%s\n' "$line"
  done
  return 0
}

hdr "scanning for hard-deny patterns"
for pat in "${DENY_PATTERNS[@]}"; do
  scan_rc=0
  matches=$(scan_pattern "$pat") || scan_rc=$?
  if [ "$scan_rc" -ne 0 ]; then
    notrunnable "deny scan could not run for pattern: $pat"
  fi
  if [ -n "$matches" ]; then
    hit "DENY pattern '$pat':"
    echo "$matches" | sed 's/^/    /'
    deny_hits=$((deny_hits + $(echo "$matches" | wc -l)))
  fi
done

hdr "scanning for warning patterns"
for pat in "${WARN_PATTERNS[@]}"; do
  scan_rc=0
  matches=$(scan_pattern "$pat") || scan_rc=$?
  if [ "$scan_rc" -ne 0 ]; then
    notrunnable "warn scan could not run for pattern: $pat"
  fi
  if [ -n "$matches" ]; then
    hit "WARN pattern '$pat':"
    echo "$matches" | sed 's/^/    /'
    warn_hits=$((warn_hits + $(echo "$matches" | wc -l)))
  fi
done

hdr "scanning HEAD commit author email (committed-content check)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Read the committed-content author email at HEAD instead of the audit-machine's
  # git config. Closes OBS-vcroe-combined-harness-fresh-clone-email-false-positive-01:
  # a fresh clone (post-publish state-audit context) inherits the audit machine's
  # global user.email, which is not the canonical public author. Checking HEAD %ae
  # answers the leak question directly ("is the published content from the canonical
  # author?") without depending on the audit machine's local config.
  author=$(git log -1 --format='%ae' 2>/dev/null || echo '')
  if [ -n "$author" ] && [ "$author" != "$PUBLIC_AUTHOR_EMAIL" ]; then
    hit "HEAD commit author email is '$author', expected '$PUBLIC_AUTHOR_EMAIL' for public-repo content"
    deny_hits=$((deny_hits + 1))
  fi
fi

echo ""
hdr "scan summary"
hdr "  deny patterns in force: ${#DENY_PATTERNS[@]}  ($overlay_state)"
hdr "  warn patterns in force: ${#WARN_PATTERNS[@]}"
hdr "  deny hits:  $deny_hits  (must be zero before push)"
hdr "  warn hits:  $warn_hits  (eyeball, then continue)"

if [ "$deny_hits" -gt 0 ]; then
  fail "abort: $deny_hits deny-pattern hits. Scrub before pushing."
fi

if [ "$STRICT" -eq 1 ] && [ "$warn_hits" -gt 0 ]; then
  fail "strict mode: $warn_hits warn-pattern hits. Review and re-run, or drop --strict."
fi

color '32' "[publish-audit] "; printf 'no deny-pattern hits. Safe to push.\n'
