#!/usr/bin/env python3
"""Validation runner for the SessionStart machine-local addon extension point
(v1.15.0).

The SessionStart hook offers an optional, fail-soft extension point: if a
machine-local module directory exists at ~/.claude/vc-roe-addons, the hook puts
it on sys.path and calls

    vc_roe_local_addons.session_start_block(detection, tier) -> (block, state)

contributing `block` (an extra additionalContext section) and `state` (a
one-word trace rendered on the `session_start_addon:` line). Plain public installs
have no such directory and skip it; any import/runtime error degrades to an
empty block and the literal state `error`, and never affects session start.

This runner drives hooks/session-start.py as a subprocess against a fake HOME
(HOME *and* USERPROFILE, per the v1.14.1 Windows divergence: Path.home()
consults USERPROFILE on Windows) and asserts each branch:

    Case 1  no addon dir          -> state 'none', no addon block
    Case 2  working addon         -> block present, ordered before the trace,
                                     state echoed verbatim
    Case 3  addon raises          -> state 'error', no block, hook still 0/JSON
    Case 4  module missing        -> state 'error' (ImportError), session intact

Cross-platform: pure stdlib, sys.executable (never the broken `python3` alias
on Windows per the user-global CLAUDE.md rule).

Usage:
    python test-session-start-addon.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent
SESSION_START = PLUGIN_ROOT / "hooks" / "session-start.py"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def _force_utf8_streams() -> None:
    """Windows cp1252 guard: this runner prints addon block text that may carry
    non-ASCII. No-op on Linux/macOS where stdout is already UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL: {label}")


def make_home(tmp: Path, name: str, addon_module: Optional[str]) -> Path:
    """Create a fake home; optionally seed ~/.claude/vc-roe-addons with a
    vc_roe_local_addons.py whose body is `addon_module`. If `addon_module` is
    None, no addon directory is created (Case 1). If it is the sentinel
    "__DIR_ONLY__", the directory is created but left empty (Case 4)."""
    home = tmp / name
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    if addon_module is None:
        return home
    addon_dir = home / ".claude" / "vc-roe-addons"
    addon_dir.mkdir(parents=True, exist_ok=True)
    if addon_module != "__DIR_ONLY__":
        (addon_dir / "vc_roe_local_addons.py").write_text(
            addon_module, encoding="utf-8"
        )
    return home


def make_cwd(tmp: Path, name: str) -> Path:
    cwd = tmp / name
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "CLAUDE.md").write_text("# test project\ntier: T0\n", encoding="utf-8")
    return cwd


def run_session_start(home: Path, cwd: Path) -> tuple[int, dict[str, Any], str]:
    """Drive the hook; return (returncode, parsed_output, additionalContext)."""
    import os
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # v1.14.1: Path.home() uses USERPROFILE on Windows
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    event = {"session_id": "test-addon-session", "cwd": str(cwd)}
    proc = subprocess.run(
        [sys.executable, str(SESSION_START)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
    )
    try:
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {}
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    return proc.returncode, out, ctx


WORKING_ADDON = '''\
def session_start_block(detection, tier):
    return ("## Addon block\\n\\nADDON-MARKER-XYZ for tier %s.\\n\\n" % tier, "clean")
'''

RAISING_ADDON = '''\
def session_start_block(detection, tier):
    raise RuntimeError("intentional addon failure")
'''


def main() -> int:
    _force_utf8_streams()
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-addon-test-"))
    try:
        # Case 1: no addon directory -> state 'none', no block.
        print("Case 1: no addon directory")
        home = make_home(tmp, "h1", None)
        cwd = make_cwd(tmp, "c1")
        rc, out, ctx = run_session_start(home, cwd)
        check(rc == 0, "C1 hook exits 0")
        check(bool(ctx), "C1 emits additionalContext")
        check("- session_start_addon: none" in ctx, "C1 session_start_addon: none")
        check("ADDON-MARKER" not in ctx, "C1 no addon block leaked")

        # Case 2: working addon -> block present, before trace, state echoed.
        print("Case 2: working addon")
        home = make_home(tmp, "h2", WORKING_ADDON)
        cwd = make_cwd(tmp, "c2")
        rc, out, ctx = run_session_start(home, cwd)
        check(rc == 0, "C2 hook exits 0")
        check("ADDON-MARKER-XYZ" in ctx, "C2 addon block present")
        check("- session_start_addon: clean" in ctx, "C2 state echoed (clean)")
        # Block must sit before the tier-detection trace section.
        i_block = ctx.find("ADDON-MARKER-XYZ")
        i_trace = ctx.find("## Tier detection trace")
        check(
            i_block != -1 and i_trace != -1 and i_block < i_trace,
            "C2 addon block ordered before tier-detection trace",
        )

        # Case 3: addon raises in session_start_block -> state 'error', no block.
        print("Case 3: addon raises")
        home = make_home(tmp, "h3", RAISING_ADDON)
        cwd = make_cwd(tmp, "c3")
        rc, out, ctx = run_session_start(home, cwd)
        check(rc == 0, "C3 hook exits 0 (fail-soft)")
        check(bool(ctx), "C3 session start unaffected (context emitted)")
        check("- session_start_addon: error" in ctx, "C3 session_start_addon: error")
        check("ADDON-MARKER" not in ctx, "C3 no partial block leaked")

        # Case 4: addon dir exists but module missing -> ImportError -> 'error'.
        print("Case 4: addon directory present, module missing")
        home = make_home(tmp, "h4", "__DIR_ONLY__")
        cwd = make_cwd(tmp, "c4")
        rc, out, ctx = run_session_start(home, cwd)
        check(rc == 0, "C4 hook exits 0 (fail-soft)")
        check("- session_start_addon: error" in ctx, "C4 session_start_addon: error")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"{PASS} pass / {FAIL} fail")
    if FAIL:
        print("FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
