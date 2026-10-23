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
#
# This branch used to be silent. A run with the overlay loaded and a run
# without it left byte-identical variables behind, so no consumer could
# tell a fully armed scan from the public subset: measured on the
# operator machine at v1.27.0, DENY_PATTERNS is 9 entries with the
# overlay and 2 without it, and nothing said which had been loaded. The
# outcome is now published as data alongside the arrays.
#
# Data only, deliberately: this file prints nothing, because
# test-audit-patterns.py reads the arrays back by capturing the stdout of
# `source bin/audit-patterns.sh`, so anything written to stdout here
# would corrupt what it parses. Reporting and gating belong to the
# consumer, which is why absence stays a fact rather than an error here.
#
# Consumer contract, valid after the source line, alongside
# DENY_PATTERNS / WARN_PATTERNS / PUBLIC_AUTHOR_EMAIL / SCAN_EXCLUDE:
#   AUDIT_OVERLAY_FILE     resolved overlay path, set whether or not it exists
#   AUDIT_OVERLAY_STATE    present | absent | unreadable
#   AUDIT_OVERLAY_PRESENT  1 when the overlay was sourced, else 0
#   AUDIT_DENY_SCAFFOLD    DENY entries this public scaffold defines
#   AUDIT_DENY_OVERLAY     DENY entries the overlay appended, 0 when absent
#   AUDIT_WARN_SCAFFOLD    WARN entries this public scaffold defines
#   AUDIT_WARN_OVERLAY     WARN entries the overlay appended, 0 when absent
#
# The three-valued state exists because on a POSIX node [ -r ] is false
# both for a missing overlay and for a present-but-unreadable one, and
# only the first is the benign case described above. Collapsing them to
# a 0 would make a consumer print "no operator-local overlay present"
# over an overlay that is sitting right there and failed to load, which
# is the one sentence a leak gate must not get wrong.
#
# Measured 2026-09-04 on the fleet's Windows node: MSYS [ -r ] reports
# true for a file whose Windows ACL denies read, so the `unreadable`
# arm does not fire there. That case is still safe and was checked
# against this file at HEAD: both take the readable arm, the `.` then
# fails with "Permission denied", and under the callers' `set -e` the
# source aborts non-zero rather than scanning with the public subset.
# Loud and fail-closed either way, so the arm is a POSIX refinement and
# never the only thing standing between an unloadable overlay and a
# green scan.
_AUDIT_PATTERNS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT_OVERLAY_FILE="$_AUDIT_PATTERNS_DIR/audit-patterns.local.sh"
AUDIT_DENY_SCAFFOLD=${#DENY_PATTERNS[@]}
AUDIT_WARN_SCAFFOLD=${#WARN_PATTERNS[@]}
if [ -r "$AUDIT_OVERLAY_FILE" ]; then
  AUDIT_OVERLAY_STATE='present'
  AUDIT_OVERLAY_PRESENT=1
  # shellcheck disable=SC1090
  . "$AUDIT_OVERLAY_FILE"
elif [ -e "$AUDIT_OVERLAY_FILE" ]; then
  AUDIT_OVERLAY_STATE='unreadable'
  AUDIT_OVERLAY_PRESENT=0
else
  AUDIT_OVERLAY_STATE='absent'
  AUDIT_OVERLAY_PRESENT=0
fi
# Assignment form, never (( )): an arithmetic command evaluating to 0 exits
# 1, which under the callers' `set -e` would abort the source at exactly
# the no-overlay case this block exists to report.
AUDIT_DENY_OVERLAY=$(( ${#DENY_PATTERNS[@]} - AUDIT_DENY_SCAFFOLD ))
AUDIT_WARN_OVERLAY=$(( ${#WARN_PATTERNS[@]} - AUDIT_WARN_SCAFFOLD ))
unset _AUDIT_PATTERNS_DIR
