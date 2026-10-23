#!/usr/bin/env bash
# anchor-rewrite.sh, rewrite the per-session methodology anchor TIER line.
#
# Usage: anchor-rewrite.sh <NEW_TIER>
#        anchor-rewrite.sh --resolve-only        # print the anchor path + layer, mutate nothing
#   <NEW_TIER>  one of T0, T1, T2, T3, T4
#
# EXIT CONTRACT (v1.27.0, F-EXIT-01). A check's CANNOT-RUN must never render
# identically to its ran-and-succeeded or ran-and-found-a-defect result:
#   0  ran, anchor resolved, TIER rewritten (or --resolve-only succeeded)
#   2  USAGE error: the caller passed a bad argument
#   3  NOT-RUNNABLE: no session anchor resolved, so heartbeat enforcement is
#      NOT in force. Nothing was written and nothing is known about the tier.
#
# ⚠ Until v1.27.0 this script exited 0 when no anchor resolved, printing a
# diagnostic nobody was obliged to read. A caller doing `bash anchor-rewrite.sh
# T4 && echo ok` therefore reported success while the heartbeat anchor was never
# touched and the UserPromptSubmit hook went on short-circuiting at the old
# tier. That is CANNOT-RUN folded into PASS on an enforcement primitive.
#
# Resolves the anchor file in three layers:
#   Layer 0 (primary, v1.27.0): $CLAUDE_CODE_SESSION_ID, exported by Claude Code
#     into the environment of a slash command's Bash block. Per-process, so no
#     other window can clobber it, which dissolves the cross-process marker race
#     the header below used to record as an accepted residual.
#
#     ⚠ HISTORY, kept because it is the reason three fallback layers exist: the
#     v0.3.0 header stated "CLAUDE_SESSION_ID env var probed at session 13 and
#     confirmed NOT exposed to slash-command Bash". The probe used the wrong
#     NAME. The variable is CLAUDE_CODE_SESSION_ID, and it is exposed. Measured
#     2026-09-04 on the Windows node: the value equalled the running session's
#     anchor id exactly, and the derived anchor path existed. A negative result
#     about an identifier is a claim about the identifier you typed, not about
#     the thing; re-probe by enumerating (`env | grep -i session`) rather than by
#     testing one remembered name.
#
#   Layer 1 (v0.3.0): marker file written per-prompt by the UserPromptSubmit
#     hook at /tmp/claude-methodology-current-session-<cwd-hash>.
#   Layer 2 (v0.2.2): transcript-derived session_id from the .jsonl in the
#     cwd-mapped project dir under ~/.claude/projects/, newest mtime, size
#     tie-break.
#
# Layers 1 and 2 are retained unchanged. Layer 0 is measured on ONE node in ONE
# session, so it is strictly additive: a harness build that does not export the
# variable falls through to exactly the previous behaviour.
#
# ⚠ A legacy Layer 3 was REMOVED here. It took the first anchor in the temp
# dir with an mtime under 5 seconds old, with no session scope and no cwd scope,
# unlike layers 0, 1 and 2, so on a box running concurrent sessions it rewrote a
# DIFFERENT window's TIER line and reported success. Reproduced 2026-09-04 in a
# private anchor dir holding two foreign anchors: it resolved layer=recent-mtime
# and rewrote claude-methodology-anchor-FOREIGN-AAA from TIER=T0 to TIER=T4 at
# rc=0. It also picked the OLDER of the two, because that pipeline was
# `find ... | head -1` with no sort anywhere in it while this header promised
# "newest".
#
# It could not be scoped and it added no reach. An anchor is named by session id
# alone, and its body carries T0, LAST_HEARTBEAT, TIER, PLUGIN_ROOT and
# PLUGIN_VERSION, so nothing in it identifies a cwd the way layers 1 and 2 scope
# themselves. And every production caller is a slash command's Bash block, which
# runs only after a prompt was submitted: user-prompt-submit.py calls
# write_session_marker() before read_anchor(), and both of its write_anchor()
# call sites sit downstream of that, so any anchor young enough for the 5 second
# window already had a Layer 1 marker written for the session that owns it.
# Removing it routes the no-anchor case to the exit 3 NOT-RUNNABLE path below,
# which is the honest answer rather than a write to a stranger's anchor.
#
# The anchor DIRECTORY is probed rather than hard-coded, because the Python
# hooks use tempfile.gettempdir() while this script historically hard-coded
# /tmp, and those differ per runtime on Windows (OBS-MET-AK).
#
# Standing rule: this script's path is canonical at <plugin-root>/bin/.

set -uo pipefail

# ---------------------------------------------------------------- arguments --
RESOLVE_ONLY=0
NEW_TIER="${1:-}"
if [ "$NEW_TIER" = "--resolve-only" ]; then
    RESOLVE_ONLY=1
else
    case "$NEW_TIER" in
        T0|T1|T2|T3|T4) ;;
        *)
            echo "USAGE: anchor-rewrite.sh <T0|T1|T2|T3|T4> | --resolve-only" >&2
            echo "anchor-rewrite.sh: invalid tier '$NEW_TIER'" >&2
            exit 2
            ;;
    esac
fi

# ------------------------------------------------------- anchor directories --
# Ordered: the value this script used to hard-code first, then the runtimes'
# own notions of temp. Duplicates are harmless; the first HIT wins.
# $VC_ROE_ANCHOR_DIR, when set, REPLACES the probe list. It exists so the
# negative test can remove this script's dependency and assert exit 3 without a
# container: the real temp dir holds every live session's anchor AND the test
# suite's own ktest-* residue, so layer 1 would otherwise resolve to one of
# those and the CANNOT-RUN arm could never be exercised. Measured 2026-09-04:
# the first attempt at that negative test resolved via ups-marker to a leftover
# ktest anchor and reported exit 0. Corrupting the input only exercises the arm
# that already works; the arm worth proving is the one where the dependency is
# gone, and it cannot be reached without this seam.
anchor_dirs() {
    if [ -n "${VC_ROE_ANCHOR_DIR:-}" ]; then
        echo "$VC_ROE_ANCHOR_DIR"
        return 0
    fi
    printf '%s\n' "/tmp" "${TMPDIR:-}" "${TEMP:-}" "${TMP:-}" | awk 'NF && !seen[$0]++'
}

anchor_for() {
    # $1 = session id. Echoes the first existing anchor path, or nothing.
    local sid="$1" d
    [ -n "$sid" ] || return 0
    while IFS= read -r d; do
        if [ -f "$d/claude-methodology-anchor-$sid" ]; then
            printf '%s\n' "$d/claude-methodology-anchor-$sid"
            return 0
        fi
    done < <(anchor_dirs)
    return 0
}

ANCHOR=""
LAYER=""

# -- Layer 0 (v1.27.0): the harness-exported session id, per-process, no race --
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
    ANCHOR="$(anchor_for "$CLAUDE_CODE_SESSION_ID")"
    [ -n "$ANCHOR" ] && LAYER="env-session-id"
fi

# cwd hash is needed by layers 1 and 2 only; compute it lazily.
CWD_HASH=""
if [ -z "$ANCHOR" ]; then
    CWD_HASH=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))' 2>/dev/null) || CWD_HASH=""
fi

# ------------------------------- Layer 1 (v0.3.0): UPS-written marker file ----
if [ -z "$ANCHOR" ] && [ -n "$CWD_HASH" ]; then
    while IFS= read -r d; do
        MARKER="$d/claude-methodology-current-session-${CWD_HASH}"
        if [ -f "$MARKER" ]; then
            SESSION_ID=$(head -1 "$MARKER" 2>/dev/null | tr -d '[:space:]')
            ANCHOR="$(anchor_for "$SESSION_ID")"
            if [ -n "$ANCHOR" ]; then LAYER="ups-marker"; break; fi
        fi
    done < <(anchor_dirs)
fi

# --------------------------- Layer 2 (v0.2.2): transcript-derived session id --
if [ -z "$ANCHOR" ] && [ -n "$CWD_HASH" ]; then
    PROJ_DIR="$HOME/.claude/projects/$CWD_HASH"
    if [ -d "$PROJ_DIR" ]; then
        TRANSCRIPT=$(find "$PROJ_DIR" -maxdepth 1 -name '*.jsonl' -printf '%T@ %s %p\n' 2>/dev/null \
            | sort -k1,1nr -k2,2nr \
            | head -1 \
            | awk '{print $3}')
        if [ -n "${TRANSCRIPT:-}" ]; then
            ANCHOR="$(anchor_for "$(basename "$TRANSCRIPT" .jsonl)")"
            [ -n "$ANCHOR" ] && LAYER="transcript-derived"
        fi
    fi
fi

# ------------------------------------------------------------------ verdict --
if [ -z "$ANCHOR" ] || [ ! -f "$ANCHOR" ]; then
    echo "NOT-RUNNABLE: no session-scoped methodology anchor resolved (tried env-session-id, ups-marker, transcript-derived)."
    echo "NOT-RUNNABLE: the v0.1.7+ heartbeat hooks may not be installed. Nothing was written; heartbeat enforcement is NOT in force at any tier."
    exit 3
fi

if [ "$RESOLVE_ONLY" = "1" ]; then
    echo "anchor=$ANCHOR layer=$LAYER"
    exit 0
fi

sed -i "s/^TIER=.*/TIER=$NEW_TIER/" "$ANCHOR" || {
    echo "NOT-RUNNABLE: anchor resolved at $ANCHOR but could not be rewritten." >&2
    exit 3
}

# READ THE WRITE BACK BEFORE ANNOUNCING IT. sed SUCCEEDS when its substitution
# matches nothing, so the || arm above catches only sed failing to run. An
# anchor carrying no TIER= line therefore left this script printing "Anchor
# TIER rewritten" at exit 0 over a byte-identical file. Reproduced 2026-09-04:
# a fixture of LAST_HEARTBEAT=0 plus SOMETHING=else came back sha256-identical
# (31a1223047a684a1 both sides) after `anchor-rewrite.sh T4` reported success,
# against a control fixture carrying TIER= whose sha DID move. That is
# CANNOT-RUN folded into PASS at the one point where a write happens, which is
# the class the rest of this file was rewritten to eliminate. Exit 3 rather
# than 1 because the tier was not applied and nothing is known about it, which
# is the same state as the resolved-but-unwritable arm just above.
if ! grep -qx "TIER=$NEW_TIER" "$ANCHOR"; then
    echo "NOT-RUNNABLE: $ANCHOR has no TIER=$NEW_TIER line after the rewrite, so it carried no TIER= line to substitute. Nothing was written; heartbeat enforcement is NOT in force at any tier." >&2
    exit 3
fi
echo "Anchor TIER rewritten to $NEW_TIER at $ANCHOR (resolved via $LAYER)"
exit 0
