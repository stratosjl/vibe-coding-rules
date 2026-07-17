#!/usr/bin/env python3
"""Kimi Code SessionStart adapter for vc-roe (translate-and-reuse).

Feeds the Kimi event to the Claude session-start.py main() in-process
(claim acquire, floor/HWM, gate re-arm, anchor write, logging all reused),
then converts the resulting additionalContext to the Kimi contract.

When _adapter.SESSION_START_DIRECT is False (Kimi does not append
SessionStart stdout to context), the block is parked via write_pending()
and user_prompt_submit prepends it to the first prompt instead.

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
        A.append_log({"ts": started, "hook": "kimi-session-start", "phase": "stdin", "error": str(e)})
        return 0
    try:
        mod = A.load_hook_module("session-start")
        out = A.run_module_main(mod, event)
        ctx = A.extract_additional_context(out)
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-session-start", "phase": "run", "error": str(e)})
        return 0
    if not ctx:
        return 0
    if A.SESSION_START_DIRECT:
        return A.emit_context(ctx)
    session_id = str(event.get("session_id") or "")
    if session_id:
        A.write_pending(session_id, ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
