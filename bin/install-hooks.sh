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
# Exit codes (the plugin's contract):
#   0  installed, or already installed (no change)
#   1  ran, and this clone is incomplete (a .githooks/ file is missing)
#   2  usage error: not inside a git working tree
#   3  NOT-RUNNABLE: git is not on PATH
#
# After install, every `git push` from this clone runs the combined-audit
# harness; any tool FAIL blocks the push.

set -euo pipefail

# The `|| REPO_ROOT=""` is load-bearing under `set -e`: a bare assignment
# takes the command substitution's exit status, so a git failure used to
# abort the script right here, with 0 bytes on both streams (rc=128 outside
# a work tree, rc=127 with git off PATH), and the guard below never ran.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || REPO_ROOT=""
if [ -z "$REPO_ROOT" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "install-hooks: git is not on PATH; cannot bootstrap hooks." >&2
    exit 3
  fi
  echo "install-hooks: not inside a git working tree." >&2
  exit 2
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

# Surface but do not touch any pre-existing .git/hooks/ content. With
# core.hooksPath set, git ignores that directory ENTIRELY, so pre-push is
# not the only name that stops firing. Nothing here is auto-removed
# (operator-local content, T4 confirmation-gate territory).
#
# `git rev-parse --git-path hooks` is deliberately NOT used to locate it:
# measured on git 2.54.0.windows.1, that resolves to core.hooksPath once
# this script has set it, so the enumeration below would compare .githooks
# against itself and report nothing, every time, on every clone.
GIT_DIR_PATH="$(git rev-parse --absolute-git-dir 2>/dev/null)" || GIT_DIR_PATH="$REPO_ROOT/.git"
LOCAL_HOOKS_DIR="$GIT_DIR_PATH/hooks"

LEGACY="$LOCAL_HOOKS_DIR/pre-push"
if [ -e "$LEGACY" ]; then
  echo ""
  echo "install-hooks: NOTE: $LEGACY exists from a prior install."
  echo "  With core.hooksPath=$HOOKS_DIR set, that file is now INACTIVE."
  echo "  You may delete it manually for cleanliness:"
  echo "    rm $LEGACY"
fi

# Every other local hook whose name $HOOKS_DIR does not carry has no
# dispatcher and no replacement: it just stops firing, silently, and git
# says nothing. Derive that set from what this clone actually has, rather
# than from a list of names somebody remembered.
SHADOWED_COUNT=0
for LOCAL_HOOK in "$LOCAL_HOOKS_DIR"/*; do
  if [ ! -f "$LOCAL_HOOK" ]; then
    continue
  fi
  HOOK_NAME="${LOCAL_HOOK##*/}"
  case "$HOOK_NAME" in
    *.sample) continue ;;
  esac
  if [ -e "$HOOKS_DIR/$HOOK_NAME" ]; then
    continue
  fi
  if [ "$SHADOWED_COUNT" -eq 0 ]; then
    echo ""
    echo "install-hooks: WARNING: core.hooksPath=$HOOKS_DIR makes git ignore"
    echo "  $LOCAL_HOOKS_DIR entirely. These local hooks have no counterpart"
    echo "  in $HOOKS_DIR/ and are now INACTIVE:"
  fi
  SHADOWED_COUNT=$((SHADOWED_COUNT + 1))
  echo "    $HOOK_NAME"
done
if [ "$SHADOWED_COUNT" -gt 0 ]; then
  echo "  To keep one firing, copy $HOOKS_DIR/post-commit to $HOOKS_DIR/<name>"
  echo "  and change the hook name in its LOCAL= line, or run that tooling"
  echo "  another way. Nothing was deleted or modified."
fi

echo ""
echo "install-hooks: done. Every 'git push' from this clone will now run"
echo "  bin/publish-audit-combined.sh and block on any FAIL."
echo "  post-commit / post-merge / post-checkout dispatchers will forward to"
echo "  operator-local .git/hooks/post-<event> if present (no-op otherwise)."
