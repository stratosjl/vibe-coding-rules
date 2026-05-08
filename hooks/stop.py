#!/usr/bin/env python3
"""Stop hook for the VC-RoE (Vibe Coding · Rules of Engagement) Claude Code plugin.

Renamed from vc-roe at v1.0.0 (2026-05-06).

Companion to user-prompt-submit.py. Fires when an assistant turn ends.
Reads the transcript_path from the event, collects the entire most-recent
assistant turn (which may span multiple JSONL lines: one per text block,
one per tool_use, etc.), greps the concatenated text for the heartbeat
sentinel `[heartbeat-fired:T+<n>m]`. If found, advances LAST_HEARTBEAT
in the session anchor file to the current epoch.

This is the primary heartbeat-fire detection per D-MET-41 layered
fail-safe (the auto-advance in user-prompt-submit.py is the safety net
for missed Stop events).

Pure stdlib. Never throws. Logs to ~/.claude/methodology-hook.log.

v0.2.0 fix for OBS-MET-V (session 10 audit): previous implementation
returned only the LAST transcript line's assistant text, missing sentinels
emitted in earlier text blocks within the same turn (when the turn had
tool calls + multiple text blocks, which is common at T3+ sessions). The
new implementation walks backwards from the end of the transcript, gathers
all consecutive assistant-role lines, concatenates their text, then greps.

v0.3.0 fix for OBS-MET-AA (session 13 root-cause): the v0.2.0 backwards
walk broke at the first non-assistant role; tool_result lines are emitted
as role="user" inside a tool-bearing assistant turn, so the walk halted
at the tool boundary BEFORE reaching the pre-tool sentinel. Now skip
role="user" lines that carry tool_result content blocks; treat them as
internal-to-turn rather than turn-boundary.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# OBS-MET-AI: Windows stdout defaults to cp1252 which cannot encode non-ASCII.
# Stop hook's normal output is ASCII but reconfigure defensively for parity
# with session-start.py and to keep future diagnostic content safe to emit.
# No-op on non-reconfigurable streams.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

ROUTINE_VERSION = "1.1.0"
ANCHOR_DIR = Path("/tmp")
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"
UPS_MARKER_PREFIX = "claude-methodology-current-session-"

SENTINEL_RE = re.compile(r"\[heartbeat-fired:T\+\d+m\]")


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def check_marker_mismatch(session_id: str, cwd_raw: Optional[str]) -> None:
    """v0.4.0 OBS-MET-AF closure: marker-mismatch diagnostic.

    Reads /tmp/claude-methodology-current-session-<cwd-hash> at session close
    and compares its writer's session_id against the calling session_id. If
    they differ, logs a marker-mismatch diagnostic so cross-process anchor-
    rewrite race forensics surface in soak data. Recording-only; no behaviour
    change. Per OBS-MET-AF body recommendation (c) "instrument first; design
    later if data warrants".
    """
    if not session_id or not cwd_raw:
        return
    try:
        cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd_raw).resolve()))  # OBS-MET-AJ
        marker = Path("/tmp") / f"{UPS_MARKER_PREFIX}{cwd_dashed}"
        if not marker.is_file():
            return
        marker_session = marker.read_text(encoding="utf-8").strip()
        if marker_session and marker_session != session_id:
            append_log({
                "ts": time.time(),
                "hook": "stop",
                "diagnostic": "marker-mismatch",
                "session_id": session_id,
                "marker_session_id": marker_session,
                "cwd": cwd_raw,
                "routine_version": ROUTINE_VERSION,
            })
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


def extract_assistant_text(turn: Any) -> str:
    """Best-effort extract of assistant-message text across transcript shapes."""
    if not isinstance(turn, dict):
        return ""
    role = turn.get("role") or turn.get("type")
    if role != "assistant":
        msg = turn.get("message")
        if isinstance(msg, dict):
            return extract_assistant_text(msg)
        return ""
    content = turn.get("content")
    if content is None:
        msg = turn.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def last_assistant_text(transcript_path: str) -> Optional[str]:
    """Collect ALL assistant text from the most-recent assistant turn.

    The transcript is JSONL with one event per line. An assistant turn may
    span multiple lines (one per text block, one per tool_use, etc.). Walk
    backwards from the end of the transcript, accumulating assistant-role
    text until we hit a non-assistant role or run out of lines. Return the
    concatenated text or None.

    v0.2.0 fix for OBS-MET-V: previous implementation returned only the
    first non-empty assistant line walking backwards, missing sentinels
    emitted in earlier text blocks within the same multi-line turn.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    parts: list[str] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            turn = json.loads(line)
        except Exception:
            continue

        role = turn.get("role") or turn.get("type") or ""
        if not role:
            inner = turn.get("message")
            if isinstance(inner, dict):
                role = inner.get("role") or inner.get("type") or ""

        if role == "assistant":
            t = extract_assistant_text(turn)
            if t:
                parts.append(t)
            continue

        # v0.3.0 fix for OBS-MET-AA: tool_result lines have role="user" but
        # carry no prompt-boundary semantics; skip them so the backwards walk
        # reaches pre-tool sentinel emissions in the same multi-line turn.
        if role == "user":
            content = turn.get("content")
            if content is None:
                msg = turn.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
            if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result"
                for c in content
            ):
                continue

        if role:
            break

    if not parts:
        return None

    parts.reverse()
    return "\n".join(parts)


def main() -> int:
    started = time.time()
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({"ts": time.time(), "hook": "stop", "phase": "stdin", "error": str(e)})
        return 0

    session_id = event.get("session_id") or ""
    transcript_path = event.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return 0

    check_marker_mismatch(session_id, event.get("cwd"))

    anchor = read_anchor(session_id)
    if not anchor:
        return 0

    text = last_assistant_text(transcript_path)
    if not text:
        return 0

    if not SENTINEL_RE.search(text):
        append_log({
            "ts": time.time(),
            "hook": "stop",
            "session_id": session_id,
            "sentinel_found": False,
            "routine_version": ROUTINE_VERSION,
        })
        return 0

    now = int(started)
    write_anchor(session_id, {
        "T0": anchor.get("T0", "0"),
        "LAST_HEARTBEAT": str(now),
        "TIER": anchor.get("TIER", "T0"),
    })

    append_log({
        "ts": time.time(),
        "hook": "stop",
        "session_id": session_id,
        "sentinel_found": True,
        "last_heartbeat_advanced_to": now,
        "routine_version": ROUTINE_VERSION,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
