#!/usr/bin/env python3
"""Validation runner for the vc-roe Kimi Code hook adapters.

Script-style harness (repo convention): exercises each adapter with
synthetic Kimi payloads, prints PASS/FAIL per check, exits 1 on any failure.
Fixtures mirror the captures in docs/superpowers/probe/ (vc-roe-private).

Usage:
    python3 test-kimi-adapter.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
KIMI_HOOKS = PLUGIN_ROOT / "hooks" / "kimi"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def load(module_file: str, key: str):
    spec = importlib.util.spec_from_file_location(key, KIMI_HOOKS / module_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_adapter(mod, event: dict) -> tuple[int, str, str]:
    """Run an adapter module's main() with mocked stdio; return (rc, out, err)."""
    stdin_bak, stdout_bak, stderr_bak = sys.stdin, sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        sys.stdout, sys.stderr = out, err
        rc = mod.main()
    finally:
        sys.stdin, sys.stdout, sys.stderr = stdin_bak, stdout_bak, stderr_bak
    return rc, out.getvalue(), err.getvalue()


# --- Task 2: adapter core ---

def t_core_load_and_run() -> None:
    A = load("_adapter.py", "kimi_adapter")
    ss = A.load_hook_module("session-start")
    check("core: session-start module loads", hasattr(ss, "main"))
    with tempfile.TemporaryDirectory() as td:
        event = {"hook_event_name": "SessionStart", "session_id": "ktest-core",
                 "cwd": td, "source": "startup"}
        out = A.run_module_main(ss, event)
    ctx = A.extract_additional_context(out)
    check("core: additionalContext extracted", "Methodology in force" in ctx, ctx[:80])


def t_core_emit_and_block() -> None:
    A = load("_adapter.py", "kimi_adapter")
    # emit_context prints text, returns 0
    out_bak = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rc = A.emit_context("hello")
    finally:
        sys.stdout = out_bak
    check("core: emit_context rc/text", rc == 0 and buf.getvalue().strip() == "hello")
    err_bak = sys.stderr
    ebuf = io.StringIO()
    sys.stderr = ebuf
    try:
        rc = A.block("nope")
    finally:
        sys.stderr = err_bak
    check("core: block rc/stderr", rc == 2 and "nope" in ebuf.getvalue())


def t_core_pending_roundtrip() -> None:
    A = load("_adapter.py", "kimi_adapter")
    sid = f"ktest-pending-{os.getpid()}"
    check("core: pop on empty -> ''", A.pop_pending(sid) == "")
    A.write_pending(sid, "tier-block")
    check("core: pending roundtrip", A.pop_pending(sid) == "tier-block")
    check("core: pending consumed", A.pop_pending(sid) == "")


def t_core_malformed_extract() -> None:
    A = load("_adapter.py", "kimi_adapter")
    check("core: extract on garbage -> ''",
          A.extract_additional_context("not json\n{\"x\":1}\n") == "")


ALL_TESTS = [
    ("core-load-run", t_core_load_and_run),
    ("core-emit-block", t_core_emit_and_block),
    ("core-pending", t_core_pending_roundtrip),
    ("core-malformed-extract", t_core_malformed_extract),
]


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for name, fn in ALL_TESTS:
        if only and name != only:
            continue
        try:
            fn()
        except Exception as e:  # a crashing test is a failing test
            check(name, False, f"raised {e!r}")
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
