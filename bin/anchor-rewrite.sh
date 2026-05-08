#!/usr/bin/env bash
# anchor-rewrite.sh, rewrite the per-session methodology anchor TIER line.
#
# Usage: anchor-rewrite.sh <NEW_TIER>
#   <NEW_TIER>  one of T0, T1, T2, T3, T4
#
# Resolves the anchor file from /tmp in three layers (D-MET-61 v0.3.0):
#   Layer 1 (primary, v0.3.0): marker file written per-prompt by the
#     UserPromptSubmit hook at /tmp/claude-methodology-current-session-<cwd-hash>.
#     Eliminates the OBS-MET-AB multi-active-transcript race for the same-process
#     slash-command case (the live-observed failure mode at session 13).
#   Layer 2 (v0.2.2 fallback): transcript-derived session_id from the .jsonl in
#     the cwd-mapped project dir under ~/.claude/projects/, sorted by mtime
#     descending and tie-broken by size descending.
#   Layer 3 (legacy fallback): newest /tmp/claude-methodology-anchor-* file
#     modified in the last 5 seconds.
#
# Origin: extracted from commands/{tier,raise-tier,lower-tier}.md DRY refactor
# at v0.2.2 (closes OBS-MET-X). v0.3.0 adds Layer 1 marker-file resolution
# (closes OBS-MET-AB; CLAUDE_SESSION_ID env var probed at session 13 and
# confirmed NOT exposed to slash-command Bash). Layer 2 logic preserved
# verbatim from v0.2.2 (transcript-derived session_id; supersedes v0.1.8
# ls -t race).
#
# Standing rule: this script's path is canonical at <plugin-root>/bin/.
# Slash commands resolve the path via
# `find ~/.claude/plugins -name anchor-rewrite.sh -path '*vc-roe*'`
# (user-scope install layout). Plugin renamed from vc-roe to
# vc-roe at v1.0.0 (2026-05-06); legacy `*vc-roe*` path glob
# also accepted for forward-compat probes during user transitions.
#
# Residual cross-process race after v0.3.0: two windows' UPS firing within
# microseconds of each other could overwrite the marker file before either
# slash-command bash reads it. Unobserved in 6 weeks of soak; accepted as
# residual per D-MET-61.
#
# Exits 0 on success or "no anchor" (heartbeat hooks not installed); exits 1
# only on argument validation failure. Always emits a diagnostic to stdout.

set -euo pipefail

NEW_TIER="${1:-}"
case "$NEW_TIER" in
    T0|T1|T2|T3|T4) ;;
    *)
        echo "anchor-rewrite.sh: invalid tier '$NEW_TIER'; expected one of T0, T1, T2, T3, T4" >&2
        exit 1
        ;;
esac

ANCHOR=""
LAYER=""

# Layer 1 (v0.3.0, D-MET-61): UPS-written marker file with the calling session's session_id.
CWD_HASH=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))' 2>/dev/null)  # OBS-MET-AJ
MARKER="/tmp/claude-methodology-current-session-${CWD_HASH}"
if [ -f "$MARKER" ]; then
    SESSION_ID=$(head -1 "$MARKER" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$SESSION_ID" ] && [ -f "/tmp/claude-methodology-anchor-$SESSION_ID" ]; then
        ANCHOR="/tmp/claude-methodology-anchor-$SESSION_ID"
        LAYER="ups-marker"
    fi
fi

# Layer 2 (v0.2.2 fallback): transcript-derived session_id with size tie-break.
if [ -z "$ANCHOR" ]; then
    PROJ_DIR="$HOME/.claude/projects/$CWD_HASH"  # OBS-MET-AJ: reuse Layer 1 canonical encoding
    if [ -d "$PROJ_DIR" ]; then
        TRANSCRIPT=$(find "$PROJ_DIR" -maxdepth 1 -name '*.jsonl' -printf '%T@ %s %p\n' 2>/dev/null \
            | sort -k1,1nr -k2,2nr \
            | head -1 \
            | awk '{print $3}' || true)
        if [ -n "$TRANSCRIPT" ]; then
            SESSION_ID=$(basename "$TRANSCRIPT" .jsonl)
            ANCHOR="/tmp/claude-methodology-anchor-$SESSION_ID"
            LAYER="transcript-derived"
        fi
    fi
fi

# Layer 3 (legacy fallback): newest anchor file modified in the last 5 seconds.
if [ -z "$ANCHOR" ]; then
    ANCHOR=$(find /tmp -maxdepth 1 -name "claude-methodology-anchor-*" -newermt "@$(($(date +%s)-5))" 2>/dev/null | head -1 || true)
    [ -n "$ANCHOR" ] && LAYER="recent-mtime"
fi

if [ -n "$ANCHOR" ] && [ -f "$ANCHOR" ]; then
    sed -i "s/^TIER=.*/TIER=$NEW_TIER/" "$ANCHOR"
    echo "Anchor TIER rewritten to $NEW_TIER at $ANCHOR (resolved via $LAYER)"
else
    echo "No session-scoped anchor resolvable; v0.1.7+ hooks may not be installed; manual heartbeat discipline applies."
fi
