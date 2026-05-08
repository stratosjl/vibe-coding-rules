#!/usr/bin/env bash
# publish-audit.sh — pre-publication leak scan.
#
# Run this BEFORE pushing any change from a local working copy back to the
# public repo. Scans for common leak patterns:
#
#   - Personal/employer email addresses other than the public author email
#   - Hard-coded operator-private paths (/home/<user>/Cloud/, OneDrive/, etc)
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

set -euo pipefail

STRICT=0
if [ "${1:-}" = "--strict" ]; then
  STRICT=1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

color() {
  if [ -t 1 ] && [ "${TERM:-}" != "dumb" ]; then printf '\033[%sm%s\033[0m' "$1" "$2"
  else printf '%s' "$2"; fi
}
hdr()  { color '36' "[publish-audit] "; printf '%s\n' "$*"; }
hit()  { color '33' "[publish-audit hit] "; printf '%s\n' "$*"; }
fail() { color '31' "[publish-audit] "; printf '%s\n' "$*"; exit 1; }

PUBLIC_AUTHOR_EMAIL='stratosjl@gmail.com'

# Patterns that are NEVER allowed in a public commit. Add operator-specific
# strings to this list as new private projects spin up.
DENY_PATTERNS=(
  # Employer + work email
  '[OPERATOR-DOMAIN]'
  's\.laspas@[OPERATOR-DOMAIN]'
  '[OPERATOR-FIRM]'
  '[OPERATOR-FIRM-ABBR]'

  # Internal R&D project names that are operator-private
  '[INT-D]'
  '[INT-E]'
  '[INT-F]'

  # Operator-private filesystem paths
  '/home/[OPERATOR-USER]/Cloud'
  'OneDrive/[OPERATOR-DOCS]'

  # Private-network hostnames (add as discovered)
  '\.internal\b'
  '\.lan\b'
)

# Patterns that PROBABLY indicate a leak but may be legitimate. Reviewer
# decides per-hit.
WARN_PATTERNS=(
  # Internal decision-ID prefixes (legitimate as documentation, but reveal
  # private-project-tracking provenance)
  'D-MET-[0-9]+'
  'OBS-MET-[A-Z]+'
  'F-MET-[A-Z0-9-]+'
  'I-MET-[0-9]+'

  # Specific compliance frameworks named in operator's day-job context
  # (regulator NAMES are public; specific firm-implementation paths might
  # not be — review case-by-case)
  'Tiered Methodology Consolidation'
)

# Files we never scan (binary / generated / license texts / this script
# itself, which contains the deny-patterns and would self-match).
SCAN_EXCLUDE='(__pycache__|\.git|\.gitignore|LICENSE-CODE|LICENSE-CONTENT|bin/publish-audit\.sh)'

deny_hits=0
warn_hits=0

hdr "scanning for hard-deny patterns"
for pat in "${DENY_PATTERNS[@]}"; do
  matches=$(grep -rnE "$pat" . 2>/dev/null | grep -vE "$SCAN_EXCLUDE" || true)
  if [ -n "$matches" ]; then
    hit "DENY pattern '$pat':"
    echo "$matches" | sed 's/^/    /'
    deny_hits=$((deny_hits + $(echo "$matches" | wc -l)))
  fi
done

hdr "scanning for warning patterns"
for pat in "${WARN_PATTERNS[@]}"; do
  matches=$(grep -rnE "$pat" . 2>/dev/null | grep -vE "$SCAN_EXCLUDE" || true)
  if [ -n "$matches" ]; then
    hit "WARN pattern '$pat':"
    echo "$matches" | sed 's/^/    /'
    warn_hits=$((warn_hits + $(echo "$matches" | wc -l)))
  fi
done

hdr "scanning author / committer email in staged changes"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  author=$(git config user.email 2>/dev/null || echo '')
  if [ -n "$author" ] && [ "$author" != "$PUBLIC_AUTHOR_EMAIL" ]; then
    hit "git config user.email is '$author', expected '$PUBLIC_AUTHOR_EMAIL' for public-repo work"
    deny_hits=$((deny_hits + 1))
  fi
fi

echo ""
hdr "scan summary"
hdr "  deny hits:  $deny_hits  (must be zero before push)"
hdr "  warn hits:  $warn_hits  (eyeball, then continue)"

if [ "$deny_hits" -gt 0 ]; then
  fail "abort: $deny_hits deny-pattern hits. Scrub before pushing."
fi

if [ "$STRICT" -eq 1 ] && [ "$warn_hits" -gt 0 ]; then
  fail "strict mode: $warn_hits warn-pattern hits. Review and re-run, or drop --strict."
fi

color '32' "[publish-audit] "; printf 'no deny-pattern hits. Safe to push.\n'
