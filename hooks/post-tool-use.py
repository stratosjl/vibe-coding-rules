#!/usr/bin/env python3
"""PostToolUse hook for the VC-RoE (Vibe Coding · Rules of Engagement) Claude Code plugin.

Added at v1.1.0 (2026-05-08) to close the autonomous-work heartbeat-silence
gap surfaced at session 33 of the [EXAMPLE-PROJ] Website project (96-minute silent
window between UserPromptSubmit fires; sid f31d8778 in the hook log shows
status=OVERDUE-2X t0=96 hb=96 at the next user prompt). Root cause:
UserPromptSubmit fires only on user prompts; Stop hook fires every turn
but only greps for the sentinel and never injects context. During long
autonomous work the assistant has no in-band signal that the cadence
elapsed.

Fix: PostToolUse fires after every tool call. When TIER is T2+ and the
heartbeat cadence is OVERDUE or OVERDUE-2X, emit an additionalContext
clock-tag asking the assistant to surface the heartbeat block. Rate-
limited to once per 60 seconds via LAST_PTU_TAG_SEC field in the anchor
to avoid spamming when the assistant runs many tool calls back-to-back.
At OVERDUE-2X, auto-advance LAST_HEARTBEAT to mirror the
user-prompt-submit.py fail-safe semantics (D-MET-41).

v1.1.1 (2026-05-09) F-8 closure: PostToolUse now mirrors stop.py's
transcript-grep responsibility. Operational reality (confirmed via the
[EXAMPLE-PROJ] session 38 hook log forensics): Claude Code fires Stop only at the
end of an agent loop, not after every assistant turn. Heartbeat
sentinels emitted in intermediate turns within a single agent loop are
invisible to Stop until loop-end, leaving LAST_HEARTBEAT stale and
producing repeated false OVERDUE alarms via PostToolUse. The fix: when
PostToolUse runs, it reads the transcript and greps the agent-loop's
assistant text for sentinels; if a sentinel newer than current
LAST_HEARTBEAT is present, LAST_HEARTBEAT advances before the OVERDUE
check, suppressing false alarms while preserving the existing fail-safe
semantics. Sentinel freshness gates the advance (parsed minute must
exceed current LAST_HEARTBEAT minute) so stale loop-history sentinels
cannot mask a legitimate cadence miss.

Pure stdlib. Never throws. Logs to ~/.claude/methodology-hook.log.
Tier-aware: short-circuits to no-op at T0/T1.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# OBS-MET-AI: Windows stdout defaults to cp1252 which cannot encode non-ASCII.
# PTU output is ASCII but reconfigure defensively for parity with the other
# vc-roe hooks. No-op on streams that don't support reconfigure.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

ROUTINE_VERSION = "1.1.1"
ANCHOR_DIR = Path("/tmp")
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"

CADENCE_SEC = 15 * 60
OVERDUE_2X_SEC = 30 * 60
PTU_RATE_LIMIT_SEC = 60
TIER_ACTIVE = {"T2", "T3", "T4"}

SENTINEL_RE = re.compile(r"\[heartbeat-fired:T\+(\d+)m\]")


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


def emit(additional_context: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    }
    try:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def extract_assistant_text(turn: Any) -> str:
    """Best-effort extract of assistant-message text across transcript shapes.

    Mirrors stop.py at v1.1.1 so the two hooks share grep behaviour."""
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


def agent_loop_assistant_text(transcript_path: str) -> Optional[str]:
    """Collect assistant text from the current agent loop.

    Walks the transcript backwards from the end, gathering all assistant-
    role lines and skipping role="user" lines that carry only tool_result
    blocks (those are tool returns within the same agent loop, not turn
    boundaries). Stops at the first user prompt boundary.

    The walk shape matches stop.py's last_assistant_text. The naming
    "agent_loop_assistant_text" emphasises the v1.1.1 insight that this
    text spans the entire current agent loop (multi-turn / multi-tool),
    not just the most recent assistant turn — Stop only sees this scope
    once per loop, but PostToolUse can sample it after every tool call.
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

        # tool_result lines have role="user" but carry no prompt-boundary
        # semantics; skip them so the backwards walk reaches pre-tool
        # sentinel emissions in the same agent loop.
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


def max_sentinel_minute(text: str) -> int:
    """Return the highest T+N minute from any heartbeat sentinel in text.

    Returns -1 if no sentinel is present. Used to gate LAST_HEARTBEAT
    advance on freshness: a sentinel whose declared minute is no greater
    than current LAST_HEARTBEAT_MIN is stale loop-history and must not
    suppress a legitimate OVERDUE alarm.
    """
    best = -1
    for m in SENTINEL_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (ValueError, IndexError):
            continue
        if n > best:
            best = n
    return best


def main() -> int:
    started = time.time()
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({"ts": time.time(), "hook": "post-tool-use", "phase": "stdin", "error": str(e)})
        return 0

    session_id = event.get("session_id") or ""
    if not session_id:
        return 0

    anchor = read_anchor(session_id)
    if not anchor:
        return 0

    tier = anchor.get("TIER", "T0")
    if tier not in TIER_ACTIVE:
        return 0

    try:
        t0 = int(anchor.get("T0", "0"))
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        last_ptu_tag = int(anchor.get("LAST_PTU_TAG_SEC", "0"))
    except ValueError:
        return 0
    if t0 <= 0:
        return 0

    now = int(started)

    # v1.1.1 F-8 closure: grep the agent-loop's assistant text for any
    # heartbeat sentinel emitted in an intermediate turn and advance
    # LAST_HEARTBEAT before the cadence check. Stop fires only at loop-end,
    # so intermediate-turn sentinels otherwise stay invisible until then,
    # producing repeated false OVERDUE alarms.
    transcript_path = event.get("transcript_path") or ""
    if transcript_path:
        text = agent_loop_assistant_text(transcript_path)
        if text:
            max_min = max_sentinel_minute(text)
            last_hb_min = (last_hb - t0) // 60 if last_hb > 0 else 0
            if max_min > last_hb_min:
                # Fresh sentinel — advance LAST_HEARTBEAT to NOW for
                # consistency with stop.py advance semantics. Preserve
                # LAST_PTU_TAG_SEC; the rate-limit window stays in force.
                last_hb = now
                write_anchor(session_id, {
                    "T0": str(t0),
                    "LAST_HEARTBEAT": str(now),
                    "TIER": tier,
                    "LAST_PTU_TAG_SEC": str(last_ptu_tag),
                })
                append_log({
                    "ts": time.time(),
                    "hook": "post-tool-use",
                    "session_id": session_id,
                    "tier": tier,
                    "tool_name": event.get("tool_name", ""),
                    "diagnostic": "sentinel-grep-advanced",
                    "max_sentinel_min": max_min,
                    "advanced_to_min": (now - t0) // 60,
                    "routine_version": ROUTINE_VERSION,
                })

    elapsed_t0 = now - t0
    last_hb_effective = last_hb if last_hb > 0 else t0
    elapsed_since_hb = now - last_hb_effective

    if elapsed_since_hb < CADENCE_SEC:
        return 0

    # Rate-limit: emit at most once per PTU_RATE_LIMIT_SEC. The Stop hook
    # advances LAST_HEARTBEAT when the assistant fires the sentinel; in the
    # healthy path, elapsed_since_hb resets and we no-op naturally. Rate
    # limit is the safety net for the bug case (assistant ignores prompt).
    if last_ptu_tag > 0 and now - last_ptu_tag < PTU_RATE_LIMIT_SEC:
        return 0

    elapsed_t0_min = elapsed_t0 // 60
    elapsed_hb_min = elapsed_since_hb // 60
    next_due_min = elapsed_t0_min + max(0, (CADENCE_SEC - elapsed_since_hb) // 60)

    if elapsed_since_hb >= OVERDUE_2X_SEC:
        status = "OVERDUE-2X"
    else:
        status = "OVERDUE"

    last_hb_display = (last_hb - t0) // 60 if last_hb > 0 else 0
    clock_tag = (
        f"[session-clock: T+{elapsed_t0_min}m | "
        f"last-heartbeat: T+{last_hb_display}m | "
        f"next-due: T+{next_due_min}m, {status}] (PostToolUse)"
    )

    if status == "OVERDUE":
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE per D-MET-33 surfaced via PostToolUse "
            "(autonomous-work coverage; no user prompt has fired the cadence "
            "during the in-flight tool sequence). Emit the session-health "
            "heartbeat block at the start of your next reply: 5 substantive "
            "content lines (1: session goal restated; 2: scope status; 3: "
            "anomaly status; 4: side-questions status; 5: background tasks) "
            f"followed by the literal sentinel [heartbeat-fired:T+{elapsed_t0_min}m] "
            "on its own line. Six lines total. Layered fail-safe per "
            "D-MET-41.\n</system-reminder>"
        )
    else:
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE-2X per D-MET-33 surfaced via PostToolUse: two "
            "consecutive cadences elapsed without sentinel detection during "
            "an autonomous-work window. Auto-advancing LAST_HEARTBEAT as the "
            "layered fail-safe per D-MET-41. Emit a recovery session-health "
            "heartbeat block at the start of your next reply: 5 substantive "
            "content lines (1: session goal restated; 2: scope status; 3: "
            "anomaly status with explicit acknowledgement of the missed "
            "cadences; 4: side-questions status; 5: background tasks) "
            f"followed by the literal sentinel [heartbeat-fired:T+{elapsed_t0_min}m] "
            "on its own line. Six lines total.\n</system-reminder>"
        )

    if status == "OVERDUE-2X":
        write_anchor(session_id, {
            "T0": str(t0),
            "LAST_HEARTBEAT": str(now),
            "TIER": tier,
            "LAST_PTU_TAG_SEC": str(now),
        })
    else:
        write_anchor(session_id, {
            "T0": str(t0),
            "LAST_HEARTBEAT": str(last_hb),
            "TIER": tier,
            "LAST_PTU_TAG_SEC": str(now),
        })

    emit(ctx)

    append_log({
        "ts": time.time(),
        "hook": "post-tool-use",
        "session_id": session_id,
        "tier": tier,
        "tool_name": event.get("tool_name", ""),
        "elapsed_t0_min": elapsed_t0_min,
        "elapsed_since_hb_min": elapsed_hb_min,
        "status": status,
        "routine_version": ROUTINE_VERSION,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
