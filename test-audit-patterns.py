#!/usr/bin/env python3
"""Synthetic-fixture regression test for bin/audit-patterns.sh.

Closes OBS-vcroe-s55-test-method-broken-by-v1.2.1-01. The pre-v1.2.1
working-tree probe (untracked `.s55-leak-test.txt` scanned by
`grep -rnE`) stopped working at v1.2.1 when publish-audit.sh switched
to `git grep -nE`, which skips untracked files. This test substitutes
that probe with a pattern-level unit assertion: each DENY and WARN
regex in bin/audit-patterns.sh is verified to match at least one
synthetic fixture line built at runtime by sanitizing the pattern to
its literal form.

The fixture strings are constructed from the patterns sourced from
bin/audit-patterns.sh at test time; no pattern literal is hard-coded
in this file. A self-audit step additionally asserts that no sanitized
literal appears as a contiguous substring of this file's own source,
so the audit gate stays tight without adding test-audit-patterns.py to
SCAN_EXCLUDE.

Pure stdlib. No working-tree side-effects.

Usage:
    python3 test-audit-patterns.py
    python3 test-audit-patterns.py --verbose
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
PATTERNS_FILE = PLUGIN_ROOT / "bin" / "audit-patterns.sh"


def extract_patterns(array_name: str) -> list[str]:
    cmd = [
        "bash",
        "-c",
        f'set -e; cd "{PLUGIN_ROOT}"; source bin/audit-patterns.sh; '
        f'printf "%s\\n" "${{{array_name}[@]}}"',
    ]
    out = subprocess.check_output(cmd, text=True).rstrip("\n")
    return out.split("\n") if out else []


def _class_literal(cls_body: str) -> str:
    if "0-9" in cls_body:
        return "1"
    if "A-Z" in cls_body:
        return "A"
    if "a-z" in cls_body:
        return "a"
    for c in cls_body:
        if c not in "-^":
            return c
    return "x"


def sanitize_to_literal(pattern: str) -> str:
    """Build a literal that the ERE pattern matches.

    Handles the regex constructs used in bin/audit-patterns.sh today:
    backslash word boundaries (dropped), escaped dot (unescaped),
    escaped whitespace (single space), single-bracket character class
    with optional `+`/`*`/`?` quantifier.

    Raises ValueError on unsupported ERE meta so a future pattern
    change surfaces as a test failure rather than a silent miss.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            nxt = pattern[i + 1] if i + 1 < len(pattern) else ""
            if nxt in ("b", "B"):
                i += 2
                continue
            if nxt == ".":
                out.append(".")
                i += 2
                continue
            if nxt == "s":
                out.append(" ")
                i += 2
                continue
            out.append(nxt)
            i += 2
        elif ch == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                raise ValueError(f"Unterminated character class in pattern: {pattern!r}")
            out.append(_class_literal(pattern[i + 1 : j]))
            i = j + 1
            if i < len(pattern) and pattern[i] in "*+?":
                i += 1
        elif ch in ("^", "$", "(", ")", "|", "*", "+", "?", "{"):
            raise ValueError(
                f"Pattern uses unsupported ERE meta {ch!r} in {pattern!r}; "
                f"extend sanitize_to_literal."
            )
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def build_fixture(literal: str) -> str:
    return f"sample-context [{literal}] trailing-context"


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    try:
        deny = extract_patterns("DENY_PATTERNS")
        warn = extract_patterns("WARN_PATTERNS")
    except subprocess.CalledProcessError as exc:
        print(f"test-audit-patterns: FAIL — could not source audit-patterns.sh: {exc}", file=sys.stderr)
        return 1

    if not deny:
        print("test-audit-patterns: FAIL — DENY_PATTERNS array empty", file=sys.stderr)
        return 1

    own_source = Path(__file__).read_text(encoding="utf-8")
    fails: list[str] = []

    for kind, patterns in (("DENY", deny), ("WARN", warn)):
        for pat in patterns:
            try:
                lit = sanitize_to_literal(pat)
            except ValueError as exc:
                fails.append(f"{kind} {pat!r}: sanitize error: {exc}")
                continue
            fixture = build_fixture(lit)
            try:
                m = re.search(pat, fixture)
            except re.error as exc:
                fails.append(f"{kind} {pat!r}: invalid as Python regex: {exc}")
                continue
            if not m:
                fails.append(f"{kind} {pat!r}: did not match fixture {fixture!r}")
                continue
            if lit in own_source:
                fails.append(
                    f"{kind} {pat!r}: literal {lit!r} appears in test source — "
                    f"audit gate hole risk; refactor to keep literal out of source."
                )
                continue
            if verbose:
                print(f"  OK  {kind:4} {pat!r}  fixture-literal={lit!r}")

    if fails:
        print("test-audit-patterns: FAIL", file=sys.stderr)
        for line in fails:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(
        f"test-audit-patterns: OK ({len(deny)} DENY + {len(warn)} WARN patterns verified, "
        f"self-audit clean)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
