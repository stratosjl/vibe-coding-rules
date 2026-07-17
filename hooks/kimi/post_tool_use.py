#!/usr/bin/env python3
"""Kimi Code PostToolUse adapter for vc-roe (translate-and-reuse).

Kimi's PostToolUse is observation-only, so the clock-tag context the Claude
module emits is inert; the point of this adapter is the claim
writer-promotion side effect (reader -> writer on first write), which keeps
cross-harness multi-chat protection intact. Wired in kimi.plugin.json with
matcher 'Edit|Write|Bash' so it only fires on write-capable tools.

Pure stdlib. Never throws; exit 0 on any error.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _adapter as A  # noqa: E402


def _translate_tool_input(event: dict) -> dict:
    """Map Kimi's file-tool `path` key onto Claude's `file_path`.

    Kimi's built-in Edit/Write tools pass `tool_input.path` (per Kimi docs);
    the Claude post-tool-use module's detect_write() reads
    `file_path`/`notebook_path`. Without this translation Kimi Edit/Write
    events never trigger claim writer-promotion, breaking cross-harness
    parity. Non-destructive: the original `path` key is kept.
    """
    ti = event.get("tool_input")
    if isinstance(ti, dict) and "file_path" not in ti and isinstance(ti.get("path"), str):
        event = dict(event)
        event["tool_input"] = {**ti, "file_path": ti["path"]}
    return event


def main() -> int:
    started = time.time()
    try:
        event = A.read_event()
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-ptu", "phase": "stdin", "error": str(e)})
        return 0
    try:
        event = _translate_tool_input(event)
        mod = A.load_hook_module("post-tool-use")
        A.run_module_main(mod, event)  # output intentionally discarded (observation-only)
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-ptu", "phase": "run", "error": str(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
