#!/usr/bin/env bash
# vc-roe-sync-aliases (renamed from vc-roe-sync-aliases at v1.0.0)
#
# Idempotently re-copies the plugin's slash command files to the user-level
# commands dir (~/.claude/commands/), retiring the manual-re-copy maintenance
# burden documented at OBS-MET-S.
#
# Origin: v0.2.0 ship at session 10 of the Tiered Methodology Consolidation
# project (2026-05-02). Background: plugin slash commands ship namespaced as
# /vc-roe:tier (was /vc-roe:tier pre-v1.0.0); the unqualified
# form (/tier) emits a "Unknown command" warning before auto-routing;
# user-level aliases at ~/.claude/commands/ take precedence and resolve
# cleanly. The user-level files diverge from plugin commands on every
# plugin update unless re-copied. This script does the re-copy.
#
# Usage: vc-roe-sync-aliases
#   (or:  ~/.claude/plugins/cache/.../vc-roe/<v>/bin/sync-user-aliases.sh)
#
# Idempotent: only copies if content differs. Safe to run on every plugin
# update, in a SessionStart hook, or via cron.
#
# Plugin's bin/ dir is auto-added to $PATH on install, so the operator can
# call this as `vc-roe-sync-aliases` after `claude plugin update`.

set -euo pipefail

MARKETPLACE_NAME="vibe-coding-rules"
USER_CMD_DIR="$HOME/.claude/commands"

# Resolve the installed bundle.
#
# v1.24.0 removes a dead branch: CACHE_BASE_VCROE and CACHE_BASE_LEGACY were
# byte-identical strings, because the v1.0.0 rename rewrote both sides of the
# pair. The elif could never be taken and the error message named the same path
# twice, which reads to whoever hits it as though two locations were searched.
#
# EXIT CONTRACT (F-EXIT-01): 0 ran, 1 found a defect, 2 usage, 3 NOT-RUNNABLE.
# Everything below is NOT-RUNNABLE: the environment lacks a dependency, which is
# a different fact from this script having found something wrong, and a caller
# must be able to tell them apart.
#
# Picking the HIGHEST version here is correct, unlike in the slash commands: this
# is an installer syncing aliases from the newest install, not a running session
# that must use the bundle it actually loaded. Said explicitly because the two
# look identical and one of them was a defect.
CACHE_BASE="$HOME/.claude/plugins/cache/$MARKETPLACE_NAME/vc-roe"
PLUGIN_NAME="vc-roe"
if [ ! -d "$CACHE_BASE" ]; then
  echo "NOT-RUNNABLE: plugin cache dir not found at $CACHE_BASE" >&2
  echo "NOT-RUNNABLE: is vc-roe@$MARKETPLACE_NAME installed? Nothing was copied." >&2
  exit 3
fi

# The scan is filtered to MAJOR.MINOR.PATCH before it is sorted. `sort -V` is a
# version sort applied to whatever names the directory happens to hold, so a
# stray bundle copy outsorts the real bundle: measured, 1.24.0.bak, 1.24.0-old
# and backup each beat 1.24.0, and the stale command bodies then landed in the
# alias dir this file's header says takes precedence, at rc=0, with nothing on
# stderr, under a summary line claiming they were byte-identical to the install.
# All 44 versions shipped so far match the pattern; a pre-release name is
# indistinguishable in shape from 1.24.0-old, so a cache with no match fails
# closed at exit 3 rather than guessing.
#
# The `|| true` is load-bearing. grep exits 1 on zero matches, pipefail
# propagates it, and the script would abort at rc=1 having printed nothing, so
# the NOT-RUNNABLE guard below would never be reached. Do not remove it.
LATEST_VERSION_DIR=$(ls -1 "$CACHE_BASE" 2>/dev/null | { grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -1)
if [ -z "$LATEST_VERSION_DIR" ]; then
  echo "NOT-RUNNABLE: no MAJOR.MINOR.PATCH version dir under $CACHE_BASE. Nothing was copied." >&2
  exit 3
fi

PLUGIN_CMD_DIR="$CACHE_BASE/$LATEST_VERSION_DIR/commands"
if [ ! -d "$PLUGIN_CMD_DIR" ]; then
  echo "NOT-RUNNABLE: plugin commands dir not found at $PLUGIN_CMD_DIR. Nothing was copied." >&2
  exit 3
fi

mkdir -p "$USER_CMD_DIR"

COPIED=0
SKIPPED=0
for cmd in tier raise-tier lower-tier audit-pass; do
  SRC="$PLUGIN_CMD_DIR/$cmd.md"
  DST="$USER_CMD_DIR/$cmd.md"
  if [ ! -f "$SRC" ]; then
    echo "WARN: plugin command not found at $SRC; skipping" >&2
    continue
  fi
  if [ -f "$DST" ] && cmp -s "$SRC" "$DST"; then
    SKIPPED=$((SKIPPED + 1))
  else
    cp "$SRC" "$DST"
    COPIED=$((COPIED + 1))
    echo "synced: $cmd.md"
  fi
done

echo ""
echo "$PLUGIN_NAME v$LATEST_VERSION_DIR: $COPIED command(s) updated, $SKIPPED unchanged."
echo "User-level aliases at $USER_CMD_DIR are byte-identical to the plugin install."
