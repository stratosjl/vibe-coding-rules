#!/usr/bin/env bash
# audit-patterns.sh — sourceable pattern lists for the publish audit.
#
# This file is sourced by both bin/publish-audit.sh (pre-push, scans the
# local working tree) and bin/publish-audit-state.sh (post-publish, scans
# a fresh clone of the public repo). Single source of truth for the
# pattern set so the two audits cannot drift apart.
#
# Patterns are extended grep -E regexes. Add operator-specific strings
# here as new private projects spin up; do not duplicate the list inside
# either consumer script.
#
# Both arrays are exported so a sourcing script can iterate them after
# the source line.

# Hard-deny: NEVER allowed in a public commit. A single hit blocks the
# push (or the post-publish scan exits non-zero).
DENY_PATTERNS=(
  # === Firm name in every form we have observed on disk ===
  '[OPERATOR-DOMAIN]'
  's\.laspas@[OPERATOR-DOMAIN]'
  '[OPERATOR-FIRM]'        # spaced form
  '[OPERATOR-FIRM]'        # encoded-path form (no spaces)
  '[OPERATOR-FIRM-ABBR]'                          # standalone Latin acronym
  '[OPERATOR-FIRM-GR]'                              # Greek capitals (path-safe form, used in [OWN-PRIV]/[OPERATOR-FIRM-GR])

  # === Operator display name + variants ===
  '[OPERATOR-USER]'                    # Windows native path form, no space
  '[OPERATOR-NAME]'                       # standalone uppercase (e.g. [OPERATOR-NAME]-EE)
  's\.laspas\b'                      # any "[OPERATOR-SUFFIX]" suffix-agnostic

  # === Internal R&D project names ===
  '[INT-D]'
  '[INT-E]'
  '[INT-F]'

  # === Personal / private project folder paths ===
  '[OWN-PRIV]/[INT-H]'
  '[OWN-PRIV]/[OPERATOR-NAME]'
  '[OWN-PRIV]/[OPERATOR-FIRM-GR]'

  # === Operator-internal project codenames ===
  # Promoted from WARN to DENY at v1.2.0 after the OBS-vcroe-historical-leak-01
  # history rewrite scrubbed both names out of every pre-v1.2.0 commit. Future
  # leaks of either codename in any public commit are blocked at pre-push.
  '[INT-G]'
  '[INT-H]'

  # === [OPERATOR-FIRM-ABBR] client identifiers (sourced from observed project-dir names) ===
  '[OPERATOR-CLIENT-A]'
  '[OPERATOR-CLIENT-B]'
  '[OPERATOR-PROJ-A]'
  '[OPERATOR-PROJ-B]'

  # === Operator-private filesystem path roots ===
  '/home/[OPERATOR-USER]/Cloud'
  '/home/[OPERATOR-USER]/Projects'
  'OneDrive/[OPERATOR-DOCS]'
  'OneDrive - [OPERATOR-FIRM]'              # path fragment, spaced
  'OneDrive---[OPERATOR-FIRM]'              # path fragment, encoded
  '[OPERATOR-DOCS]'                     # operator's OneDrive doc-root anchor

  # === Private-network hostnames (extend as discovered) ===
  '\.internal\b'
  '\.lan\b'

  # === Operator-specific external service providers ([OPERATOR-FIRM-ABBR], low-FP class) ===
  # Added s55 POPULATION v2 batch 2 per W3 architectural decision
  # documented in the operator's private overlay slice. High-FP-private
  # provider names are intentionally NOT in this list to avoid publishing
  # those relationships through this pattern file. W2 (per-operator
  # private DENY layer) deferred to a future vc-roe session.
  '[external-svc-A]'
  '[external-svc-B]'
  '[external-svc-C]'
  '[external-svc-D]'
  '[external-svc-E]'
)

# Soft-warn: PROBABLY a leak but may be legitimate. Reviewer decides
# per-hit. --strict mode treats these as deny.
WARN_PATTERNS=(
  # Internal decision-ID prefixes — legitimate as documentation but
  # reveal private-project tracking provenance.
  'D-MET-[0-9]+'
  'OBS-MET-[A-Z]+'
  'F-MET-[A-Z0-9-]+'
  'I-MET-[0-9]+'
  'D-VCS-[A-Z0-9-]+'
  'F-VCS-[A-Z0-9-]+'

  # Specific compliance frameworks named in operator's day-job context
  # — regulator names are public, specific firm-implementation paths
  # might not be.
  'Tiered Methodology Consolidation'
  'HCMC'                             # supervised authority; legitimate to mention; flag for eyeball
)

# Public author email expected on every public-repo commit. The pre-push
# audit verifies git config user.email matches this exactly.
PUBLIC_AUTHOR_EMAIL='stratosjl@gmail.com'

# Files / paths that we never scan: binary, generated, license texts,
# the audit infrastructure itself (which contains the deny patterns and
# would self-match).
SCAN_EXCLUDE='(__pycache__|\.git|\.gitignore|LICENSE-CODE|LICENSE-CONTENT|bin/publish-audit\.sh|bin/publish-audit-state\.sh|bin/audit-patterns\.sh)'
