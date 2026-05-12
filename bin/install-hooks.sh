#!/usr/bin/env bash
# install-hooks.sh - one-time bootstrap for the cross-machine pre-push hook.
#
# Runs `git config --local core.hooksPath .githooks` so that the version-
# controlled hooks under .githooks/ supersede the per-clone .git/hooks/
# directory. Idempotent: safe to re-run.
#
# v1.8.0 ship target per F-61-02 forward obligation. Replaces the s61
# operator-local stopgap which required manual reinstall per machine.
#
# Usage:
#   bash bin/install-hooks.sh
#
# After install, every `git push` from this clone runs the combined-audit
# harness; any tool FAIL blocks the push.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
  echo "install-hooks: not inside a git working tree." >&2
  exit 1
fi
cd "$REPO_ROOT"

HOOKS_DIR=".githooks"
if [ ! -d "$HOOKS_DIR" ]; then
  echo "install-hooks: $REPO_ROOT/$HOOKS_DIR not found; this clone may be incomplete." >&2
  exit 1
fi

PREPUSH="$HOOKS_DIR/pre-push"
if [ ! -r "$PREPUSH" ]; then
  echo "install-hooks: $REPO_ROOT/$PREPUSH not found; cannot proceed." >&2
  exit 1
fi

if [ ! -x "$PREPUSH" ]; then
  echo "install-hooks: making $PREPUSH executable..."
  chmod +x "$PREPUSH"
fi

# v1.9.1 (F-66-01): the three post-* dispatchers must also be executable.
# They forward to operator-local .git/hooks/post-<event> if present; without
# core.hooksPath redirect they would be inert per v1.8.0+v1.8.1 behaviour.
for HOOK in post-commit post-merge post-checkout; do
  DISPATCH="$HOOKS_DIR/$HOOK"
  if [ ! -r "$DISPATCH" ]; then
    echo "install-hooks: $REPO_ROOT/$DISPATCH not found; this clone may be incomplete." >&2
    exit 1
  fi
  if [ ! -x "$DISPATCH" ]; then
    echo "install-hooks: making $DISPATCH executable..."
    chmod +x "$DISPATCH"
  fi
done

CURRENT=$(git config --local --get core.hooksPath 2>/dev/null || echo '')
if [ "$CURRENT" = "$HOOKS_DIR" ]; then
  echo "install-hooks: core.hooksPath already set to $HOOKS_DIR (no change)."
else
  git config --local core.hooksPath "$HOOKS_DIR"
  echo "install-hooks: core.hooksPath set to $HOOKS_DIR (was: ${CURRENT:-unset})."
fi

# Surface but do not touch any pre-existing .git/hooks/pre-push. With
# core.hooksPath set, that file becomes inactive; operator may delete
# manually for cleanliness, but it is not auto-removed here (operator-
# local content, T4 confirmation-gate territory).
LEGACY=".git/hooks/pre-push"
if [ -e "$LEGACY" ]; then
  echo ""
  echo "install-hooks: NOTE: $REPO_ROOT/$LEGACY exists from a prior install."
  echo "  With core.hooksPath=$HOOKS_DIR set, that file is now INACTIVE."
  echo "  You may delete it manually for cleanliness:"
  echo "    rm $REPO_ROOT/$LEGACY"
fi

echo ""
echo "install-hooks: done. Every 'git push' from this clone will now run"
echo "  bin/publish-audit-combined.sh and block on any FAIL."
echo "  post-commit / post-merge / post-checkout dispatchers will forward to"
echo "  operator-local .git/hooks/post-<event> if present (no-op otherwise)."
