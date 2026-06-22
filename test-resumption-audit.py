#!/usr/bin/env python3
"""Regression suite for the v1.15.0 resumption audit (T3 item 12).

Run: python test-resumption-audit.py
Zero-framework, same convention as the sibling test-*.py suites: numbered
cases, PASS/FAIL counters, exit 0 iff all pass. Builds throwaway git repos
under a tempdir; never touches the operator's real projects or config.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# session-start.py has a hyphenated name; import via spec.
HOOK_PATH = Path(__file__).resolve().parent / "hooks" / "session-start.py"
spec = importlib.util.spec_from_file_location("session_start", HOOK_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PASS = 0
FAIL = 0


def check(case: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {case}")
    else:
        FAIL += 1
        print(f"FAIL  {case}  {detail}")


def git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *args],
                         capture_output=True, text=True, check=True)
    return res.stdout.strip()


def make_repo(base: Path, name: str, commits: int = 1) -> Path:
    repo = base / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(f"content {i}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"c{i}")
    return repo


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-resaudit-"))
    try:
        # Case 1: below T3 -> skipped, empty block.
        block, state = mod.resumption_audit_block(None, "T2", False)
        check("01 below-T3 skipped", block == "" and state == "skipped-below-T3",
              f"state={state}")

        # Case 2: T3 with no git root -> no-repo guidance.
        block, state = mod.resumption_audit_block(None, "T3", False)
        check("02 no-repo state", state == "no-repo", f"state={state}")
        check("03 no-repo text", "NO git repository" in block, block[:80])

        # Case 4: repo with remote, clean tree, no upstream -> clean verdict.
        r1 = make_repo(tmp, "clean-remote")
        git(r1, "remote", "add", "origin",
            "git@github.com:example/clean-remote.git")
        block, state = mod.resumption_audit_block(r1, "T3", True)
        check("04 clean verdict", state == "clean", f"state={state}\n{block}")
        check("05 remote classified",
              "hosted forge (github)" in block, block)

        # Case 6: uncommitted change -> diverged.
        (r1 / "dirty.txt").write_text("x\n", encoding="utf-8")
        block, state = mod.resumption_audit_block(r1, "T3", True)
        check("06 dirty diverged", state == "diverged", f"state={state}")
        check("07 dirty count", "1 uncommitted change(s)" in block, block)
        (r1 / "dirty.txt").unlink()

        # Case 8: no remote, no ***REMOVED*** -> clause-1 violation, diverged.
        r2 = make_repo(tmp, "no-remote")
        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("08 no-remote diverged", state == "diverged"
              and "NONE" in block, f"state={state}")

        # Case 9: ***REMOVED*** sentinel + no remote -> softer wording (pending
        # local-forge deployment), but ***REMOVED*** unconfigured -> diverged.
        mod.LOCAL_CONFIG_PATH = tmp / "nonexistent.json"
        (r2 / "CLAUDE.md").write_text("---\ntier: T3\n***REMOVED***: ***REMOVED***\n---\n",
                                      encoding="utf-8")
        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("09 ***REMOVED*** read", "***REMOVED*** `***REMOVED***`" in block, block)
        check("10 no-config diverged", state == "diverged"
              and "***REMOVED*** not configured" in block, block)

        # Cases 11+: bundle states with configured ***REMOVED***.
        proot = tmp / "portable"
        (proot / r2.name).mkdir(parents=True)
        cfg = tmp / "***REMOVED***"
        cfg.write_text('{"***REMOVED***": "%s"}' % str(proot).replace("\\", "\\\\"),
                       encoding="utf-8")
        mod.LOCAL_CONFIG_PATH = cfg

        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("11 bundle missing", "bundle MISSING" in block and state == "diverged",
              block)

        bundle = proot / r2.name / f"{r2.name}.git.bundle"
        git(r2, "add", "-A")
        git(r2, "commit", "-q", "-m", "claude-md")
        git(r2, "bundle", "create", str(bundle), "--all")
        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("12 bundle in sync", "in sync" in block and state == "clean",
              f"state={state}\n{block}")

        # Local ahead of bundle.
        (r2 / "ahead.txt").write_text("a\n", encoding="utf-8")
        git(r2, "add", "-A")
        git(r2, "commit", "-q", "-m", "ahead")
        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("13 local ahead", "local is AHEAD of bundle" in block
              and state == "diverged", block)

        # Bundle ahead of local: re-bundle at tip, then rewind local.
        git(r2, "bundle", "create", str(bundle), "--all")
        git(r2, "reset", "-q", "--hard", "HEAD~1")
        block, state = mod.resumption_audit_block(r2, "T3", True)
        check("14 bundle ahead", "bundle is AHEAD" in block
              and state == "diverged", block)

        # Case 15: T4 also audited.
        block, state = mod.resumption_audit_block(r1, "T4", True)
        check("15 T4 audited", state in ("clean", "diverged") and block, state)

        # Case 16: remote classification unit checks.
        check("16 classify ***REMOVED***-class",
              mod._classify_remote("http://localhost:3000/op/x.git")
              == "local forge (***REMOVED***-class)", "")
        check("17 classify other hosted",
              mod._classify_remote("git@gitlab.com:op/x.git")
              == "hosted forge (other)", "")

        # Case 18: ***REMOVED*** reader ignores files without the sentinel.
        check("18 ***REMOVED*** none",
              mod.find_***REMOVED***_in_claude_md(r1 / "CLAUDE.md") is None, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} pass / {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
