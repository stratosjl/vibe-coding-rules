#!/usr/bin/env python3
"""Validation runner for bin/claude-md-sentinel.py (v1.13.0).

Generates synthetic CLAUDE.md fixtures under a tempdir, invokes the helper,
asserts:
    - action string emitted (created|prepended|inserted|noop|replaced)
    - resulting file content matches expected
    - rest-of-file content preserved verbatim
    - idempotency (re-invoking with the same tier emits noop)

Cleans up its fixtures on exit. Returns exit code 0 on full pass; nonzero
with a diff report on any failure.

Cross-platform: pure stdlib, sys.executable (not the broken `python3` alias
on Windows per the user-global CLAUDE.md rule).

Usage:
    python test-claude-md-sentinel.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

PLUGIN_ROOT = Path(__file__).resolve().parent
HELPER = PLUGIN_ROOT / "bin" / "claude-md-sentinel.py"


def make_fixture(parent: Path, name: str, initial_content: Optional[str]) -> Path:
    fix = parent / name
    fix.mkdir(parents=True, exist_ok=True)
    (fix / ".git").mkdir(exist_ok=True)
    if initial_content is not None:
        (fix / "CLAUDE.md").write_text(initial_content, encoding="utf-8", newline="")
    return fix


def run_helper(cwd: Path, tier: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(HELPER), tier],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def assert_eq(label: str, actual: str, expected: str) -> bool:
    if actual == expected:
        print(f"  PASS: {label}")
        return True
    print(f"  FAIL: {label}")
    print(f"    expected: {expected!r}")
    print(f"    actual:   {actual!r}")
    return False


def assert_contains(label: str, haystack: str, needle: str) -> bool:
    if needle in haystack:
        print(f"  PASS: {label} (contains {needle!r})")
        return True
    print(f"  FAIL: {label}")
    print(f"    haystack: {haystack!r}")
    print(f"    needle:   {needle!r}")
    return False


def case_create(parent: Path) -> bool:
    """Case 1: no CLAUDE.md exists → create with frontmatter only."""
    print("\n== Case 1: create from absent CLAUDE.md ==")
    fix = make_fixture(parent, "case1-create", None)
    rc, stdout, stderr = run_helper(fix, "T4")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=created", stdout, "created")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    ok &= assert_eq("file content", content, "---\ntier: T4\n---\n")
    return ok


def case_noop(parent: Path) -> bool:
    """Case 2: existing frontmatter with same tier → noop, idempotent."""
    print("\n== Case 2: noop on idempotent re-invocation ==")
    fix = make_fixture(parent, "case2-noop", "---\ntier: T4\n---\n")
    rc, stdout, stderr = run_helper(fix, "T4")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=noop", stdout, "noop")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    ok &= assert_eq("file content unchanged", content, "---\ntier: T4\n---\n")
    return ok


def case_replace_in_frontmatter(parent: Path) -> bool:
    """Case 3: existing frontmatter with different tier → replace."""
    print("\n== Case 3: replace tier in existing frontmatter ==")
    initial = "---\ndescription: existing\ntier: T2\nfoo: bar\n---\n\n# Project\n"
    fix = make_fixture(parent, "case3-replace-fm", initial)
    rc, stdout, _ = run_helper(fix, "T4")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=replaced", stdout, "replaced")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    expected = "---\ndescription: existing\ntier: T4\nfoo: bar\n---\n\n# Project\n"
    ok &= assert_eq("file content (tier replaced, rest preserved)", content, expected)
    return ok


def case_insert_into_frontmatter(parent: Path) -> bool:
    """Case 4: existing frontmatter without tier → insert."""
    print("\n== Case 4: insert tier into existing frontmatter ==")
    initial = "---\ndescription: existing\nfoo: bar\n---\n\n# Project\n"
    fix = make_fixture(parent, "case4-insert-fm", initial)
    rc, stdout, _ = run_helper(fix, "T3")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=inserted", stdout, "inserted")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    expected = "---\ndescription: existing\nfoo: bar\ntier: T3\n---\n\n# Project\n"
    ok &= assert_eq("file content (tier inserted at end of frontmatter)", content, expected)
    return ok


def case_prepend_frontmatter(parent: Path) -> bool:
    """Case 5: CLAUDE.md without frontmatter → prepend new frontmatter block."""
    print("\n== Case 5: prepend frontmatter to file without one ==")
    initial = "# My Project\n\nSome content here.\n"
    fix = make_fixture(parent, "case5-prepend", initial)
    rc, stdout, _ = run_helper(fix, "T2")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=prepended", stdout, "prepended")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    expected = "---\ntier: T2\n---\n\n# My Project\n\nSome content here.\n"
    ok &= assert_eq("file content (frontmatter prepended, body preserved)", content, expected)
    return ok


def case_replace_bare_legacy(parent: Path) -> bool:
    """Case 6: bare legacy `tier: T<N>` line outside frontmatter → replace value, preserve surrounding whitespace."""
    print("\n== Case 6: replace bare legacy tier line ==")
    initial = "# Project\n\ntier: T1\n\nsome content\n"
    fix = make_fixture(parent, "case6-bare-legacy", initial)
    rc, stdout, _ = run_helper(fix, "T3")
    ok = True
    ok &= rc == 0
    ok &= assert_contains("action=replaced", stdout, "replaced")
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    expected = "# Project\n\ntier: T3\n\nsome content\n"
    ok &= assert_eq(
        "file content (tier value updated, blank lines preserved)",
        content,
        expected,
    )
    return ok


def case_invalid_tier(parent: Path) -> bool:
    """Case 7: invalid tier argument → exit 1, no CLAUDE.md created."""
    print("\n== Case 7: invalid tier argument rejected ==")
    fix = make_fixture(parent, "case7-invalid", None)
    rc, _, stderr = run_helper(fix, "T9")
    ok = True
    ok &= rc == 1
    ok &= assert_contains("error on invalid tier", stderr, "invalid tier")
    ok &= not (fix / "CLAUDE.md").exists()
    if (fix / "CLAUDE.md").exists():
        print("  FAIL: CLAUDE.md was created despite invalid tier")
        ok = False
    else:
        print("  PASS: no CLAUDE.md created for invalid tier")
    return ok


def case_cross_machine_simulation(parent: Path) -> bool:
    """Case 8: simulate the multi-machine scenario from CLAUDE.md project rules.

    Machine A elevates to T4 (creates CLAUDE.md sentinel). The CLAUDE.md gets
    git-synced. Machine B clones the repo, runs detection from a fresh
    `~/.claude/projects/` (no floor file), and would have to rely on the
    sentinel to start at T4. This test does not invoke the SessionStart hook;
    it asserts only that the helper produced a sentinel matching the reader
    regex `^\\s*tier:\\s*T([0-4])\\b` used by session-start.py:157."""
    import re
    print("\n== Case 8: sentinel matches reader regex (cross-machine portability) ==")
    fix = make_fixture(parent, "case8-cross-machine", None)
    rc, _, _ = run_helper(fix, "T4")
    ok = rc == 0
    content = (fix / "CLAUDE.md").read_text(encoding="utf-8")
    sentinel_re = re.compile(r"^\s*tier:\s*T([0-4])\b", re.MULTILINE | re.IGNORECASE)
    m = sentinel_re.search(content)
    if m and m.group(1) == "4":
        print("  PASS: written sentinel parsed by reader regex as T4")
    else:
        print("  FAIL: written sentinel does NOT parse via session-start reader regex")
        print(f"    content: {content!r}")
        ok = False
    return ok


def main() -> int:
    if not HELPER.is_file():
        print(f"FATAL: helper not found at {HELPER}", file=sys.stderr)
        return 2

    parent = Path(tempfile.mkdtemp(prefix="vc-roe-sentinel-tests-"))
    try:
        results = [
            case_create(parent),
            case_noop(parent),
            case_replace_in_frontmatter(parent),
            case_insert_into_frontmatter(parent),
            case_prepend_frontmatter(parent),
            case_replace_bare_legacy(parent),
            case_invalid_tier(parent),
            case_cross_machine_simulation(parent),
        ]
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n== {passed}/{total} cases passed ==")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
