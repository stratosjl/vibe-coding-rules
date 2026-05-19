#!/usr/bin/env python3
"""Heartbeat-hook regression tests for vc-roe.

Covers the v1.1.1 F-8 fix: PostToolUse transcript-grep behaviour. Builds
synthetic transcripts + anchor files and exercises post-tool-use.py
end-to-end via subprocess, asserting the LAST_HEARTBEAT field on disk
advances (or does not advance) according to spec.

Pure stdlib. Cleans up its /tmp artefacts on exit.

Usage:
    python3 test-heartbeat.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
HOOK = PLUGIN_ROOT / "hooks" / "post-tool-use.py"
STOP_HOOK = PLUGIN_ROOT / "hooks" / "stop.py"
ANCHOR_DIR = Path(tempfile.gettempdir())  # OBS-MET-AK: cross-runtime /tmp divergence on Windows


def write_anchor(session_id: str, t0: int, last_hb: int, tier: str = "T4",
                 last_ptu_tag: int = 0) -> Path:
    path = ANCHOR_DIR / f"claude-methodology-anchor-{session_id}"
    path.write_text(
        f"T0={t0}\nLAST_HEARTBEAT={last_hb}\nTIER={tier}\nLAST_PTU_TAG_SEC={last_ptu_tag}\n",
        encoding="utf-8",
    )
    return path


def read_anchor(session_id: str) -> dict[str, str]:
    path = ANCHOR_DIR / f"claude-methodology-anchor-{session_id}"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_transcript(path: Path, turns: list[dict]) -> None:
    """Write a synthetic JSONL transcript.

    Each turn dict supports several shapes:
    - {role: "assistant", text: "..."}
    - {role: "assistant", blocks: [...]}  (v1.1.3 OBS-50-01 fixture: lets
        a test write a tool_use-only assistant turn, an empty assistant
        turn, or any other content-block layout. When "blocks" is
        present it overrides the default text-block construction.)
    - {role: "user-prompt", text: "..."}
    - {role: "tool-result", text: "..."}
    - {role: "metadata", type: "attachment"|"last-prompt"|"ai-title"|
        "permission-mode"|"file-history-snapshot"|...}
        (v1.1.2 OBS-46-02 fixture: synthetic Claude Code metadata lines
        that carry only type=..., no role)
    The helper builds a minimally-realistic JSONL line for each.
    """
    lines: list[str] = []
    for turn in turns:
        role = turn["role"]
        if role == "assistant":
            if "blocks" in turn:
                content = turn["blocks"]
            else:
                content = [{"type": "text", "text": turn["text"]}]
            lines.append(json.dumps({"role": role, "content": content}))
        elif role == "user-prompt":
            content = turn["text"]
            lines.append(json.dumps({"role": "user", "content": content}))
        elif role == "tool-result":
            content = [{"type": "tool_result", "content": turn.get("text", "ok")}]
            lines.append(json.dumps({"role": "user", "content": content}))
        elif role == "metadata":
            meta_type = turn.get("type", "attachment")
            lines.append(json.dumps({"type": meta_type, "message": None}))
        else:
            content = turn.get("text", "")
            lines.append(json.dumps({"role": role, "content": content}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_post_tool_use(session_id: str, transcript_path: Path,
                      tool_name: str = "Bash") -> int:
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "tool_name": tool_name,
        "cwd": str(PLUGIN_ROOT),
    }
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode


def run_stop(session_id: str, transcript_path: Path) -> tuple[int, str]:
    """Run hooks/stop.py end-to-end; return (returncode, stdout-decoded)."""
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "cwd": str(PLUGIN_ROOT),
    }
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    proc = subprocess.run(
        [sys.executable, str(STOP_HOOK)],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _cleanup(session_id: str) -> None:
    p = ANCHOR_DIR / f"claude-methodology-anchor-{session_id}"
    if p.is_file():
        p.unlink()


def case_fresh_sentinel_advances(tmp: Path) -> tuple[bool, str]:
    """Fresh sentinel newer than LAST_HEARTBEAT_MIN advances the anchor."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        # T0 was 16 minutes ago; LAST_HEARTBEAT still at 0 (T+0m), so
        # LAST_HEARTBEAT_MIN = 0. Sentinel for T+15m is fresh.
        now = int(time.time())
        t0 = now - 16 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant", "text": "starting work"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant",
             "text": "session goal restated\nscope clean\n"
                     "no anomaly\nno side-questions\nno bg tasks\n"
                     "[heartbeat-fired:T+15m]"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "continuing work"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb >= now - 2:
            return True, f"OK (LAST_HEARTBEAT advanced to ~{last_hb})"
        return False, f"FAIL: LAST_HEARTBEAT={last_hb}, expected ~{now}"
    finally:
        _cleanup(sid)


def case_stale_sentinel_does_not_advance(tmp: Path) -> tuple[bool, str]:
    """Sentinel with minute <= LAST_HEARTBEAT_MIN must not advance."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        # T0 was 30 minutes ago; LAST_HEARTBEAT at T0+15m (LAST_HEARTBEAT_MIN
        # = 15). Sentinel for T+10m is stale (10 <= 15). Must not advance.
        now = int(time.time())
        t0 = now - 30 * 60
        last_hb_initial = t0 + 15 * 60
        write_anchor(sid, t0=t0, last_hb=last_hb_initial, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant",
             "text": "early heartbeat block\n[heartbeat-fired:T+10m]"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "more work, no fresh sentinel"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == last_hb_initial:
            return True, f"OK (LAST_HEARTBEAT unchanged at {last_hb})"
        return False, (f"FAIL: LAST_HEARTBEAT={last_hb}, expected "
                       f"unchanged at {last_hb_initial}")
    finally:
        _cleanup(sid)


def case_no_sentinel_no_change(tmp: Path) -> tuple[bool, str]:
    """Transcript with no sentinel must not advance LAST_HEARTBEAT."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 20 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant", "text": "all assistant text, no sentinel"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "still no sentinel"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == 0:
            return True, "OK (LAST_HEARTBEAT untouched)"
        return False, f"FAIL: LAST_HEARTBEAT={last_hb}, expected 0"
    finally:
        _cleanup(sid)


def case_multiturn_picks_max_sentinel(tmp: Path) -> tuple[bool, str]:
    """Multiple sentinels in the same agent loop: pick the max."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 20 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant", "text": "[heartbeat-fired:T+5m] earlier"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "[heartbeat-fired:T+19m] later"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "continuing"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb >= now - 2:
            return True, f"OK (advanced to ~{last_hb})"
        return False, f"FAIL: LAST_HEARTBEAT={last_hb}, expected ~{now}"
    finally:
        _cleanup(sid)


def case_tier_t1_no_op(tmp: Path) -> tuple[bool, str]:
    """T1 short-circuits to no-op even with a fresh sentinel."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 20 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T1")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant",
             "text": "fresh sentinel\n[heartbeat-fired:T+19m]"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == 0:
            return True, "OK (T1 short-circuit; no advance)"
        return False, f"FAIL: T1 advanced LAST_HEARTBEAT to {last_hb}"
    finally:
        _cleanup(sid)


def case_synthetic_metadata_lines_do_not_halt_walk(tmp: Path) -> tuple[bool, str]:
    """v1.1.2 OBS-46-02 regression: synthetic metadata must not halt walk.

    In real Claude Code transcripts, synthetic metadata line types
    (attachment, last-prompt, ai-title, permission-mode,
    file-history-snapshot) interleave between assistant text and the
    most-recent tool_result. The pre-v1.1.2 walk halted at any
    non-assistant / non-user-with-tool_result role, missing pre-tool
    sentinel emissions and producing repeated false OVERDUE alarms.
    Layout mirrors the s46 transcript fragment that surfaced the bug.
    """
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 16 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant",
             "text": "session goal restated\nscope clean\n"
                     "no anomaly\nno side-questions\nno bg tasks\n"
                     "[heartbeat-fired:T+15m]"},
            {"role": "tool-result", "text": "ok"},
            {"role": "metadata", "type": "attachment"},
            {"role": "metadata", "type": "last-prompt"},
            {"role": "metadata", "type": "ai-title"},
            {"role": "metadata", "type": "permission-mode"},
            {"role": "metadata", "type": "file-history-snapshot"},
            {"role": "assistant", "text": "more work, no fresh sentinel"},
            {"role": "tool-result", "text": "ok"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb >= now - 2:
            return True, f"OK (walk skipped past metadata; advanced to ~{last_hb})"
        return False, (f"FAIL: LAST_HEARTBEAT={last_hb}, expected ~{now}; "
                       f"walk halted at metadata line (regression of OBS-46-02)")
    finally:
        _cleanup(sid)


def case_user_prompt_boundary_halts_walk(tmp: Path) -> tuple[bool, str]:
    """v1.1.2 regression guard: real user-prompt boundary still halts walk.

    The new permissive walk-rule must continue to halt at a true user-
    prompt boundary (role="user" without tool_result content) so
    sentinels from previous agent loops do not leak into the current-
    loop scope. Setup: LAST_HEARTBEAT_MIN = 12; a stale T+10m sentinel
    in the current loop (must not advance via freshness gate); a
    T+20m sentinel in the PREVIOUS loop (must not be reached at all
    by the walk). If the walk leaks past the user-prompt boundary it
    will see T+20m and incorrectly advance.
    """
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 25 * 60
        last_hb_initial = t0 + 12 * 60
        write_anchor(sid, t0=t0, last_hb=last_hb_initial, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "earliest prompt"},
            {"role": "assistant",
             "text": "old turn, [heartbeat-fired:T+20m]"},
            {"role": "tool-result", "text": "ok"},
            {"role": "user-prompt", "text": "current prompt"},
            {"role": "assistant",
             "text": "current turn, [heartbeat-fired:T+10m]"},
            {"role": "tool-result", "text": "ok"},
        ])
        run_post_tool_use(sid, tx)
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == last_hb_initial:
            return True, ("OK (walk halted at user-prompt; T+20m from "
                          "prior loop unreached; T+10m stale)")
        return False, (f"FAIL: LAST_HEARTBEAT={last_hb}, expected unchanged "
                       f"at {last_hb_initial}; walk leaked past user-prompt "
                       f"boundary or freshness gate failed")
    finally:
        _cleanup(sid)


def case_silent_stop_after_tool_blocks_returns_block(tmp: Path) -> tuple[bool, str]:
    """v1.1.3 OBS-50-01: tool_use-only most-recent agent loop -> Stop blocks.

    Reproduces the [INT-A] M0 build 2026-05-09 silent-stop signature:
    the assistant emitted a tool_use block and then ended its turn after
    the tool_result without producing any text. Walking back from the
    end of the transcript yields blocks=[tool_use], so the silent-stop
    guard fires and stop.py prints {"decision":"block", ...}.
    """
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 5 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "install vitest"},
            {"role": "assistant",
             "blocks": [{"type": "tool_use", "id": "toolu_01",
                         "name": "Bash",
                         "input": {"command": "pnpm add -D vitest"}}]},
            {"role": "tool-result", "text": "ok"},
        ])
        rc, stdout = run_stop(sid, tx)
        if rc != 0:
            return False, f"FAIL: stop.py rc={rc}, stdout={stdout!r}"
        try:
            payload = json.loads(stdout)
        except Exception as exc:
            return False, f"FAIL: stdout not JSON ({exc}): {stdout!r}"
        if payload.get("decision") == "block":
            return True, "OK (silent-stop blocked; decision=block emitted)"
        return False, f"FAIL: decision={payload.get('decision')!r}, expected 'block'"
    finally:
        _cleanup(sid)


def case_silent_stop_with_text_does_not_block(tmp: Path) -> tuple[bool, str]:
    """v1.1.3 OBS-50-01 negative-control: text in same loop suppresses block.

    Same agent-loop shape as the silent-stop case but the assistant turn
    carries a non-empty text block alongside the tool_use. The walk
    yields has_text=True and the silent-stop guard does NOT fire. Stop
    falls through to the existing sentinel-grep path; with no
    [heartbeat-fired] sentinel it logs sentinel_found:false and returns
    0 with empty stdout (no decision emitted).
    """
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 5 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "install vitest"},
            {"role": "assistant",
             "blocks": [
                 {"type": "text", "text": "running pnpm add -D vitest"},
                 {"type": "tool_use", "id": "toolu_02",
                  "name": "Bash",
                  "input": {"command": "pnpm add -D vitest"}},
             ]},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "vitest installed"},
        ])
        rc, stdout = run_stop(sid, tx)
        if rc != 0:
            return False, f"FAIL: stop.py rc={rc}, stdout={stdout!r}"
        if stdout.strip():
            return False, (f"FAIL: stdout non-empty: {stdout!r}; "
                           f"silent-stop guard should not have fired")
        return True, "OK (text present; silent-stop guard skipped)"
    finally:
        _cleanup(sid)


def case_silent_stop_no_tool_use_does_not_block(tmp: Path) -> tuple[bool, str]:
    """v1.1.3 OBS-50-01 edge case: zero tool_use AND zero text -> no block.

    Pathological assistant turn with an empty content list. The walk
    yields has_text=False AND has_tool_use=False, so the silent-stop
    guard's `has_tool_use and not has_text` condition is False. Stop
    falls through to the sentinel-grep path; last_assistant_text returns
    None and Stop returns 0 with empty stdout. This guards against a
    false positive on edge cases that may never occur in practice.
    """
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        t0 = now - 5 * 60
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "go"},
            {"role": "assistant", "blocks": []},
        ])
        rc, stdout = run_stop(sid, tx)
        if rc != 0:
            return False, f"FAIL: stop.py rc={rc}, stdout={stdout!r}"
        if stdout.strip():
            return False, (f"FAIL: stdout non-empty: {stdout!r}; "
                           f"empty-content turn must not trip silent-stop")
        return True, "OK (no tool_use AND no text; silent-stop guard skipped)"
    finally:
        _cleanup(sid)


def main() -> int:
    cases = [
        ("fresh sentinel advances", case_fresh_sentinel_advances),
        ("stale sentinel no-op", case_stale_sentinel_does_not_advance),
        ("no sentinel no-op", case_no_sentinel_no_change),
        ("multi-sentinel picks max", case_multiturn_picks_max_sentinel),
        ("T1 short-circuit", case_tier_t1_no_op),
        ("metadata lines no-halt (v1.1.2 OBS-46-02)",
         case_synthetic_metadata_lines_do_not_halt_walk),
        ("user-prompt halts (v1.1.2 regression guard)",
         case_user_prompt_boundary_halts_walk),
        ("silent-stop after tool blocks (v1.1.3 OBS-50-01)",
         case_silent_stop_after_tool_blocks_returns_block),
        ("silent-stop with text does not block (v1.1.3 OBS-50-01)",
         case_silent_stop_with_text_does_not_block),
        ("silent-stop no tool_use does not block (v1.1.3 OBS-50-01)",
         case_silent_stop_no_tool_use_does_not_block),
    ]
    parent = Path(tempfile.mkdtemp(prefix="vc-roe-heartbeat-tests-"))
    try:
        print(f"== Heartbeat hook regression (fixtures: {parent}) ==\n")
        misses = 0
        for label, fn in cases:
            ok, detail = fn(parent)
            status = "OK  " if ok else "FAIL"
            print(f"  [{status}] {label} — {detail}")
            if not ok:
                misses += 1
        print()
        print(f"Misses: {misses} / {len(cases)}")
        return 0 if misses == 0 else 1
    finally:
        import shutil
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
