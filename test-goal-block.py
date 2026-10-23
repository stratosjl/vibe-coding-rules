#!/usr/bin/env python3
"""Regression tests for bin/check-goal-block.py and the slices that cite it (v1.26.0).

The checker guards the `## Goal` block that an opening prompt carries for
Claude Code's `/goal` command. These tests prove three things:

  ARM A  a well-formed prompt passes, and each defect the checker exists
         to catch makes exactly that check fail (fixtures written here, so
         a check that can never fail would show up as a missing FAIL);
  ARM B  the checker's own --self-test passes;
  ARM C  the slices and the checker agree: the slices name the checker,
         the checker exists, and the canonical clause the T4 slice tells
         a session to paste contains every phrase the checker requires.

Pure stdlib. Usage: python3 test-goal-block.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKER = ROOT / "bin" / "check-goal-block.py"
SLICES = ROOT / "methodology-content"

NAME = "session-07-opening-prompt.md"

GOOD_GOAL = (
    "/goal Run session 7 of demo-project as written in handovers/" + NAME + ". "
    "Done only when all of this is shown in this conversation:\n"
    "1. Pre-next-chat verification items 1 to 4 run, each with its output shown.\n"
    "2. Session close done: handover, next opening prompt, decisions and open issues updated.\n"
    "Ask me before any confirmation-gated action or scope change; stopping to ask me counts as done. "
    "Or stop after 40 turns."
)


def prompt_with(goal_body: str | None, extra_goal_section: bool = False) -> str:
    parts = [
        "# Session 7 opening prompt\n",
        "## Read order\n\n1. CLAUDE.md\n",
        "## Pre-next-chat verification\n\n1. Run `git status -sb`; expect `## main...github/main`.\n",
    ]
    if goal_body is not None:
        parts.append("## Goal\n\n```\n" + goal_body + "\n```\n")
    if extra_goal_section:
        parts.append("## Goal\n\n```\n" + GOOD_GOAL + "\n```\n")
    parts.append("## Standing rules\n\nNone new.\n")
    return "\n".join(parts)


def run_checker(path: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(path), *args],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def failed_checks(output: str) -> set[str]:
    out = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("FAIL "):
            out.add(line.split()[1])
    return out


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_goal_block", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def arm_a(tmp: Path) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    if not CHECKER.is_file():
        return [(False, f"checker missing at {CHECKER}")]

    good = tmp / NAME
    good.write_text(prompt_with(GOOD_GOAL), encoding="utf-8")
    rc, out = run_checker(good)
    results.append((rc == 0 and not failed_checks(out),
                    f"A0 well-formed prompt passes (rc={rc}, fails={sorted(failed_checks(out))})"))

    # One defect per check. The expected set is exactly one check id, so a
    # defect that trips the wrong check, or several, is a failure too.
    over = GOOD_GOAL + " " + ("x" * 4001)
    cases = [
        ("G1", "no Goal section", prompt_with(None), False),
        ("G1", "two Goal sections", prompt_with(GOOD_GOAL, extra_goal_section=True), False),
        ("G2", "Goal section without a /goal fenced block",
         prompt_with(None) + "\n## Goal\n\nRun the session.\n", False),
        ("G3", "condition over 4,000 characters", prompt_with(over), False),
        ("G4", "condition names another session's prompt",
         prompt_with(GOOD_GOAL.replace(NAME, "session-06-opening-prompt.md")), False),
        ("G5", "no stop-to-ask clause",
         prompt_with(GOOD_GOAL.replace("stopping to ask me counts as done", "keep going")), False),
        ("G6", "no turn bound",
         prompt_with(GOOD_GOAL.replace(" Or stop after 40 turns.", "")), False),
        ("G7", "no shown-in-this-conversation evidence wording",
         prompt_with(GOOD_GOAL.replace("is shown in this conversation", "is true")), False),
    ]
    for expect, label, text, _ in cases:
        p = tmp / expect / NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        rc, out = run_checker(p)
        got = failed_checks(out)
        # G3 and G1 cases can legitimately suppress later checks; require
        # the expected id and rc=1, and that nothing else fired.
        ok = rc == 1 and got == {expect}
        results.append((ok, f"A {expect} {label}: rc={rc}, fails={sorted(got)}"))

    # Greek and other non-ASCII text is counted in characters, not bytes:
    # 1,500 Greek letters are 3,000 UTF-8 bytes but well under the cap.
    greek = GOOD_GOAL + " " + ("α" * 1500)
    p = tmp / "greek" / NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(prompt_with(greek), encoding="utf-8")
    rc, out = run_checker(p)
    results.append((rc == 0, f"A Greek text counted in characters not bytes (rc={rc})"))

    # A /goal line inside a fenced block of ANOTHER section must not count.
    stray = prompt_with(None).replace(
        "## Standing rules", "## Notes\n\n```\n" + GOOD_GOAL + "\n```\n\n## Standing rules")
    p = tmp / "stray" / NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(stray, encoding="utf-8")
    rc, out = run_checker(p)
    results.append((rc == 1 and failed_checks(out) == {"G1"},
                    f"A /goal block outside the Goal section is ignored (rc={rc}, fails={sorted(failed_checks(out))})"))

    # A '## Goal' line inside a fenced block is not a heading.
    fenced_heading = prompt_with(None).replace(
        "## Standing rules", "```\n## Goal\n```\n\n## Standing rules")
    p = tmp / "fenced-heading" / NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fenced_heading, encoding="utf-8")
    rc, out = run_checker(p)
    results.append((rc == 1 and failed_checks(out) == {"G1"},
                    f"A '## Goal' inside a code fence is not a heading (rc={rc})"))

    missing = tmp / "does-not-exist.md"
    rc, out = run_checker(missing)
    results.append((rc == 2, f"A unreadable file exits 2, not 0 or 1 (rc={rc})"))
    return results


def arm_b() -> list[tuple[bool, str]]:
    if not CHECKER.is_file():
        return [(False, "checker missing")]
    proc = subprocess.run([sys.executable, str(CHECKER), "--self-test"],
                          capture_output=True, text=True, timeout=60)
    return [(proc.returncode == 0, f"B checker --self-test rc={proc.returncode}: "
             f"{(proc.stdout.strip().splitlines() or ['(no output)'])[-1]}")]


def arm_c() -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    t2 = (SLICES / "T2.md").read_text(encoding="utf-8")
    t3 = (SLICES / "T3.md").read_text(encoding="utf-8")
    t4 = (SLICES / "T4.md").read_text(encoding="utf-8")

    results.append(("check-goal-block.py" in t2 and "check-goal-block.py" in t4,
                    "C T2 and T4 slices name check-goal-block.py"))
    results.append((CHECKER.is_file(), "C the named checker exists in bin/"))
    results.append(("T2 item 17" in t3 and "T2 item 17" in t4,
                    "C T3 and T4 inherited lists carry T2 item 17"))

    # T2 item 5 must ask for command-plus-expected-output checks.
    item5 = next((ln for ln in t2.splitlines() if ln.startswith("5. ")), "")
    results.append(("expect" in item5 and "shown" in item5,
                    "C T2 item 5 asks for a command and its expected output"))

    if not CHECKER.is_file():
        results.append((False, "C clause agreement skipped: checker missing"))
        return results
    mod = load_checker_module()
    clause = getattr(mod, "CANONICAL_GATE_CLAUSE", "")
    results.append((bool(clause) and clause in t4,
                    "C the T4 slice carries the checker's canonical gate clause verbatim"))
    results.append((bool(clause) and mod.GATE_PHRASE in clause.lower(),
                    "C the canonical clause satisfies the checker's own G5 phrase"))
    results.append((str(mod.MAX_CONDITION_CHARS) == "4000" and "4,000" in t2,
                    "C the 4,000-character cap agrees between checker and T2 slice"))
    return results


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vc-roe-goal-block-") as d:
        results = arm_a(Path(d)) + arm_b() + arm_c()
    fails = 0
    for ok, label in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
        fails += 0 if ok else 1
    print(f"\n{len(results) - fails} pass / {fails} fail")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
