#!/usr/bin/env python3
"""PreToolUse question gate for T4 sessions (added v1.35.0).

At T4, Goal runs rule 2 routes technical questions to a stronger reviewer
model and lets a question reach the operator only on a named exception. That
rule was text alone, and text did not hold: measured 2026-11-15, a session
asked the operator "start the release now, or leave it for a later session?"
after the operator had already made the substantive choice. The question was
timing, which the rule delegates; the session had classified it by subject
("new code, not on tonight's agenda") and a reviewer then repeated that
framing back as an instruction to ask.

This hook sits between a classification and the dialog. It fires on
AskUserQuestion only, in a session whose anchor records TIER=T4, and denies
the call (Claude Code feeds the reason back to the session) when any of these
holds for any question:

  Q1  the question text carries no line `Basis: <category> - <what differs>`
      whose category is one of BASIS_CATEGORIES, with a reason of at least
      MIN_REASON_CHARS characters;
  Q2  an option label carries "(Recommended)": inside the delegated remit a
      recommendation is the answer, so a recommended option means the
      question should not be asked;
  Q3  every option label is a timing choice (now, later, next session,
      defer...): timing is delegated, so a now-or-later question is never the
      operator's, whatever basis it claims. Since v1.35.2 the reason says the
      denial settles nothing about the action itself: an approval that is not
      on record is asked for with options that differ in the decision, not in
      time (a valid confirmation-gated basis with "Push tonight" / "Push
      tomorrow" was otherwise told it was "never the operator's question");
  Q4  the basis is a reviewer one (REVIEWER_CATEGORIES) and the transcript
      shows no `advisor` call since the last operator message.

What it cannot see, stated so nobody relies on it: a question asked in plain
prose rather than through AskUserQuestion; whether the stated category is
TRUE (it is self-attested); and what the reviewer said (its result is
encrypted in the transcript). A deny-by-default on its own failure would
close the channel every confirmation gate depends on, so on any internal
error the hook ALLOWS and logs `pre-tool-use-error`.

Pure stdlib. Reads the hook payload on stdin; prints a deny decision as JSON
on stdout, or nothing to allow.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")


def _resolve_plugin_version() -> str:
    """This bundle's version from its own manifest (I-22); "unknown" on failure."""
    try:
        manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        with open(manifest, encoding="utf-8") as f:
            return str(json.load(f).get("version", "")).strip() or "unknown"
    except Exception:
        return "unknown"


ROUTINE_VERSION = _resolve_plugin_version()
ANCHOR_DIR = Path(tempfile.gettempdir())  # OBS-MET-AK: cross-runtime /tmp divergence on Windows
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"
LOG_MAX_BYTES = 150_000
GATED_TIERS = {"T4"}
MIN_REASON_CHARS = 15

# The exceptions of T4 Goal runs rule 2, as closed slugs. bin/check-goal-block.py
# carries the same set; test-question-gate.py asserts the two are equal.
BASIS_CATEGORIES = {
    # a technical-looking choice whose answer has one of these effects
    "licence", "cost", "data-egress", "ux", "deliverable",
    # categories the operator kept
    "business", "compliance", "spend", "outside-wording", "signature",
    # always the operator's
    "scope", "gui", "end-user-function", "final-deliverables",
    # the clause's own stops
    "gated-output-mismatch", "record-waits-on-operator", "confirmation-gated",
    # after the reviewer was consulted
    "reviewer-only-operator-knows", "reviewer-conflict",
}
REVIEWER_CATEGORIES = {"reviewer-only-operator-knows", "reviewer-conflict"}

BASIS_RE = re.compile(r"^\s*\**\s*Basis\s*\**\s*:\s*\**\s*([A-Za-z-]+)\s*\**\s*[-–—:]+\s*(.+?)\s*$",
                      re.IGNORECASE | re.MULTILINE)
RECOMMENDED_RE = re.compile(r"\(\s*recommended\s*\)", re.IGNORECASE)
TIMING_RE = re.compile(
    r"\b(now|later|tonight|tomorrow|today|next (session|sitting|time|week)|defer\w*|postpone\w*|"
    r"wait\w*|leave (it )?for|start (it )?now|for now|in a later|another (session|day))\b",
    re.IGNORECASE,
)


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if LOG_PATH.stat().st_size >= LOG_MAX_BYTES:
                import os
                os.replace(LOG_PATH, LOG_PATH.with_name(LOG_PATH.name + ".1"))
        except OSError:
            pass
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_tier(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TIER="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _is_operator_message(entry: dict[str, Any]) -> bool:
    """A user-role transcript entry that carries text, not only tool results."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def advisor_since_last_operator_message(transcript_path: str) -> Optional[bool]:
    """True/False from the transcript; None when it cannot be read."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, TypeError, ValueError):
        return None
    seen = False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if _is_operator_message(entry):
            seen = False
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in ("server_tool_use", "tool_use") \
                        and b.get("name") == "advisor":
                    seen = True
    return seen


def problems(tool_input: Any, transcript_path: str) -> list[str]:
    out: list[str] = []
    questions = (tool_input or {}).get("questions") or []
    for n, q in enumerate(questions, 1):
        text = str(q.get("question", ""))
        labels = [str(o.get("label", "")) for o in (q.get("options") or [])]
        m = BASIS_RE.search(text)
        cat = m.group(1).lower() if m else None
        valid = bool(m) and cat in BASIS_CATEGORIES and len(m.group(2)) >= MIN_REASON_CHARS
        if not valid:
            out.append(f"Q1 question {n}: no valid 'Basis: <category> - <what differs between the options>' line"
                       + (f" (category '{cat}' is not on the list)" if m and cat not in BASIS_CATEGORIES else ""))
        if any(RECOMMENDED_RE.search(lbl) for lbl in labels):
            if valid:
                # v1.35.1: a question with a valid basis stays the operator's; the label
                # goes, the question does not (a confirmation-gated question told to
                # "decide it" would be steered past its own gate).
                out.append(f"Q2 question {n}: an option is marked (Recommended); this question has a valid "
                           "basis, so it stays the operator's: remove the label and ask again")
            else:
                out.append(f"Q2 question {n}: an option is marked (Recommended) and the question names no "
                           "valid basis; inside the delegated remit the recommendation is the answer")
        if labels and all(TIMING_RE.search(lbl) for lbl in labels):
            # v1.35.2: the time is the session's; the action is not. A confirmation-gated
            # question phrased as a choice of time must not read as "decide and act".
            out.append(f"Q3 question {n}: the options differ only in timing ({' / '.join(labels)}). The time "
                       "is the session's to choose, and this denial settles nothing about whether the action "
                       "may be taken: if it needs an approval that is not on record, ask for the approval with "
                       "options that differ in the decision and carry no time words (for example 'Push and "
                       "tag' / 'Do not push')")
        if cat in REVIEWER_CATEGORIES:
            seen = advisor_since_last_operator_message(transcript_path)
            if seen is not True:
                out.append(f"Q4 question {n}: basis '{cat}' needs an advisor call since the operator's last "
                           "message, and the transcript shows none" + (" (transcript unreadable)" if seen is None else ""))
    return out


def deny_reason(found: list[str]) -> str:
    cats = ", ".join(sorted(BASIS_CATEGORIES))
    return (
        "vc-roe T4 question gate: this question may not go to the operator as written. A denial is never an "
        "approval: do not take a confirmation-gated action, or the action this question asked about, because "
        "of it.\n"
        + "\n".join(f"- {p}" for p in found)
        + "\nA question reaches the operator only on a named exception (T4 Goal runs rule 2). Allowed "
        f"categories: {cats}. A reviewer basis also needs an advisor call first. A technical question goes "
        "to the advisor. Only a choice inside the delegated remit (engineering, tooling, ordering, timing) is "
        "decided by the session: state the decision in one line with its reason, and act; that never covers a "
        "confirmation-gated action with no approval on record. A confirmation-gated action, or any other real "
        "exception, is asked again with 'Basis: <category> - <what differs between the options>' in the "
        "question text and options that differ in the decision, not in time."
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("tool_name") != "AskUserQuestion":
            return 0
        tier = read_tier(str(payload.get("session_id") or ""))
        if tier not in GATED_TIERS:
            return 0
        found = problems(payload.get("tool_input"), str(payload.get("transcript_path") or ""))
        append_log({"ts": int(time.time()), "event": "pre-tool-use-question-gate",
                    "version": ROUTINE_VERSION, "tier": tier,
                    "decision": "deny" if found else "allow", "problems": [p[:2] for p in found]})
        if found:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason(found),
            }}))
        return 0
    except Exception as e:  # fail open: see the module docstring
        append_log({"ts": int(time.time()), "event": "pre-tool-use-error",
                    "version": ROUTINE_VERSION, "error": f"{type(e).__name__}: {e}"[:300]})
        return 0


if __name__ == "__main__":
    sys.exit(main())
