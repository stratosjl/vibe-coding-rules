#!/usr/bin/env python3
"""Kimi Code UserPromptSubmit adapter for vc-roe (translate-and-reuse).

Emits the session-clock tag / OVERDUE flag via the Claude
user-prompt-submit.py main(), and prepends any parked SessionStart block
(deferred injection when _adapter.SESSION_START_DIRECT is False).

Pure stdlib. Never throws; exit 0 on any error.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _adapter as A  # noqa: E402


def main() -> int:
    started = time.time()
    try:
        event = A.read_event()
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-ups", "phase": "stdin", "error": str(e)})
        return 0
    session_id = str(event.get("session_id") or "")
    parts: list[str] = []
    if session_id:
        pending = A.pop_pending(session_id)
        if pending:
            parts.append(pending)
    try:
        mod = A.load_hook_module("user-prompt-submit")
        out = A.run_module_main(mod, event)
        ctx = A.extract_additional_context(out)
        if ctx:
            parts.append(ctx)
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-ups", "phase": "run", "error": str(e)})
    return A.emit_context("\n".join(parts))


if __name__ == "__main__":
    sys.exit(main())
