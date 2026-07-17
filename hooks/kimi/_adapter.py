#!/usr/bin/env python3
"""Shared helpers for the vc-roe Kimi Code hook adapters.

Translates the Kimi Code hook contract (snake_case stdin JSON; plain-text
stdout appended to context on exit 0; exit 2 + stderr to block) onto the
existing Claude-contract vc-roe hook modules, which are importlib-loaded by
path because their hyphenated filenames are not importable by name.

SESSION_START_DIRECT records the Task-1 probe decision: when Kimi does NOT
append SessionStart stdout to context, session_start writes a pending file
and user_prompt_submit prepends it to the first prompt's context instead.

Pure stdlib. Fail-soft: helpers degrade to no-ops so adapters still exit 0.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

KIMI_ADAPTER_VERSION = "1.19.0"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"
HOOKS_DIR = Path(__file__).resolve().parent.parent  # the Claude hook modules live here

# PROBE DECISIONS (the Task-1 probe notes, archived outside this repo).
# Defaults assume the conservative
# path: SessionStart stdout does NOT land -> defer injection to first prompt.
SESSION_START_DIRECT = False  # set True iff probe shows SessionStart stdout in context
ENVELOPE_HONORED = False      # set True iff probe shows hookSpecificOutput honored

_PENDING_DIR = Path(tempfile.gettempdir())


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry.setdefault("harness", "kimi")
        entry.setdefault("routine_version", KIMI_ADAPTER_VERSION)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_hook_module(module_key: str):
    """Importlib-load a hyphenated Claude hook module, e.g. 'session-start'."""
    path = HOOKS_DIR / f"{module_key}.py"
    spec = importlib.util.spec_from_file_location(
        f"vc_roe_{module_key.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_event() -> dict[str, Any]:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def run_module_main(mod: Any, event: dict[str, Any]) -> str:
    """Run a Claude hook module's main() in-process with mocked stdio.

    Returns the module's stdout text (usually one hookSpecificOutput JSON
    line). The Kimi event already uses the field names the Claude modules
    read (cwd, session_id), so it is passed through verbatim.
    """
    stdin_bak, stdout_bak = sys.stdin, sys.stdout
    buf = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        sys.stdout = buf
        mod.main()
    finally:
        sys.stdin, sys.stdout = stdin_bak, stdout_bak
    return buf.getvalue()


def extract_additional_context(stdout_text: str) -> str:
    """Pull additionalContext out of a Claude hookSpecificOutput line."""
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        hso = obj.get("hookSpecificOutput")
        if isinstance(hso, dict) and isinstance(hso.get("additionalContext"), str):
            return hso["additionalContext"]
    return ""


def emit_context(text: str) -> int:
    """Append text to the Kimi context (exit 0; plain stdout per Kimi docs)."""
    if text:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0


def block(reason: str) -> int:
    """Block the current Kimi operation (exit 2; reason on stderr)."""
    try:
        sys.stderr.write(reason + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    return 2


def _pending_path(session_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in session_id)
    return _PENDING_DIR / f"vc-roe-kimi-pending-{safe}"


def write_pending(session_id: str, text: str) -> None:
    try:
        _pending_path(session_id).write_text(text, encoding="utf-8")
    except Exception:
        pass


def pop_pending(session_id: str) -> str:
    try:
        p = _pending_path(session_id)
        if not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8")
        p.unlink()
        return text
    except Exception:
        return ""
