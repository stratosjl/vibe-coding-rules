#!/usr/bin/env bash
# vc-roe (vibe-coding-rules) one-line installer for Linux + macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/stratosjl/vibe-coding-rules/main/install.sh | bash
#
# What it does:
#   1. Verifies the `claude` CLI is on PATH.
#   2. Adds the vibe-coding-rules marketplace via HTTPS clone (no SSH key needed).
#   3. Installs the vc-roe plugin at user scope.
#   4. Prints next-steps (Claude Code restart required to pick up the new hooks).
#
# Idempotent: re-running is safe. The marketplace-add and plugin-install commands
# are no-ops when the marketplace / plugin is already present.

set -euo pipefail

REPO_URL="https://github.com/stratosjl/vibe-coding-rules.git"
MARKETPLACE_NAME="vibe-coding-rules"
PLUGIN_NAME="vc-roe"

color() {
  if [ -t 1 ] && [ "${TERM:-}" != "dumb" ]; then
    printf '\033[%sm%s\033[0m' "$1" "$2"
  else
    printf '%s' "$2"
  fi
}
info()  { color '36' "[vc-roe install] "; printf '%s\n' "$*"; }
warn()  { color '33' "[vc-roe install] "; printf '%s\n' "$*"; }
fail()  { color '31' "[vc-roe install] "; printf '%s\n' "$*"; exit 1; }

if ! command -v claude >/dev/null 2>&1; then
  fail "the 'claude' CLI is not on PATH. Install Claude Code first: https://docs.claude.com/en/docs/claude-code/setup"
fi

CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
info "claude CLI detected: ${CLAUDE_VERSION}"

info "adding marketplace ${MARKETPLACE_NAME} from ${REPO_URL}"
if ! claude plugin marketplace add "${REPO_URL}" 2>&1 | tee /tmp/vc-roe-install-marketplace.log; then
  if grep -qi 'already' /tmp/vc-roe-install-marketplace.log 2>/dev/null; then
    warn "marketplace already present; continuing"
  else
    fail "marketplace add failed; see /tmp/vc-roe-install-marketplace.log"
  fi
fi

info "installing plugin ${PLUGIN_NAME}@${MARKETPLACE_NAME} at user scope"
if ! claude plugin install "${PLUGIN_NAME}@${MARKETPLACE_NAME}" --scope user 2>&1 | tee /tmp/vc-roe-install-plugin.log; then
  if grep -qi 'already' /tmp/vc-roe-install-plugin.log 2>/dev/null; then
    warn "plugin already installed; continuing"
  else
    fail "plugin install failed; see /tmp/vc-roe-install-plugin.log"
  fi
fi

info "verifying"
claude plugin list | grep -i "${PLUGIN_NAME}" || warn "plugin not visible in 'claude plugin list' output; check ~/.claude/settings.json"

cat <<EOF

$(color '32' '[vc-roe install] success.')

Next steps:
  1. Close any running Claude Code window/process (CLI, desktop app, IDE extension).
     Claude Code holds an in-memory snapshot of plugin hooks across each process;
     a restart is required to pick them up.
  2. Reopen Claude Code in a project directory.
  3. The first reply will show: "Detected tier: T<N> (S<x>/C<y>), <label>."
  4. Override with /vc-roe:tier <T0..T4> if needed.

For local development or troubleshooting, see:
  https://github.com/stratosjl/vibe-coding-rules#local-development
  https://github.com/stratosjl/vibe-coding-rules#logs
EOF
