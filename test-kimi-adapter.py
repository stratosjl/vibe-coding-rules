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


# --- Task 3: session_start / session_end ---

def t_session_start_injects_slice() -> None:
    A = load("_adapter.py", "kimi_adapter")
    ss = load("session_start.py", "kimi_session_start")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".git").mkdir()
        (root / "decisions.md").write_text("# d\n")
        (root / "handovers").mkdir()
        event = {"hook_event_name": "SessionStart", "session_id": "ktest-ss",
                 "cwd": td, "source": "startup"}
        rc, out, err = run_adapter(ss, event)
    if A.SESSION_START_DIRECT:
        check("ss: slice emitted directly", rc == 0 and "Methodology in force" in out, out[:80])
    else:
        check("ss: deferred -> no direct emit", rc == 0 and "Methodology in force" not in out)
        check("ss: deferred -> pending written", A.pop_pending("ktest-ss").find("Methodology in force") >= 0)


def t_session_start_t0_noop_and_failsafe() -> None:
    ss = load("session_start.py", "kimi_session_start")
    rc, out, err = run_adapter(ss, {"hook_event_name": "SessionStart",
                                    "session_id": "", "cwd": "/nonexistent-dir-xyz"})
    check("ss: degenerate event exits 0", rc == 0)


def t_session_end_releases_claim() -> None:
    se = load("session_end.py", "kimi_session_end")
    # Seed a claim the way session-start.py does, in a temp project.
    with tempfile.TemporaryDirectory() as td:
        A = load("_adapter.py", "kimi_adapter")
        ssmod = A.load_hook_module("session-start")
        ssmod.write_claim(Path(td), "ktest-se", int(time.time()),
                          "testhost", os.getpid(), None, "bootx", mode="reader")
        claim = ssmod.claim_path(Path(td))
        check("se: claim seeded", claim.is_file())
        rc, out, err = run_adapter(se, {"hook_event_name": "SessionEnd",
                                        "session_id": "ktest-se", "cwd": td})
        check("se: claim released", rc == 0 and not claim.exists())


ALL_TESTS = [
    ("core-load-run", t_core_load_and_run),
    ("core-emit-block", t_core_emit_and_block),
    ("core-pending", t_core_pending_roundtrip),
    ("core-malformed-extract", t_core_malformed_extract),
]
ALL_TESTS += [("session-start-injects", t_session_start_injects_slice), ("session-start-failsafe", t_session_start_t0_noop_and_failsafe), ("session-end-claim", t_session_end_releases_claim)]


# --- Task 4: user_prompt_submit ---

def t_ups_clock_tag_and_pending() -> None:
    A = load("_adapter.py", "kimi_adapter")
    ups_hook = load("user_prompt_submit.py", "kimi_ups")
    upsmod = A.load_hook_module("user-prompt-submit")
    sid = f"ktest-ups-{os.getpid()}"
    now = int(time.time())
    upsmod.write_anchor(sid, {"T0": str(now - 60), "LAST_HEARTBEAT": "0", "TIER": "T2"})
    rc, out, err = run_adapter(ups_hook, {"hook_event_name": "UserPromptSubmit",
                                          "session_id": sid, "cwd": os.getcwd(),
                                          "user_prompt": "hi"})
    check("ups: clock tag emitted", rc == 0 and "[session-clock:" in out, out[:80])
    # Pending block prepended when present (deferred-injection path).
    A.write_pending(sid, "PENDING-TIER-BLOCK")
    rc, out, err = run_adapter(ups_hook, {"hook_event_name": "UserPromptSubmit",
                                          "session_id": sid, "cwd": os.getcwd(),
                                          "user_prompt": "hi again"})
    check("ups: pending prepended once", "PENDING-TIER-BLOCK" in out)
    rc, out, err = run_adapter(ups_hook, {"hook_event_name": "UserPromptSubmit",
                                          "session_id": sid, "cwd": os.getcwd(),
                                          "user_prompt": "and again"})
    check("ups: pending consumed", "PENDING-TIER-BLOCK" not in out)
    upsmod.write_anchor(sid, {"T0": "0", "LAST_HEARTBEAT": "0", "TIER": "T0"})  # cleanup-ish


ALL_TESTS += [("ups-clock-pending", t_ups_clock_tag_and_pending)]


# --- Task 5: stop (block-based heartbeat) ---

def _seed_anchor(A, sid, **over):
    upsmod = A.load_hook_module("user-prompt-submit")
    fields = {"T0": str(int(time.time()) - 3600), "LAST_HEARTBEAT": str(int(time.time()) - 3600),
              "TIER": "T2"}
    fields.update(over)
    upsmod.write_anchor(sid, fields)
    return upsmod


def t_stop_under_cadence_allows() -> None:
    A = load("_adapter.py", "kimi_adapter")
    st = load("stop.py", "kimi_stop")
    sid = f"ktest-stop1-{os.getpid()}"
    _seed_anchor(A, sid, LAST_HEARTBEAT=str(int(time.time()) - 60))
    rc, out, err = run_adapter(st, {"hook_event_name": "Stop", "session_id": sid, "cwd": os.getcwd()})
    check("stop: under cadence allows", rc == 0 and not err)


def t_stop_overdue_blocks_then_trust_advances() -> None:
    A = load("_adapter.py", "kimi_adapter")
    st = load("stop.py", "kimi_stop")
    upsmod = A.load_hook_module("user-prompt-submit")
    sid = f"ktest-stop2-{os.getpid()}"
    _seed_anchor(A, sid)  # 60 min since heartbeat, cadence 15m
    ev = {"hook_event_name": "Stop", "session_id": sid, "cwd": os.getcwd()}
    rc1, _, err1 = run_adapter(st, ev)
    check("stop: block 1 (rc2+instruction)", rc1 == 2 and "heartbeat-fired" in err1)
    rc2, _, err2 = run_adapter(st, ev)
    check("stop: block 2 (cap boundary)", rc2 == 2)
    rc3, _, err3 = run_adapter(st, ev)
    check("stop: cap reached -> allow", rc3 == 0)
    anchor = upsmod.read_anchor(sid)
    check("stop: trust-advanced LAST_HEARTBEAT",
          abs(int(anchor["LAST_HEARTBEAT"]) - int(time.time())) < 30)
    check("stop: STOP_BLOCKS reset", anchor.get("STOP_BLOCKS") == "0")


def t_stop_tier_and_anchor_guards() -> None:
    A = load("_adapter.py", "kimi_adapter")
    st = load("stop.py", "kimi_stop")
    ev = {"hook_event_name": "Stop", "session_id": "ktest-stop3", "cwd": os.getcwd()}
    rc, _, _ = run_adapter(st, ev)  # no anchor at all
    check("stop: no anchor allows", rc == 0)
    sid = f"ktest-stop4-{os.getpid()}"
    _seed_anchor(A, sid, TIER="T1")
    rc, _, _ = run_adapter(st, {"hook_event_name": "Stop", "session_id": sid, "cwd": os.getcwd()})
    check("stop: T1 allows (heartbeat is T2+)", rc == 0)


ALL_TESTS += [("stop-under-cadence", t_stop_under_cadence_allows), ("stop-block-trust", t_stop_overdue_blocks_then_trust_advances), ("stop-guards", t_stop_tier_and_anchor_guards)]


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
