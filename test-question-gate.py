#!/usr/bin/env python3
"""Regression tests for the T4 question gate (hooks/pre-tool-use.py, v1.35.0).

The gate exists because of one measured failure, 2026-11-15: a T4 session
asked the operator "start the release now, or leave it for a later session?"
after the operator had made the substantive choice. These tests run the real
hook in a subprocess, with a temporary anchor, home and transcript, and judge
each case by the parsed JSON decision and the exit code.

  ARM A  denials: that question untagged; the same question tagged "scope";
         a "(Recommended)" option; an unknown category; a reviewer basis
         with no advisor call, or one only before the operator's last message
  ARM B  allows: a records question with a real basis; a push-and-tag
         question; a reviewer basis after an advisor call; below T4; no
         anchor; another tool
  ARM C  fail-open: malformed input is allowed and logged as an error
  ARM D  lockstep: the hook and bin/check-goal-block.py carry the same
         category set; hooks.json registers the hook for AskUserQuestion;
         the T4 slice names the gate; check-goal-block G8 fails the
         prompt line that recorded the timing question as waiting

Pure stdlib. Usage: python3 test-question-gate.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOOK = ROOT / "hooks" / "pre-tool-use.py"
CHECKER = ROOT / "bin" / "check-goal-block.py"
SID = "test-question-gate-session"

# The 2026-11-15 question, with the companion repository's name removed.
TONIGHT = (
    "The companion's D-73 (your choice of option (c)) needs a new release: remove the SessionStart "
    "import from hooks/session-start.py, keep the publish gate's lookup through the link, and say how "
    "the session_start_addon trace lines read afterwards. Changing code and tagging a release is "
    "outside what this session was asked to do. Start it now, or leave it for a later session?"
)
TONIGHT_OPTIONS = [
    {"label": "Leave for later", "description": "Stop here. The next session picks it up."},
    {"label": "Start it now", "description": "Brainstorm and plan the change in this session first."},
]


def q(text: str, options: list[dict], header: str = "Release") -> dict:
    return {"questions": [{"question": text, "header": header, "options": options, "multiSelect": False}]}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(tool_input, tier: str | None = "T4", transcript: list[dict] | None = None,
        tool_name: str = "AskUserQuestion") -> tuple[int, dict | None, list[dict]]:
    with tempfile.TemporaryDirectory(prefix="qgate-") as d:
        d = Path(d)
        (d / "home" / ".claude").mkdir(parents=True)
        if tier is not None:
            (d / f"claude-methodology-anchor-{SID}").write_text(f"T0=1\nLAST_HEARTBEAT=0\nTIER={tier}\n",
                                                                 encoding="utf-8")
        tp = d / "transcript.jsonl"
        tp.write_text("\n".join(json.dumps(e) for e in (transcript or [])) + "\n", encoding="utf-8")
        payload = {"session_id": SID, "transcript_path": str(tp), "hook_event_name": "PreToolUse",
                   "tool_name": tool_name, "tool_input": tool_input}
        env = dict(os.environ, TMPDIR=str(d), TEMP=str(d), TMP=str(d), HOME=str(d / "home"),
                   USERPROFILE=str(d / "home"))
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, timeout=30)
        out = json.loads(p.stdout) if p.stdout.strip() else None
        log = d / "home" / ".claude" / "methodology-hook.log"
        entries = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return p.returncode, out, entries


def decision(out: dict | None) -> str:
    if out is None:
        return "allow"
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "?")


def codes(out: dict | None) -> set[str]:
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    return {c for c in ("Q1", "Q2", "Q3", "Q4") if f"- {c} " in reason}


OPERATOR = {"type": "user", "message": {"role": "user", "content": "please carry on"}}
ADVISOR = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "server_tool_use", "id": "srvtoolu_x", "name": "advisor", "input": {}}]}}
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}

RECORDS_Q = ("Should I record this sitting, or leave the records as they are?\n"
             "Basis: scope - the goal covered the audit and floor only; recording adds commits and an upload "
             "the goal did not include")
RECORDS_OPTIONS = [{"label": "Skip, close here", "description": "Leave the records."},
                   {"label": "Record and refresh", "description": "Commit readings and refresh the handover."}]
PUSH_Q = ("Push the release branch to GitHub and tag v1.35.0?\n"
          "Basis: confirmation-gated - a tag ships to every node at the next nightly; no approval is on record")
PUSH_OPTIONS = [{"label": "Push and tag v1.35.0", "description": "Fast-forward main, annotated tag."},
                {"label": "Do not push", "description": "Keep it on the branch."}]
REVIEWER_Q = ("Which retention period applies to the overlay backups?\n"
              "Basis: reviewer-only-operator-knows - the advisor says it rests on the firm's retention policy")
REVIEWER_OPTIONS = [{"label": "30 days", "description": "a"}, {"label": "1 year", "description": "b"}]


def main() -> int:
    results: list[tuple[bool, str]] = []

    def expect(name: str, rc_out, want: str, want_codes: set[str] | None = None) -> None:
        rc, out, _ = rc_out
        got = decision(out)
        ok = rc == 0 and got == want and (want_codes is None or want_codes <= codes(out))
        results.append((ok, f"{name}: rc={rc} decision={got} codes={sorted(codes(out))}"))

    # ARM A: denials
    expect("A1 the 2026-11-15 question, untagged", run(q(TONIGHT, TONIGHT_OPTIONS)), "deny", {"Q1", "Q3"})
    expect("A2 the same question tagged 'scope'",
           run(q(TONIGHT + "\nBasis: scope - building the release was not on tonight's agenda", TONIGHT_OPTIONS)),
           "deny", {"Q3"})
    expect("A3 an option marked (Recommended)",
           run(q(PUSH_Q, [dict(PUSH_OPTIONS[0], label="Push and tag v1.35.0 (Recommended)"), PUSH_OPTIONS[1]])),
           "deny", {"Q2"})
    expect("A4 a category not on the list",
           run(q("Which order?\nBasis: engineering - two equally good orders", RECORDS_OPTIONS)), "deny", {"Q1"})
    expect("A5 reviewer basis, no advisor call at all",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR]), "deny", {"Q4"})
    expect("A6 reviewer basis, advisor only before the operator's last message",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[ADVISOR, OPERATOR, TOOL_RESULT]), "deny", {"Q4"})
    expect("A7 a basis with too short a reason",
           run(q("Push?\nBasis: confirmation-gated - tag", PUSH_OPTIONS)), "deny", {"Q1"})

    # ARM B: allows
    expect("B1 the records question with a real basis", run(q(RECORDS_Q, RECORDS_OPTIONS)), "allow")
    expect("B2 a push-and-tag question", run(q(PUSH_Q, PUSH_OPTIONS)), "allow")
    expect("B3 reviewer basis after an advisor call (tool results do not reset it)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT]), "allow")
    expect("B4 the untagged question below T4", run(q(TONIGHT, TONIGHT_OPTIONS), tier="T3"), "allow")
    expect("B5 no anchor for the session", run(q(TONIGHT, TONIGHT_OPTIONS), tier=None), "allow")
    expect("B6 another tool", run({"command": "ls"}, tool_name="Bash"), "allow")

    # ARM C: fail-open on malformed input, logged
    rc, out, log = run({"questions": "not a list of objects"})
    ok = rc == 0 and out is None and any(e.get("event") == "pre-tool-use-error" for e in log)
    results.append((ok, f"C1 malformed input allowed and logged as pre-tool-use-error: rc={rc} out={out} "
                        f"events={[e.get('event') for e in log]}"))
    rc, out, log = run(q(TONIGHT, TONIGHT_OPTIONS))
    ok = any(e.get("event") == "pre-tool-use-question-gate" and e.get("decision") == "deny" for e in log)
    results.append((ok, "C2 a deny is logged as pre-tool-use-question-gate"))

    # ARM D: lockstep and wiring
    hook = load(HOOK, "qgate_hook")
    checker = load(CHECKER, "qgate_checker")
    results.append((hook.BASIS_CATEGORIES == checker.BASIS_CATEGORIES,
                    "D1 the hook and check-goal-block.py carry the same category set"))
    hooks_json = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    reg = [h for m in hooks_json.get("hooks", {}).get("PreToolUse", []) if m.get("matcher") == "AskUserQuestion"
           for h in m.get("hooks", []) if "pre-tool-use.py" in h.get("command", "")]
    results.append((len(reg) == 1, f"D2 hooks.json registers pre-tool-use.py for AskUserQuestion ({len(reg)})"))
    t4 = (ROOT / "methodology-content" / "T4.md").read_text(encoding="utf-8")
    results.append(("question gate" in t4 and "pre-tool-use.py" in t4, "D3 the T4 slice names the question gate"))
    with tempfile.TemporaryDirectory(prefix="qgate-g8-") as d:
        name = "demo-opening-prompt-2026-11-15.md"
        goal = (f"/goal Work the opening prompt in {name}: each step, with each command's output shown in this "
                "conversation. Ask me before any confirmation-gated action or scope change; stopping to ask me "
                "counts as done. Or stop after 40 turns.")
        base = f"# Prompt\n\n{{line}}\n\n## Goal\n\n```text\n{goal}\n```\n"
        bad = "4. Awaiting the operator, not to be started without the go: the public release for the import drop."
        good = ("4. Ask the operator before the GitHub push and the tag. Basis: confirmation-gated - a tag "
                "ships to every node.")
        fails = {}
        for label, line in (("bad", bad), ("good", good)):
            p = Path(d) / label
            p.mkdir()
            f = p / name
            f.write_text(base.format(line=line), encoding="utf-8")
            fails[label] = {c for c, s, _ in checker.check(f) if s == "FAIL"}
        results.append((fails["bad"] == {"G8"} and fails["good"] == set(),
                        f"D4 G8 fails the 2026-11-15 step-4 line and passes a based one ({fails})"))

    fails = 0
    for ok, msg in results:
        fails += 0 if ok else 1
        print(f"  [{'OK  ' if ok else 'FAIL'}] {msg}")
    print(f"\n{len(results) - fails} pass / {fails} fail")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
