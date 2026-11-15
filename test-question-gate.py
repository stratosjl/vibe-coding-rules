#!/usr/bin/env python3
"""Regression tests for the T4 question gate (hooks/pre-tool-use.py, v1.35.0).

The gate exists because of one measured failure, 2026-11-15: a T4 session
asked the operator "start the release now, or leave it for a later session?"
after the operator had made the substantive choice. These tests run the real
hook in a subprocess, with a temporary anchor, home and transcript, and judge
each case by the parsed JSON decision and the exit code.

  ARM A  denials: that question untagged; the same question tagged "scope";
         a confirmation-gated question with timing-only labels (v1.35.2:
         the reason leaves the action unsettled, and its re-ask passes);
         a "(Recommended)" option; an unknown category; a reviewer basis
         with no advisor call, or one only before the operator's last message
         (v1.35.3: a message typed mid-turn, stored as a queued_command, counts);
         a short reason under a valid category named as such (v1.35.4)
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
# v1.35.3 (VCR-I-34): a message the operator types while a turn runs is stored as an
# `attachment` entry of type `queued_command` with `origin.kind: human`; a cross-session
# message and a task notification take the same entry type with another origin kind.
# Shapes as recorded by Claude Code on deskmeet, 2026-11-15 (bodies shortened).
QUEUED_HUMAN = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "stop, wait for me",
                                                      "commandMode": "prompt", "origin": {"kind": "human"}}}
QUEUED_PEER = {"type": "attachment", "attachment": {"type": "queued_command", "isMeta": True,
                                                     "prompt": "<cross-session-message from=\"uds:x\">hi</cross-session-message>",
                                                     "origin": {"kind": "peer", "name": "peer-1"}}}
QUEUED_TASK = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "<task-notification/>",
                                                     "origin": {"kind": "task-notification", "producer": "session-task"}}}

# v1.35.5 (VCR-I-38): an operator message can carry only an image (a pasted screenshot);
# shapes as recorded on deskmeet 2026-11-15: top-level image blocks in the message or the
# queued prompt, while a tool's image result is nested inside its tool_result block.
IMAGE_BLOCK = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
QUEUED_HUMAN_IMAGE = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": [IMAGE_BLOCK],
                                                            "commandMode": "prompt", "origin": {"kind": "human"}}}
OPERATOR_IMAGE = {"type": "user", "message": {"role": "user", "content": [IMAGE_BLOCK]}}
TOOL_RESULT_IMAGE = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t2", "content": [IMAGE_BLOCK]}]}}

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
    # v1.35.5 (VCR-I-41): the unknown-category cause text is asserted, not only the deny.
    rc, out, _ = run(q("Which order?\nBasis: engineering - two equally good orders", RECORDS_OPTIONS))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    q1 = next((ln for ln in reason.splitlines() if ln.startswith("- Q1 ")), "")
    results.append((decision(out) == "deny" and "(category 'engineering' is not on the list)" in q1
                    and "characters" not in q1,
                    f"A4b an unknown category is named as not on the list: {q1!r}"))
    expect("A5 reviewer basis, no advisor call at all",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR]), "deny", {"Q4"})
    expect("A6 reviewer basis, advisor only before the operator's last message",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[ADVISOR, OPERATOR, TOOL_RESULT]), "deny", {"Q4"})
    rc, out, _ = run(q(PUSH_Q, [dict(PUSH_OPTIONS[0], label="Push and tag v1.35.0 (Recommended)"), PUSH_OPTIONS[1]]))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    results.append(("remove the label and ask again" in reason and "the recommendation is the answer" not in reason,
                    "A3b a confirmation-gated question with (Recommended) is told to remove the label, not to decide"))
    rc, out, _ = run(q("Which order?", [{"label": "A first (Recommended)", "description": "x"},
                                        {"label": "B first", "description": "y"}]))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    results.append(("the recommendation is the answer" in reason, "A3c no basis plus (Recommended): the remit text"))
    denials = [run(q(TONIGHT, TONIGHT_OPTIONS)), run(q(PUSH_Q.split("\n")[0], PUSH_OPTIONS)),
               run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR])]
    texts = [(o or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "") for _, o, _ in denials]
    results.append((all("A denial is never an approval" in x for x in texts) and all(texts),
                    "A8 every deny reason says a denial is never an approval (incl. a push question without a basis)"))
    # v1.35.2 (VCR-I-32): a valid confirmation-gated basis with timing-only labels is still denied,
    # but the reason must not call the question "never the operator's", and the re-ask it
    # prescribes must itself pass the gate.
    rc, out, _ = run(q(PUSH_Q, [{"label": "Push tonight", "description": "a"},
                                {"label": "Push tomorrow", "description": "b"}]))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    results.append((decision(out) == "deny" and "Q3" in codes(out)
                    and "settles nothing about whether the action may be taken" in reason
                    and "never the operator's question" not in reason
                    and "never covers a confirmation-gated action" in reason,
                    "A2b a confirmation-gated basis with timing-only labels: denied, and told the action is not "
                    "settled, not that it is never the operator's"))
    ok_example = "'Push and tag' / 'Do not push'" in reason
    rc2, out2, _ = run(q(PUSH_Q, [{"label": "Push and tag", "description": "a"},
                                  {"label": "Do not push", "description": "b"}]))
    results.append((ok_example and decision(out2) == "allow",
                    f"A2c the re-ask the reason prescribes passes the gate (example in reason: {ok_example}, "
                    f"decision: {decision(out2)})"))
    expect("A9 reviewer basis, advisor call, then an operator message typed mid-turn (queued_command, human)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT, QUEUED_HUMAN]),
           "deny", {"Q4"})
    expect("A9b reviewer basis, advisor call, then a mid-turn operator message that is only an image",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT, QUEUED_HUMAN_IMAGE]),
           "deny", {"Q4"})
    expect("A9c reviewer basis, advisor call, then an operator message that is only an image",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT, OPERATOR_IMAGE]),
           "deny", {"Q4"})
    expect("A7 a basis with too short a reason",
           run(q("Push?\nBasis: confirmation-gated - tag", PUSH_OPTIONS)), "deny", {"Q1"})
    # v1.35.4 (VCR-I-36): a valid category with a short reason is named as such, so the
    # session can tell it from a missing line; a missing line keeps the generic text.
    rc, out, _ = run(q("Push?\nBasis: confirmation-gated - ships a tag", PUSH_OPTIONS))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    q1 = next((ln for ln in reason.splitlines() if ln.startswith("- Q1 ")), "")
    results.append((decision(out) == "deny" and "reason 'ships a tag' is 11 characters" in q1
                    and "at least 15" in q1,
                    f"A7b a valid category with a short reason names the reason and the minimum: {q1!r}"))
    rc, out, _ = run(q("Push?\nBasis: confirmation-gated - ships a tag to every node at the nightly", PUSH_OPTIONS))
    results.append((decision(out) == "allow", f"A7b re-ask with the reason lengthened is allowed: {decision(out)}"))
    rc, out, _ = run(q("Push?\nBasis: confirmation-gated - x", PUSH_OPTIONS))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    q1 = next((ln for ln in reason.splitlines() if ln.startswith("- Q1 ")), "")
    results.append((decision(out) == "deny" and "reason 'x' is 1 character;" in q1,
                    f"A7d a one-character reason is counted in the singular: {q1!r}"))
    rc, out, _ = run(q("Push the branch?", PUSH_OPTIONS))
    reason = (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    q1 = next((ln for ln in reason.splitlines() if ln.startswith("- Q1 ")), "")
    results.append((decision(out) == "deny" and "no valid 'Basis:" in q1 and "characters" not in q1,
                    f"A7c no basis line keeps the generic Q1 text with no length claim: {q1!r}"))

    # ARM B: allows
    expect("B1 the records question with a real basis", run(q(RECORDS_Q, RECORDS_OPTIONS)), "allow")
    expect("B2 a push-and-tag question", run(q(PUSH_Q, PUSH_OPTIONS)), "allow")
    expect("B3 reviewer basis after an advisor call (tool results do not reset it)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT]), "allow")
    expect("B7 reviewer basis, advisor call, then a cross-session message (queued_command, peer)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT, QUEUED_PEER]), "allow")
    expect("B7b the same cross-session message without isMeta (the origin kind alone decides)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT,
               {"type": "attachment", "attachment": {k: v for k, v in QUEUED_PEER["attachment"].items()
                                                     if k != "isMeta"}}]), "allow")
    expect("B8 reviewer basis, advisor call, then a task notification (queued_command)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT, QUEUED_TASK]), "allow")
    expect("B10 reviewer basis, advisor call, then a tool result carrying an image (not an operator message)",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, ADVISOR, TOOL_RESULT_IMAGE]), "allow")
    expect("B9 reviewer basis, operator message typed mid-turn, then an advisor call",
           run(q(REVIEWER_Q, REVIEWER_OPTIONS), transcript=[OPERATOR, QUEUED_HUMAN, ADVISOR, TOOL_RESULT]), "allow")
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
        # v1.35.5 (VCR-I-39): a basis whose category is not on the list fails G8 too.
        unknown = ("4. Ask the operator before the GitHub push and the tag. Basis: timing - after the "
                   "nightly has run on every node.")
        fails = {}
        for label, line in (("bad", bad), ("good", good), ("unknown", unknown)):
            p = Path(d) / label
            p.mkdir()
            f = p / name
            f.write_text(base.format(line=line), encoding="utf-8")
            fails[label] = {c for c, s, _ in checker.check(f) if s == "FAIL"}
        results.append((fails["bad"] == {"G8"} and fails["good"] == set(),
                        f"D4 G8 fails the 2026-11-15 step-4 line and passes a based one ({fails})"))
        results.append((fails["unknown"] == {"G8"},
                        f"D4b G8 fails a waiting line whose basis category is not on the list ({fails['unknown']})"))

    fails = 0
    for ok, msg in results:
        fails += 0 if ok else 1
        print(f"  [{'OK  ' if ok else 'FAIL'}] {msg}")
    print(f"\n{len(results) - fails} pass / {fails} fail")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
