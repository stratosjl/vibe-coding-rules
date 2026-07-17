#!/usr/bin/env python3
"""Kimi Code SessionEnd adapter for vc-roe (translate-and-reuse).

Releases the chat-claim via the Claude session-end.py main(). No output
contract needed (SessionEnd is observation-only); side effect is the point.
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
        A.append_log({"ts": started, "hook": "kimi-session-end", "phase": "stdin", "error": str(e)})
        return 0
    try:
        mod = A.load_hook_module("session-end")
        A.run_module_main(mod, event)
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-session-end", "phase": "run", "error": str(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
