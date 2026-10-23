#!/usr/bin/env python3
"""Check the `## Goal` block of a vc-roe opening prompt (added v1.26.0).

An opening prompt for a Claude Code session ends with a `## Goal` section
holding one fenced block that starts with `/goal `, ready to paste
(T2 item 17). This script checks that block before the session closes,
because nothing else would notice a goal that is too long to paste, points
at the previous session's prompt, or has lost its gate clause.

Checks, each reported PASS, FAIL or SKIP:

  G1  exactly one `## Goal` heading outside code fences
  G2  that section holds a fenced block whose text starts with `/goal `
  G3  the condition (the text after `/goal `) is 1 to 4,000 characters,
      counted in UTF-16 code units, which is never fewer than the
      characters a user sees, so the count errs on the safe side
  G4  the condition names this file's own basename, so a goal copied from
      the previous session's prompt fails
  G5  the condition carries the stop-to-ask clause ("counts as done")
  G6  the condition carries a bound ("or stop after ...")
  G7  the condition asks for evidence shown in the conversation
      ("shown in this conversation"), because the goal evaluator reads
      only the conversation and runs no command

When G1 or G2 fails, the later checks have nothing to read and are
reported SKIP rather than FAIL, so one defect yields one FAIL.

Exit codes: 0 all checks pass; 1 at least one FAIL; 2 the file could not
be read or the arguments were wrong.

Usage:
    check-goal-block.py handovers/session-12-opening-prompt.md
    check-goal-block.py --self-test

Pure stdlib.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

MAX_CONDITION_CHARS = 4000
GOAL_PREFIX = "/goal "
GATE_PHRASE = "counts as done"
BOUND_RE = re.compile(r"\bor stop after\b", re.IGNORECASE)
EVIDENCE_PHRASE = "shown in this conversation"

# The clause the T4 slice tells a session to paste verbatim.
# test-goal-block.py asserts the slice carries this exact text.
CANONICAL_GATE_CLAUSE = (
    "Ask me before any confirmation-gated action or scope change; "
    "stopping to ask me counts as done."
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def scan(lines: list[str]) -> tuple[list[int], list[tuple[int, int, str]]]:
    """Return (goal heading line numbers, fenced blocks as (start, end, body))."""
    goal_headings: list[int] = []
    blocks: list[tuple[int, int, str]] = []
    fence: str | None = None
    start = 0
    body: list[str] = []
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                start = i
                body = []
                continue
            h = HEADING_RE.match(line)
            if h and len(h.group(1)) == 2 and h.group(2).strip().lower() == "goal":
                goal_headings.append(i)
        else:
            closing = m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                and line.strip() == m.group(1)
            if closing:
                blocks.append((start, i, "\n".join(body)))
                fence = None
            else:
                body.append(line)
    return goal_headings, blocks


def section_end(lines: list[str], heading: int, blocks: list[tuple[int, int, str]]) -> int:
    """First line after the Goal heading that starts a level 1 or 2 heading outside a fence."""
    inside = set()
    for s, e, _ in blocks:
        inside.update(range(s, e + 1))
    for i in range(heading + 1, len(lines)):
        if i in inside:
            continue
        h = HEADING_RE.match(lines[i])
        if h and len(h.group(1)) <= 2:
            return i
    return len(lines)


def check(path: Path) -> list[tuple[str, str, str]]:
    """Return a list of (check id, PASS|FAIL|SKIP, detail)."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    out: list[tuple[str, str, str]] = []
    later = ["G3", "G4", "G5", "G6", "G7"]

    headings, blocks = scan(lines)
    if len(headings) != 1:
        out.append(("G1", "FAIL", f"found {len(headings)} '## Goal' headings outside code fences, need exactly 1"))
        out.append(("G2", "SKIP", "no single Goal section"))
        out.extend((c, "SKIP", "no single Goal section") for c in later)
        return out
    out.append(("G1", "PASS", f"one '## Goal' heading at line {headings[0] + 1}"))

    end = section_end(lines, headings[0], blocks)
    goals = [b for b in blocks if headings[0] < b[0] < end and b[2].strip().startswith(GOAL_PREFIX.strip())]
    goals = [b for b in goals if b[2].strip()[len(GOAL_PREFIX.strip()):][:1] in (" ", "\n", "\t")]
    if len(goals) != 1:
        out.append(("G2", "FAIL", f"found {len(goals)} fenced blocks starting with '/goal ' in the Goal section, need exactly 1"))
        out.extend((c, "SKIP", "no /goal block") for c in later)
        return out
    out.append(("G2", "PASS", f"/goal block at lines {goals[0][0] + 1}-{goals[0][1] + 1}"))

    condition = goals[0][2].strip()[len(GOAL_PREFIX.strip()):].strip()
    n = utf16_len(condition)
    if 0 < n <= MAX_CONDITION_CHARS:
        out.append(("G3", "PASS", f"condition is {n} characters (cap {MAX_CONDITION_CHARS})"))
    else:
        out.append(("G3", "FAIL", f"condition is {n} characters; it must be 1 to {MAX_CONDITION_CHARS}"))

    low = condition.lower()
    name = path.name
    out.append(("G4", "PASS", f"names {name}") if name in condition
               else ("G4", "FAIL", f"does not name this file ({name}); a goal copied from another prompt points at the wrong session"))
    out.append(("G5", "PASS", "stop-to-ask clause present") if GATE_PHRASE in low
               else ("G5", "FAIL", f"no stop-to-ask clause; paste: {CANONICAL_GATE_CLAUSE}"))
    out.append(("G6", "PASS", "bound present") if BOUND_RE.search(condition)
               else ("G6", "FAIL", "no bound; end the condition with 'Or stop after <N> turns.'"))
    out.append(("G7", "PASS", "asks for evidence shown in the conversation") if EVIDENCE_PHRASE in low
               else ("G7", "FAIL", f"does not say '{EVIDENCE_PHRASE}'; the evaluator reads only the conversation"))
    return out


def report(path: Path) -> int:
    try:
        results = check(path)
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR cannot read {path}: {e}", file=sys.stderr)
        return 2
    print(f"check-goal-block: {path}")
    for cid, status, detail in results:
        print(f"  {status} {cid} {detail}")
    fails = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"{'OK' if fails == 0 else 'FAILED'}: {fails} fail of {len(results)} checks")
    return 0 if fails == 0 else 1


def self_test() -> int:
    """Prove every check can fail: one good prompt, then one defect per check."""
    name = "session-03-opening-prompt.md"
    goal = (
        f"/goal Run session 3 as written in handovers/{name}. "
        f"Done only when all of this is shown in this conversation: the verification items, each with output shown. "
        f"{CANONICAL_GATE_CLAUSE} Or stop after 30 turns."
    )

    def doc(g: str) -> str:
        return f"# Prompt\n\n## Goal\n\n```\n{g}\n```\n\n## Rules\n\nNone.\n"

    cases = {
        None: doc(goal),
        "G1": "# Prompt\n\n## Rules\n\nNone.\n",
        "G2": "# Prompt\n\n## Goal\n\nNo block here.\n",
        "G3": doc(goal + " " + "y" * MAX_CONDITION_CHARS),
        "G4": doc(goal.replace(name, "session-02-opening-prompt.md")),
        "G5": doc(goal.replace(CANONICAL_GATE_CLAUSE, "Keep going.")),
        "G6": doc(goal.replace(" Or stop after 30 turns.", "")),
        "G7": doc(goal.replace("shown in this conversation", "true")),
    }
    misses = 0
    with tempfile.TemporaryDirectory(prefix="check-goal-block-") as d:
        for expect, text in cases.items():
            sub = Path(d) / (expect or "good")
            sub.mkdir()
            p = sub / name
            p.write_text(text, encoding="utf-8")
            fails = {c for c, s, _ in check(p) if s == "FAIL"}
            want = set() if expect is None else {expect}
            ok = fails == want
            misses += 0 if ok else 1
            print(f"  [{'OK  ' if ok else 'FAIL'}] {expect or 'good prompt'}: fails={sorted(fails)}")
    print(f"self-test: {len(cases) - misses} pass / {misses} fail")
    return 0 if misses == 0 else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__.split("Usage:")[1].split("Pure stdlib")[0].rstrip(), file=sys.stderr)
        return 2
    return report(Path(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
