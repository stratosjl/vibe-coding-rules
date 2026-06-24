#!/usr/bin/env python3
"""Validation runner for the self-healing pre-push gate guard (v1.18.1, OBS-AUD-2).

SessionStart calls `ensure_pre_push_gate_armed(cwd)`. When the session runs
inside the vibe-coding-rules clone (the cwd's git root carries BOTH
`.githooks/pre-push` and `bin/install-hooks.sh`) and `core.hooksPath` is not
`.githooks`, the guard re-sets it directly via git so the publish-audit gate
cannot stay silently inert on a fresh clone. It is a no-op (`n/a`) in any other
repo, and fully fail-soft.

This runner drives hooks/session-start.py as a subprocess against a fake HOME
(HOME *and* USERPROFILE, per the v1.14.1 Windows divergence) with the cwd
pointed at synthetic git repos, and asserts the `pre_push_gate:` trace token
plus the real side effect on `core.hooksPath`:

    Case A  git repo, marker files present, hooksPath unset -> 're-armed',
            core.hooksPath becomes '.githooks', WARNING banner present
    Case B  same repo but hooksPath already '.githooks'     -> 'armed', no banner
    Case C  git repo WITHOUT the two marker files            -> 'n/a' (not this repo)
    Case D  cwd is not a git repo                            -> 'n/a', session intact

Cross-platform: pure stdlib, sys.executable (never the broken `python3` alias
on Windows per the user-global CLAUDE.md rule). Requires git on PATH.

Usage:
    python test-pre-push-gate-guard.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent
SESSION_START = PLUGIN_ROOT / "hooks" / "session-start.py"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL: {label}")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=10,
    )


def make_repo(tmp: Path, name: str, with_markers: bool, is_git: bool = True) -> Path:
    repo = tmp / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "CLAUDE.md").write_text("# test project\ntier: T0\n", encoding="utf-8")
    if is_git:
        git(repo, "init", "-q")
    if with_markers:
        (repo / ".githooks").mkdir(parents=True, exist_ok=True)
        (repo / ".githooks" / "pre-push").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (repo / "bin").mkdir(parents=True, exist_ok=True)
        (repo / "bin" / "install-hooks.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return repo


def hooks_path(repo: Path) -> str:
    res = git(repo, "config", "--local", "--get", "core.hooksPath")
    return res.stdout.strip() if res.returncode == 0 else ""


def make_home(tmp: Path, name: str) -> Path:
    home = tmp / name
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return home


def run_session_start(home: Path, cwd: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # v1.14.1: Path.home() uses USERPROFILE on Windows
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    event = {"session_id": f"test-gate-{cwd.name}", "cwd": str(cwd)}
    proc = subprocess.run(
        [sys.executable, str(SESSION_START)],
        input=json.dumps(event),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=20,
    )
    try:
        out: dict[str, Any] = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {}
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    return proc.returncode, ctx


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-gate-test-"))
    try:
        home = make_home(tmp, "home")

        # Case A: marker files present, hooksPath unset -> re-armed + side effect.
        print("Case A: vc-roe-like repo, core.hooksPath unset")
        repo = make_repo(tmp, "repoA", with_markers=True)
        check(hooks_path(repo) == "", "A precondition: core.hooksPath unset")
        rc, ctx = run_session_start(home, repo)
        check(rc == 0, "A hook exits 0")
        check("- pre_push_gate: re-armed" in ctx, "A trace token re-armed")
        check(hooks_path(repo) == ".githooks", "A core.hooksPath now set to .githooks")
        check("pre-push publish-audit gate was INERT" in ctx, "A WARNING banner present")

        # Case B: same shape but already armed -> armed, no banner.
        print("Case B: vc-roe-like repo, core.hooksPath already .githooks")
        repo = make_repo(tmp, "repoB", with_markers=True)
        git(repo, "config", "--local", "core.hooksPath", ".githooks")
        rc, ctx = run_session_start(home, repo)
        check(rc == 0, "B hook exits 0")
        check("- pre_push_gate: armed" in ctx, "B trace token armed")
        check("pre-push publish-audit gate was INERT" not in ctx, "B no re-arm banner")
        check(hooks_path(repo) == ".githooks", "B core.hooksPath unchanged")

        # Case C: a git repo WITHOUT the two marker files -> n/a (not this repo).
        print("Case C: unrelated git repo (no marker files)")
        repo = make_repo(tmp, "repoC", with_markers=False)
        rc, ctx = run_session_start(home, repo)
        check(rc == 0, "C hook exits 0")
        check("- pre_push_gate: n/a" in ctx, "C trace token n/a")
        check(hooks_path(repo) == "", "C core.hooksPath untouched (still unset)")

        # Case D: cwd not a git repo at all -> n/a, fail-soft.
        print("Case D: non-git cwd")
        repo = make_repo(tmp, "repoD", with_markers=False, is_git=False)
        rc, ctx = run_session_start(home, repo)
        check(rc == 0, "D hook exits 0 (fail-soft)")
        check("- pre_push_gate: n/a" in ctx, "D trace token n/a")
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
