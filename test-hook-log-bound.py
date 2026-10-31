#!/usr/bin/env python3
"""Hook log stays bounded (v1.30.1).

Every hook appends one JSON line per run to ~/.claude/methodology-hook.log, and
nothing trimmed it: measured 2026-10-31, 6,976,203 bytes on one node and
5,064,285 on another. Each writer now moves the log to `.1` once it reaches
LOG_MAX_BYTES and starts a fresh file, so the pair stays under about 300 KB.

For each of the six writers this test, in a throwaway HOME:
  1. below the cap: a write appends and creates no `.1`;
  2. at the cap: a write moves the old log to `.1` byte for byte and the new
     log holds exactly the new line;
  3. control: with the cap raised out of reach the same oversized log is NOT
     rotated, which proves check 2 can fail.

Pure stdlib. Usage: python test-hook-log-bound.py ; exit code is the verdict.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
WRITERS = [
    "hooks/session-start.py",
    "hooks/stop.py",
    "hooks/user-prompt-submit.py",
    "hooks/post-tool-use.py",
    "hooks/session-end.py",
    "hooks/kimi/_adapter.py",
]


def load(rel: str, home: Path):
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(REPO_ROOT))
    path = REPO_ROOT / rel
    sys.path.insert(0, str((REPO_ROOT / rel).parent))
    try:
        spec = importlib.util.spec_from_file_location(
            "vcroe_" + rel.replace("/", "_").replace("-", "_").replace(".py", ""), path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8").splitlines() if p.is_file() else []


def check(rel: str, tmp: Path) -> list[str]:
    fails: list[str] = []
    home = tmp / rel.replace("/", "_")
    (home / ".claude").mkdir(parents=True)
    mod = load(rel, home)
    log = mod.LOG_PATH
    rotated = log.with_name(log.name + ".1")
    if log.parent != home / ".claude":
        return [f"{rel}: LOG_PATH {log} is not under the test HOME"]
    cap = getattr(mod, "LOG_MAX_BYTES", None)
    if not isinstance(cap, int):
        return [f"{rel}: no LOG_MAX_BYTES, so the log is unbounded"]
    if not (0 < cap <= 150_000):
        fails.append(f"{rel}: LOG_MAX_BYTES={cap}, expected 1..150000")

    # 1. below the cap
    mod.append_log({"hook": "t", "n": 1})
    if len(lines(log)) != 1 or rotated.exists():
        fails.append(f"{rel}: below cap, expected 1 line and no .1")

    # 2. at the cap
    filler = (json.dumps({"hook": "fill", "pad": "x" * 200}) + "\n")
    log.write_text(filler * (cap // len(filler) + 1), encoding="utf-8")
    before = log.read_bytes()
    mod.append_log({"hook": "t", "n": 2})
    if not rotated.is_file() or rotated.read_bytes() != before:
        fails.append(f"{rel}: at cap, .1 missing or not byte-identical to the old log")
    new = lines(log)
    if len(new) != 1 or json.loads(new[0]).get("n") != 2:
        fails.append(f"{rel}: at cap, new log should hold only the new line, got {len(new)}")

    # 3. control: cap out of reach, so no rotation
    rotated.unlink(missing_ok=True)
    log.write_bytes(before)
    setattr(mod, "LOG_MAX_BYTES", 10**12)
    mod.append_log({"hook": "t", "n": 3})
    if rotated.exists() or len(lines(log)) != len(before.splitlines()) + 1:
        fails.append(f"{rel}: control rotated or lost a line with the cap disabled")
    return fails


def main() -> int:
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vc-roe-logbound-") as d:
        try:
            for rel in WRITERS:
                f = check(rel, Path(d))
                print(("FAIL " if f else "pass ") + rel)
                fails += f
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    for f in fails:
        print("  " + f)
    print(f"{len(WRITERS) - len({f.split(':')[0] for f in fails})} pass / "
          f"{len({f.split(':')[0] for f in fails})} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
