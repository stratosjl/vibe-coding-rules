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

# Auto-detect plugin name in cache (vc-roe post-v1.0.0; vc-roe
# pre-v1.0.0). Prefer vc-roe if both exist (operator transitioning).
CACHE_BASE_VCROE="$HOME/.claude/plugins/cache/$MARKETPLACE_NAME/vc-roe"
CACHE_BASE_LEGACY="$HOME/.claude/plugins/cache/$MARKETPLACE_NAME/vc-roe"
if [ -d "$CACHE_BASE_VCROE" ]; then
  PLUGIN_NAME="vc-roe"
  CACHE_BASE="$CACHE_BASE_VCROE"
elif [ -d "$CACHE_BASE_LEGACY" ]; then
  PLUGIN_NAME="vc-roe"
  CACHE_BASE="$CACHE_BASE_LEGACY"
else
  echo "ERROR: plugin cache dir not found at $CACHE_BASE_VCROE or $CACHE_BASE_LEGACY" >&2
  echo "Is vc-roe@$MARKETPLACE_NAME (or vc-roe@$MARKETPLACE_NAME pre-v1.0.0) installed?" >&2
  exit 1
fi

LATEST_VERSION_DIR=$(ls -1 "$CACHE_BASE" | sort -V | tail -1)
if [ -z "$LATEST_VERSION_DIR" ]; then
  echo "ERROR: no version dirs under $CACHE_BASE" >&2
  exit 1
fi

PLUGIN_CMD_DIR="$CACHE_BASE/$LATEST_VERSION_DIR/commands"
if [ ! -d "$PLUGIN_CMD_DIR" ]; then
  echo "ERROR: plugin commands dir not found at $PLUGIN_CMD_DIR" >&2
  exit 1
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
