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
  # NOTE: bare '[INT-A]' and '[INT-H]' are NOT in DENY (they appear in
  # the public repo at v1.1.3 baked into hook log messages + CHANGELOG +
  # methodology-content as forensic context tags). They live in WARN
  # below pending the dedicated scrub-and-remediate session
  # (OBS-vcroe-historical-leak-01). Path-prefixed forms are still hard
  # deny because they encode operator's filesystem layout, which is
  # different from the project name being mentioned in prose.
  'OWN/[EXAMPLE-PROJ]'
  '[OWN-PRIV]/[OPERATOR-NAME]'
  '[OWN-PRIV]/[OPERATOR-FIRM-GR]'

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

  # === Project-name forensic tags found in the public repo at v1.1.3 ===
  # These are scheduled for scrub in a dedicated remediation session per
  # OBS-vcroe-historical-leak-01 (gate decision: surface, don't
  # auto-remediate). Promoted to DENY once history is rewritten or
  # references are anonymised.
  '[INT-A]'                       # FOUND IN PUBLIC v1.1.3: hooks/stop.py, test-heartbeat.py, CHANGELOG.md
  '[INT-H]'                          # FOUND IN PUBLIC v1.1.3: hooks/post-tool-use.py, methodology-content/T*.md, CHANGELOG.md
)

# Public author email expected on every public-repo commit. The pre-push
# audit verifies git config user.email matches this exactly.
PUBLIC_AUTHOR_EMAIL='stratosjl@gmail.com'

# Files / paths that we never scan: binary, generated, license texts,
# the audit infrastructure itself (which contains the deny patterns and
# would self-match).
SCAN_EXCLUDE='(__pycache__|\.git|\.gitignore|LICENSE-CODE|LICENSE-CONTENT|bin/publish-audit\.sh|bin/publish-audit-state\.sh|bin/audit-patterns\.sh)'
