#!/usr/bin/env python3
"""SessionEnd hook for the VC-RoE plugin.

Releases the chat-claim file on clean session close. Companion to
session-start.py (acquire) and stop.py (refresh per turn). Part of the
multi-chat-access protection introduced at v1.3.0 per
OBS-vcroe-multi-chat-contamination-01.

Behaviour:

- Reads claim file at ~/.claude/projects/<cwd-dashed>/chat-claim.json.
- If the claim's session_id matches the calling SessionEnd's
  session_id, delete the file (clean release).
- If the session_id mismatches, leave the file in place. Another
  session owns it now (e.g. the operator killed this session, the
  TTL reaper expired the orphan, and a sibling session took over).
- On corrupt claim or read error, delete defensively (we own the
  filesystem state so cleanup beats stale state).
- On any other error, no-op silently.

Pure stdlib. Never throws. Logs to ~/.claude/methodology-hook.log.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# OBS-MET-AI: defensive utf-8 reconfigure for parity with sibling hooks.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

ROUTINE_VERSION = "1.10.4"
CLAIM_FILENAME = "chat-claim.json"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"

# v1.10.0 (F-63-01 Layer 2): SessionEnd semantics unchanged — release_claim
# already deletes the file regardless of mode, which correctly clears both
# reader and writer state in one operation. Implicit-readers (sessions that
# coexisted without writing the file per the reader-coexist branch) have no
# on-disk presence; their SessionEnd no-ops cleanly via the "not-owner"
# path. The writer-lease ledger (writer-lease.jsonl) is append-only and
# survives SessionEnd; T4-close summarises it into audit-trail markdown.


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


def release_claim(session_id: str, cwd_raw: Optional[str]) -> str:
    """Release own claim. Returns 'released' / 'released-corrupt' / 'not-owner' / 'no-claim' / 'no-cwd' / 'no-session-id' / 'error'."""
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
        try:
            path.unlink()
            return "released-corrupt"
        except Exception:
            return "error"
    if not isinstance(claim, dict) or claim.get("session_id") != session_id:
        return "not-owner"
    try:
        path.unlink()
        return "released"
    except Exception:
        return "error"


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({
            "ts": time.time(),
            "hook": "session_end",
            "phase": "stdin",
            "error": str(e),
            "routine_version": ROUTINE_VERSION,
        })
        return 0

    session_id = str(event.get("session_id") or "")
    cwd_raw = event.get("cwd")
    status = release_claim(session_id, cwd_raw)

    append_log({
        "ts": time.time(),
        "hook": "session_end",
        "session_id": session_id,
        "cwd": cwd_raw,
        "claim_release_status": status,
        "session_end_reason": event.get("reason"),
        "routine_version": ROUTINE_VERSION,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
