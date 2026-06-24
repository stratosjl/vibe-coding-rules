#!/usr/bin/env python3
"""UserPromptSubmit hook for the VC-RoE (Vibe Coding · Rules of Engagement) Claude Code plugin.

Renamed from vc-roe at v1.0.0 (2026-05-06).

Companion to session-start.py (which writes the anchor file on SessionStart).
Reads the anchor file, computes elapsed-since-T0 and elapsed-since-LAST_HEARTBEAT,
emits a system-reminder context tag with the clock state. At OVERDUE-2X
(>= 30 min since last heartbeat), auto-advances LAST_HEARTBEAT and emits an
additional anomaly reminder (layered fail-safe per D-MET-41).

Tier-aware: reads TIER from anchor; short-circuits to no-op at T0/T1
(heartbeat rule is T2+ per D-MET-33).

Pure stdlib. Never throws. Logs to ~/.claude/methodology-hook.log.
Source basis: D-MET-33 (heartbeat T2+ cadence), D-MET-39..D-MET-41 (v0.1.7
hotfix structural enforcement).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# OBS-MET-AI: Windows stdout defaults to cp1252 which cannot encode non-ASCII.
# UPS output is currently ASCII but reconfigure defensively for parity with
# session-start.py and to keep future heartbeat-tag content (Greek labels,
# arrows, etc.) safe to emit. No-op on non-reconfigurable streams.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

ROUTINE_VERSION = "1.18.1"
ANCHOR_DIR = Path(tempfile.gettempdir())  # OBS-MET-AK: cross-runtime /tmp divergence on Windows
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"

CADENCE_SEC = 15 * 60
OVERDUE_2X_SEC = 30 * 60
TIER_ACTIVE = {"T2", "T3", "T4"}


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_anchor(session_id: str) -> Optional[dict[str, str]]:
    path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_anchor(session_id: str, fields: dict[str, str]) -> None:
    path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
    try:
        ordered = "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n"
        path.write_text(ordered, encoding="utf-8")
    except Exception:
        pass


def write_session_marker(session_id: str, cwd: Optional[str]) -> None:
    # v0.3.0 D-MET-61: marker file consumed by anchor-rewrite.sh as Layer 1
    # session-id resolution to eliminate the OBS-MET-AB multi-active-transcript
    # race for slash-command invocations within the calling session's process.
    if not session_id or not cwd:
        return
    try:
        cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd).resolve()))  # OBS-MET-AJ
        marker = ANCHOR_DIR / f"claude-methodology-current-session-{cwd_dashed}"  # OBS-MET-AK
        marker.write_text(session_id + "\n", encoding="utf-8")
    except Exception:
        pass


def emit(additional_context: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }
    try:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def main() -> int:
    started = time.time()
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({"ts": time.time(), "hook": "user-prompt-submit", "phase": "stdin", "error": str(e)})
        return 0

    session_id = event.get("session_id") or ""
    if not session_id:
        return 0

    write_session_marker(session_id, event.get("cwd"))

    anchor = read_anchor(session_id)
    if not anchor:
        return 0

    tier = anchor.get("TIER", "T0")
    if tier not in TIER_ACTIVE:
        return 0

    try:
        t0 = int(anchor.get("T0", "0"))
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
    except ValueError:
        return 0
    if t0 <= 0:
        return 0

    now = int(started)
    elapsed_t0 = now - t0
    last_hb_effective = last_hb if last_hb > 0 else t0
    elapsed_since_hb = now - last_hb_effective

    elapsed_t0_min = elapsed_t0 // 60
    elapsed_hb_min = elapsed_since_hb // 60
    next_due_min = elapsed_t0_min + max(0, (CADENCE_SEC - elapsed_since_hb) // 60)

    if elapsed_since_hb >= OVERDUE_2X_SEC:
        status = "OVERDUE-2X"
    elif elapsed_since_hb >= CADENCE_SEC:
        status = "OVERDUE"
    else:
        status = "OK"

    last_hb_display = (last_hb - t0) // 60 if last_hb > 0 else 0
    clock_tag = (
        f"[session-clock: T+{elapsed_t0_min}m | "
        f"last-heartbeat: T+{last_hb_display}m | "
        f"next-due: T+{next_due_min}m, {status}]"
    )

    if status == "OK":
        ctx = f"<system-reminder>\n{clock_tag}\n</system-reminder>"
    elif status == "OVERDUE":
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE per D-MET-33. Emit the session-health heartbeat "
            "block at the start of your reply: 5 substantive content lines "
            "(1: session goal restated; 2: scope status; 3: anomaly status; "
            "4: side-questions status; 5: background tasks) followed by the "
            f"literal sentinel [heartbeat-fired:T+{elapsed_t0_min}m] on its own "
            "line. Six lines total. Layered fail-safe per D-MET-41.\n"
            "</system-reminder>"
        )
    else:
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE-2X per D-MET-33: two consecutive cadences elapsed "
            "without sentinel detection. Auto-advancing LAST_HEARTBEAT as the "
            "layered fail-safe per D-MET-41. Emit a recovery session-health "
            "heartbeat block at the start of your reply: 5 substantive content "
            "lines (1: session goal restated; 2: scope status; 3: anomaly status "
            "with explicit acknowledgement of the missed cadences; 4: "
            "side-questions status; 5: background tasks) followed by the literal "
            f"sentinel [heartbeat-fired:T+{elapsed_t0_min}m] on its own line. "
            "Six lines total.\n</system-reminder>"
        )
        # v1.1.1: preserve LAST_PTU_TAG_SEC across the auto-advance so the
        # PostToolUse rate-limit window survives an OVERDUE-2X recovery.
        last_ptu_tag = anchor.get("LAST_PTU_TAG_SEC", "0")
        write_anchor(session_id, {
            "T0": str(t0),
            "LAST_HEARTBEAT": str(now),
            "TIER": tier,
            "LAST_PTU_TAG_SEC": last_ptu_tag,
        })

    emit(ctx)

    append_log({
        "ts": time.time(),
        "hook": "user-prompt-submit",
        "session_id": session_id,
        "tier": tier,
        "elapsed_t0_min": elapsed_t0_min,
        "elapsed_since_hb_min": elapsed_hb_min,
        "status": status,
        "routine_version": ROUTINE_VERSION,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
