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

v1.1.3 silent-stop blocker (closes OBS-50-01, [INT-A] M0 build
2026-05-09): when the most-recent agent-loop's assistant content
contains tool_use blocks but zero non-empty text blocks, Stop emits
{"decision":"block", ...} so Claude Code re-prompts rather than ending
the chat silently. Adds last_assistant_blocks() as the underlying
walker; last_assistant_text() now derives its result from it for code
reuse. Walk-rule and skip-rules are unchanged from v1.1.2.
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

ROUTINE_VERSION = "1.7.0"
ANCHOR_DIR = Path("/tmp")
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"
UPS_MARKER_PREFIX = "claude-methodology-current-session-"

# v1.3.0: chat-claim refresh (multi-chat-access protection per
# OBS-vcroe-multi-chat-contamination-01). Stop fires per turn and
# refreshes our own claim's ts; this keeps the claim "alive" across long
# user-think-time gaps so a sibling chat opening during that gap is
# correctly refused. SessionEnd deletes the claim cleanly; TTL reaps
# orphans. Refresh fires unconditionally so claim hygiene is independent
# of the heartbeat / silent-stop logic that may early-return.
CLAIM_FILENAME = "chat-claim.json"

SENTINEL_RE = re.compile(r"\[heartbeat-fired:T\+\d+m\]")


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def claim_path_for(cwd_raw: Optional[str]) -> Optional[Path]:
    if not cwd_raw:
        return None
    try:
        cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd_raw).resolve()))  # OBS-MET-AJ
        return Path.home() / ".claude" / "projects" / cwd_dashed / CLAIM_FILENAME
    except Exception:
        return None


def refresh_claim(session_id: str, cwd_raw: Optional[str], now: int) -> str:
    """Refresh own claim's ts. Returns 'refreshed' / 'not-owner' / 'no-claim' / 'error' / 'no-cwd' / 'no-session-id'."""
    if not session_id:
        return "no-session-id"
    path = claim_path_for(cwd_raw)
    if not path:
        return "no-cwd"
    if not path.is_file():
        return "no-claim"
    try:
        with open(path, "r", encoding="utf-8") as f:
            claim = json.load(f)
    except Exception:
        return "error"
    if not isinstance(claim, dict) or claim.get("session_id") != session_id:
        return "not-owner"
    claim["ts"] = now
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(claim, f)
            f.write("\n")
        return "refreshed"
    except Exception:
        return "error"


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


def extract_assistant_blocks(turn: Any) -> list[dict[str, Any]]:
    """Best-effort extract of assistant content-block list across transcript shapes.

    Returns the raw list of content blocks (each a dict with at least a
    "type" key) for an assistant-role turn. Returns [] for non-assistant
    turns or unrecognised shapes. A bare-string content is normalised to
    a single text block so downstream callers can treat all return
    values uniformly.
    """
    if not isinstance(turn, dict):
        return []
    role = turn.get("role") or turn.get("type")
    if role != "assistant":
        msg = turn.get("message")
        if isinstance(msg, dict):
            return extract_assistant_blocks(msg)
        return []
    content = turn.get("content")
    if content is None:
        msg = turn.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    return []


def last_assistant_blocks(transcript_path: str) -> Optional[list[dict[str, Any]]]:
    """Collect ALL content blocks from the most-recent agent-loop assistant range.

    The transcript is JSONL with one event per line. An assistant turn
    may span multiple lines (one per text block, one per tool_use, etc.)
    and an agent loop may chain multiple assistant turns through one or
    more tool_result lines. Walk backwards from the end of the
    transcript, accumulating assistant content blocks. Skip role="user"
    lines that carry only tool_result blocks (internal to the agent
    loop), and skip synthetic Claude Code metadata line types
    (attachment, last-prompt, ai-title, permission-mode,
    file-history-snapshot, plus any future unknown synthetic type).
    Halt only on a true user-prompt boundary. Return the flat block list
    in original (forward) order, or None.

    v1.1.3 introduction: this is the new underlying walker. It mirrors
    the v1.1.2 last_assistant_text() walk-rule and skip-rules but
    returns the raw block list rather than concatenated text, so that
    main() can ask "are there tool_use blocks but no text blocks" for
    the silent-stop blocker (OBS-50-01).
    """
    p = Path(transcript_path)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    collected: list[list[dict[str, Any]]] = []
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
            blocks = extract_assistant_blocks(turn)
            if blocks:
                collected.append(blocks)
            continue

        if role == "user":
            # Distinguish tool_result-bearing user lines (internal to the
            # agent loop, skip) from real user-prompt lines (boundary,
            # halt). A user line whose content list contains any
            # tool_result block is treated as internal per OBS-MET-AA.
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
            break

        # v1.1.2 fix for OBS-46-02: any other role/type (synthetic Claude
        # Code metadata like attachment / last-prompt / ai-title /
        # permission-mode / file-history-snapshot, or any future unknown
        # synthetic line type) is transparent and skipped past.
        continue

    if not collected:
        return None

    flat: list[dict[str, Any]] = []
    for blocks in reversed(collected):
        flat.extend(blocks)
    return flat


def last_assistant_text(transcript_path: str) -> Optional[str]:
    """Concatenated text of all text-type blocks from the most-recent agent loop.

    Walk-rule and skip-rules are identical to v1.1.2: HALT only on a
    true user-prompt boundary (role="user" whose content is NOT a
    tool_result-bearing block list); any other role/type, including
    synthetic Claude Code metadata, is transparent and skipped past.

    v1.1.3 refactor: this function now derives its result from
    last_assistant_blocks() rather than walking the transcript itself.
    The behaviour is unchanged for all v1.1.2 inputs (text-block joins
    with "\n" produce the same string under either gathering order).
    """
    blocks = last_assistant_blocks(transcript_path)
    if not blocks:
        return None
    parts = [
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    parts = [p for p in parts if p]
    if not parts:
        return None
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

    # v1.3.0: refresh chat-claim ts before any early-return paths so claim
    # hygiene is independent of transcript / anchor availability.
    claim_status = refresh_claim(session_id, event.get("cwd"), int(started))

    if not session_id or not transcript_path:
        append_log({
            "ts": time.time(),
            "hook": "stop",
            "session_id": session_id,
            "phase": "early-return-no-session-or-transcript",
            "claim_status": claim_status,
            "routine_version": ROUTINE_VERSION,
        })
        return 0

    check_marker_mismatch(session_id, event.get("cwd"))

    anchor = read_anchor(session_id)
    if not anchor:
        return 0

    # v1.1.3 silent-stop blocker (OBS-50-01). When the most-recent agent
    # loop's assistant content is one-or-more tool_use blocks with zero
    # non-empty text blocks, the assistant ended its turn after the
    # tool_result without ever explaining its next step. Claude Code
    # ends the chat silently in that state and no subsequent hook fires
    # to bridge the gap ([INT-A] M0 build 2026-05-09 stalled 50 min
    # before operator typed "where are we"). Emit decision="block" so
    # Claude Code re-prompts the assistant for follow-up text.
    blocks = last_assistant_blocks(transcript_path)
    has_text = any(
        isinstance(b, dict)
        and b.get("type") == "text"
        and (b.get("text") or "").strip()
        for b in (blocks or [])
    )
    has_tool_use = any(
        isinstance(b, dict) and b.get("type") == "tool_use"
        for b in (blocks or [])
    )
    if has_tool_use and not has_text:
        output = {
            "decision": "block",
            "reason": (
                "STOP BLOCKED by vc-roe silent-stop blocker (v1.1.3, "
                "OBS-50-01). Your previous turn ended after tool calls "
                "with zero text blocks. Either continue execution toward "
                "the active milestone, OR emit an explicit "
                "'[awaiting-user]' or '[turn-complete]' sentinel as your "
                "text response if you intend to stop. Silent end after a "
                "tool_result is the documented failure mode from the "
                "[INT-A] M0 build (2026-05-09)."
            ),
        }
        print(json.dumps(output))
        append_log({
            "ts": time.time(),
            "hook": "stop",
            "session_id": session_id,
            "blocked": True,
            "reason": "silent-stop",
            "claim_status": claim_status,
            "routine_version": ROUTINE_VERSION,
        })
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
    # v1.1.1: preserve LAST_PTU_TAG_SEC across the advance so the PostToolUse
    # rate-limit window stays in force. Prior versions silently dropped it,
    # which let an immediately-following PostToolUse re-emit before the
    # 60-second rate-limit was due.
    write_anchor(session_id, {
        "T0": anchor.get("T0", "0"),
        "LAST_HEARTBEAT": str(now),
        "TIER": anchor.get("TIER", "T0"),
        "LAST_PTU_TAG_SEC": anchor.get("LAST_PTU_TAG_SEC", "0"),
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
