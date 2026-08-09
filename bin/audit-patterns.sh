#!/usr/bin/env bash
# audit-patterns.sh - public scaffold for the publish audit.
#
# PUBLIC SCAFFOLD. Operator-flavored DENY entries live in the gitignored
# bin/audit-patterns.local.sh (sourced below if present). The example
# template bin/audit-patterns.local.sh.example shows the expected shape.
#
# Sourced by both bin/publish-audit.sh (pre-push, scans the local working
# tree) and bin/publish-audit-state.sh (post-publish, scans a fresh clone
# of the public repo). Single source of truth for the public pattern set
# so the two audits cannot drift apart.
#
# The DENY/WARN arrays defined here are intentionally minimal and generic.
# Operator-specific patterns must be appended via the local overlay; the
# public scaffold itself stays free of operator-flavored content so this
# file is safe to publish on github.com.
#
# Architectural rationale: vc-roe v1.6.0 replaced the prior single-file
# design (which baked operator-flavored DENY entries into a publicly-
# committed file, creating a self-leak the in-file SCAN_EXCLUDE could not
# prevent) with the public-scaffold + private-overlay split. The W3-to-W2
# decision inversion is documented in the v1.6.0 CHANGELOG entry.
#
# Both arrays are exported so a sourcing script can iterate them after
# the source line.

# Hard-deny: NEVER allowed in a public commit. A single hit blocks the
# push (or the post-publish scan exits non-zero). The public scaffold
# carries only generic private-network hostname patterns; operator-
# specific identifiers are appended by the local overlay.
DENY_PATTERNS=(
  # Generic private-network hostnames (safe to publish; pattern shapes
  # rather than identifiers, no false-positive surface in regulator-domain
  # prose).
  '\.internal\b'
  '\.lan\b'
)

# Soft-warn: PROBABLY a leak but may be legitimate. Reviewer decides
# per-hit. --strict mode treats these as deny.
WARN_PATTERNS=(
  # Internal decision-ID prefixes - legitimate as documentation but
  # reveal private-project tracking provenance.
  'D-MET-[0-9]+'
  'OBS-MET-[A-Z]+'
  'F-MET-[A-Z0-9-]+'
  'I-MET-[0-9]+'
  'D-VCS-[A-Z0-9-]+'
  'F-VCS-[A-Z0-9-]+'

  # Methodology-cycle markers and supervised-authority abbreviations.
  # Regulator names are public; specific firm-implementation paths might
  # not be, hence the soft-warn flag for eyeball-then-continue review.
  'Tiered Methodology Consolidation'
  'HCMC'
)

# Public author email expected on every public-repo commit. The pre-push
# audit verifies git config user.email matches this exactly.
PUBLIC_AUTHOR_EMAIL='stratosjl@gmail.com'

# Files / paths that we never scan: binary, generated, license texts,
# the audit infrastructure itself (which contains the deny patterns and
# would self-match), and the local overlay (which contains operator-
# flavored DENY entries by design).
#
# v1.20.1 (OBS-S67-03): two entries were corrected here.
#   - `\.git` matched any path CONTAINING that substring, so it silently
#     excluded `.gitignore`, `.gitattributes`, and everything under
#     `.github/`. Narrowed to the `.git/` directory itself, which
#     `git grep` never returns anyway, so the entry is now belt-and-braces
#     rather than a hole.
#   - `\.gitignore` was listed outright. It is a published file like any
#     other and it was on this list only because it names the overlay
#     filenames, which the operator-specific entries above already cover.
#     Removed, so `.gitignore` is scanned again.
# v1.20.2 (OBS-S67-04): bin/audit-history-baseline.txt and its .local.
# companion join the list. They enumerate accepted (path, pattern) pairs in
# published history, so they necessarily quote pattern literals and would
# self-match, exactly like the audit scripts above. This is an infrastructure
# exclusion by the same argument, not a widening of the list.
#
# Consumers must apply this regex to the PATH field only; see the
# scan_pattern() note in bin/publish-audit.sh.
SCAN_EXCLUDE='(__pycache__|(^|/)\.git/|LICENSE-CODE|LICENSE-CONTENT|bin/publish-audit\.sh|bin/publish-audit-state\.sh|bin/audit-patterns\.sh|bin/audit-patterns\.local\.sh|bin/audit-patterns\.local\.sh\.example|bin/audit-history-baseline\.txt|bin/audit-history-baseline\.local\.txt)'

# Source the operator-local private overlay if present. The overlay
# appends operator-flavored patterns to DENY_PATTERNS via array-append.
# The overlay file is gitignored (matches the existing *.local.* rule in
# .gitignore); absence is normal for fresh public clones and unprivileged
# CI environments.
_AUDIT_PATTERNS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_AUDIT_PATTERNS_LOCAL="$_AUDIT_PATTERNS_DIR/audit-patterns.local.sh"
if [ -r "$_AUDIT_PATTERNS_LOCAL" ]; then
  # shellcheck disable=SC1090
  . "$_AUDIT_PATTERNS_LOCAL"
fi
unset _AUDIT_PATTERNS_DIR _AUDIT_PATTERNS_LOCAL
