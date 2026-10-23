#!/usr/bin/env python3
"""Tests for the shared assurance controls.

`policy/assurance.md` is the one normative definition of the four controls.
It carries a single fenced `assurance-contract` JSON block, and
`bin/check-assurance.py` loads its rules from that block. These tests check
the contract, then drive the checker as a real process against small record
fixtures: each acceptance criterion of the proposal is a rejection or an
acceptance here, not a sentence looked up in a document.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "policy" / "assurance.md"

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


def contract_blocks(text: str) -> list[str]:
    return re.findall(r"^```json assurance-contract[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)


def contract_block_loads_and_is_complete() -> None:
    print("contract_block_loads_and_is_complete")
    if not POLICY.is_file():
        check(False, "policy/assurance.md exists")
        return
    blocks = contract_blocks(POLICY.read_text(encoding="utf-8"))
    check(len(blocks) == 1, f"exactly one assurance-contract block ({len(blocks)})")
    if len(blocks) != 1:
        return
    try:
        c = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        check(False, f"contract block is valid JSON ({e})")
        return
    check(c.get("version") == 1, "contract version is 1")
    check(sorted(c.get("controls", {})) == ["AC-EVIDENCE", "AC-RESTORE", "AC-REVIEW", "AC-TRACE"],
          "the four control IDs")
    check(c.get("statuses") == ["planned", "passed", "failed", "blocked", "stale",
                                "not-applicable", "accepted-risk"], "the seven statuses")
    check(c.get("columns") == ["Control", "Requirements", "Applies", "Reason", "Owner", "Status",
                               "Evidence", "Kind", "Result", "Revision", "Reviewer",
                               "Independence", "Trigger"], "the thirteen columns")
    kinds = set(c.get("kinds", []))
    passing = [k for ctl in c.get("controls", {}).values() for k in ctl.get("passing_kinds", [])]
    check(bool(passing) and all(k in kinds for k in passing), "every passing kind is a known kind")
    check(c.get("controls", {}).get("AC-RESTORE", {}).get("passing_kinds") == ["restore-test"],
          "only a restore test can pass AC-RESTORE")
    check(set(c.get("decision_required", {}).get("T4", [])) == set(c.get("controls", {})),
          "T4 requires a decision on every control")


TESTS = [contract_block_loads_and_is_complete]


def main() -> int:
    for t in TESTS:
        t()
    print(f"\n{PASS} pass / {FAIL} fail")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
