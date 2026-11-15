#!/usr/bin/env python3
"""Validation runner for the retired SessionStart addon seam (v1.36.0).

From v1.15.0 to v1.35.x the SessionStart hook imported
`vc_roe_local_addons` from ~/.claude/vc-roe-addons whenever that directory
existed and spliced the block it returned into additionalContext. That ran a
file from another repository's working tree at every session start. v1.36.0
removes the import. The directory stays, because bin/audit-patterns.sh reads
the publish gate's overlay through it (test-audit-overlay-lookup.py covers
that), but this hook no longer looks at it.

This runner drives hooks/session-start.py as a subprocess against a fake HOME
(HOME *and* USERPROFILE, per the v1.14.1 Windows divergence: Path.home()
consults USERPROFILE on Windows) and asserts, for each shape of the directory,
that the module is never imported (a module that writes a marker file at
import leaves none), that no block reaches the context, that the trace reads
`session_start_addon: retired` and `session_start_addon_source: n/a`, and that
session start is unaffected:

    Case 1  no addon dir
    Case 2  a module that writes a marker at import and returns a block
    Case 3  a module that raises at import
    Case 4  the directory with no module

On the v1.35.x hook Case 2 fails: the marker is written and the block is
injected.

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
import atexit
import os

# VCR-I-49: every temp path this suite makes (its own folders, and the hooks' session
# anchors, written in process and by the subprocesses, which inherit the environment)
# goes under one private root, removed at exit, so the floor leaves nothing in the real
# temp directory. Set before any hook module is loaded: the hooks bind ANCHOR_DIR to
# tempfile.gettempdir() at import, and gettempdir() caches its first answer.
_FLOOR_TMP = tempfile.mkdtemp(prefix="vcroe-floor-addon-")
for _k in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_k] = _FLOOR_TMP
tempfile.tempdir = _FLOOR_TMP
atexit.register(shutil.rmtree, _FLOOR_TMP, True)

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


MARKING_ADDON = '''\
from pathlib import Path
Path(__file__).with_name("IMPORTED-MARKER").write_text("imported", encoding="utf-8")


def session_start_block(detection, tier):
    return ("## Addon block\\n\\nADDON-MARKER-XYZ for tier %s.\\n\\n" % tier, "clean")
'''

RAISING_ADDON = '''\
from pathlib import Path
Path(__file__).with_name("IMPORTED-MARKER").write_text("imported", encoding="utf-8")
raise RuntimeError("intentional addon failure at import")
'''


def check_retired(tag: str, rc: int, ctx: str, home: Path) -> None:
    addon_dir = home / ".claude" / "vc-roe-addons"
    check(rc == 0, f"{tag} hook exits 0")
    check(bool(ctx) and "## Tier detection trace" in ctx, f"{tag} emits the methodology context and trace")
    check("- session_start_addon: retired\n" in ctx, f"{tag} session_start_addon: retired")
    check("- session_start_addon_source: n/a\n" in ctx, f"{tag} session_start_addon_source: n/a")
    check("ADDON-MARKER" not in ctx, f"{tag} no addon block in the context")
    check(not (addon_dir / "IMPORTED-MARKER").exists(), f"{tag} the module was not imported (no marker file)")


def main() -> int:
    _force_utf8_streams()
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-addon-retired-"))
    try:
        cases = [("C1", None), ("C2", MARKING_ADDON), ("C3", RAISING_ADDON), ("C4", "__DIR_ONLY__")]
        for tag, module in cases:
            print(f"{tag}:")
            home = make_home(tmp, f"home-{tag}", module)
            rc, _, ctx = run_session_start(home, make_cwd(tmp, f"cwd-{tag}"))
            check_retired(tag, rc, ctx, home)
        src = SESSION_START.read_text(encoding="utf-8")
        check("import vc_roe_local_addons" not in src and "from vc_roe_local_addons" not in src,
              "C5 the hook source carries no import statement for the module")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS} pass / {FAIL} fail")
    if FAIL:
        for f in FAILURES:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
