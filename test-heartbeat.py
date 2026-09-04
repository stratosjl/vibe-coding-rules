#!/usr/bin/env python3
"""Heartbeat-hook regression tests for vc-roe.

Covers the v1.1.1 F-8 fix: PostToolUse transcript-grep behaviour. Builds
synthetic transcripts + anchor files and exercises post-tool-use.py
end-to-end via subprocess, asserting the LAST_HEARTBEAT field on disk
advances (or does not advance) according to spec.

Pure stdlib. Runs in a private anchor namespace: main() redirects
TMPDIR/TEMP/TMP and HOME/USERPROFILE into a throwaway directory before any
case runs, so the hooks it spawns cannot touch a live session's anchors,
session marker, hook log or writer claim. Everything it writes goes with
that directory on exit, and a leak check asserts so.

Usage:
    python3 test-heartbeat.py
"""

from __future__ import annotations

import json
import os
import re
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


def isolate_anchor_namespace(parent: Path) -> Path:
    """Move this run's anchor namespace into a private dir under `parent`.

    F043. Every hook resolves its own ANCHOR_DIR from tempfile.gettempdir()
    and none of them honours VC_ROE_ANCHOR_DIR, so an unisolated run writes
    into the very temp namespace live sessions keep their anchors in: the
    transient per-case anchors widen the newest-anchor resolution race, and
    run_ups drives user-prompt-submit.py, which writes a
    claude-methodology-current-session-<cwd> marker against the REAL
    checkout path and so overwrites a live session's Layer 1 pointer.

    The hooks are spawned with os.environ.copy(), so redirecting TMPDIR,
    TEMP and TMP here is what the children resolve gettempdir() from.
    HOME and USERPROFILE move with them because the hooks also append to
    ~/.claude/methodology-hook.log and read and write the writer-claim
    files under ~/.claude/projects/<cwd>, which are production state for
    this same repository path. Returns the private namespace dir so the
    caller can assert nothing is left in it.
    """
    global ANCHOR_DIR
    anchors = parent / "anchor-namespace"
    anchors.mkdir(parents=True, exist_ok=True)
    home = parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    for var in ("TMPDIR", "TEMP", "TMP"):
        os.environ[var] = str(anchors)
    for var in ("HOME", "USERPROFILE"):
        os.environ[var] = str(home)
    # gettempdir() caches into tempfile.tempdir on first call, so the parent
    # process needs telling directly; the children read the env vars above.
    tempfile.tempdir = str(anchors)
    ANCHOR_DIR = anchors
    return anchors


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


def _marker_path() -> Path:
    """The Layer 1 session marker user-prompt-submit.py writes for our cwd.

    Mirrors write_session_marker in hooks/user-prompt-submit.py: the name is
    keyed by the resolved cwd, and run_ups passes PLUGIN_ROOT as the cwd.
    """
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(PLUGIN_ROOT.resolve()))
    return ANCHOR_DIR / f"claude-methodology-current-session-{cwd_dashed}"


def _cleanup(session_id: str) -> None:
    p = ANCHOR_DIR / f"claude-methodology-anchor-{session_id}"
    if p.is_file():
        p.unlink()
    # F043: the run_ups cases make user-prompt-submit.py write a session
    # marker keyed by cwd rather than by session id. Unlinking only the
    # anchor left that marker behind, pointing at a session that no longer
    # exists. It is one file per run, not one per case, so the unlink is
    # guarded rather than unconditional.
    marker = _marker_path()
    if marker.is_file():
        marker.unlink()


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
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
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
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant",
             "text": "early heartbeat block\n[heartbeat-fired:T+10m]"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "more work, no fresh sentinel"},
        ])
        # F044 witness: the same transcript against LAST_HEARTBEAT_MIN = 5
        # makes the T+10m sentinel fresh, so the hook MUST advance. Without
        # it the assertion below is satisfied by a hook that never ran.
        write_anchor(sid, t0=t0, last_hb=t0 + 5 * 60, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: witness run: post-tool-use.py exited {rc}"
        witness_hb = int(read_anchor(sid).get("LAST_HEARTBEAT", "0"))
        if witness_hb < now - 2:
            return False, (f"FAIL: witness did not advance "
                           f"(LAST_HEARTBEAT={witness_hb}, expected ~{now}); "
                           f"the no-advance assertion below would be vacuous")
        write_anchor(sid, t0=t0, last_hb=last_hb_initial, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == last_hb_initial:
            return True, (f"OK (witness advanced to ~{witness_hb}; "
                          f"LAST_HEARTBEAT unchanged at {last_hb})")
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
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant", "text": "all assistant text, no sentinel"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "still no sentinel"},
        ])
        # F044 witness: the same shape WITH a sentinel MUST advance, so the
        # untouched assertion below cannot be satisfied by a dead hook.
        tx_witness = tmp / f"transcript-{sid}-witness.jsonl"
        write_transcript(tx_witness, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant", "text": "all assistant text, no sentinel"},
            {"role": "tool-result", "text": "ok"},
            {"role": "assistant", "text": "still working [heartbeat-fired:T+15m]"},
        ])
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        rc = run_post_tool_use(sid, tx_witness)
        if rc != 0:
            return False, f"FAIL: witness run: post-tool-use.py exited {rc}"
        witness_hb = int(read_anchor(sid).get("LAST_HEARTBEAT", "0"))
        if witness_hb < now - 2:
            return False, (f"FAIL: witness did not advance "
                           f"(LAST_HEARTBEAT={witness_hb}, expected ~{now}); "
                           f"the untouched assertion below would be vacuous")
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == 0:
            return True, f"OK (witness advanced to ~{witness_hb}; LAST_HEARTBEAT untouched)"
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
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
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
        tx = tmp / f"transcript-{sid}.jsonl"
        write_transcript(tx, [
            {"role": "user-prompt", "text": "open"},
            {"role": "assistant",
             "text": "fresh sentinel\n[heartbeat-fired:T+19m]"},
        ])
        # F044 witness: the identical fixture at T4 MUST advance, so the
        # no-advance assertion below is attributable to the tier gate and
        # not to a hook that did nothing.
        write_anchor(sid, t0=t0, last_hb=0, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: witness run: post-tool-use.py exited {rc}"
        witness_hb = int(read_anchor(sid).get("LAST_HEARTBEAT", "0"))
        if witness_hb < now - 2:
            return False, (f"FAIL: T4 witness did not advance "
                           f"(LAST_HEARTBEAT={witness_hb}, expected ~{now}); "
                           f"the T1 assertion below would be vacuous")
        write_anchor(sid, t0=t0, last_hb=0, tier="T1")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == 0:
            return True, (f"OK (T4 witness advanced to ~{witness_hb}; "
                          f"T1 short-circuit, no advance)")
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
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
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
        # F044 witness: the same transcript against LAST_HEARTBEAT_MIN = 5
        # makes the CURRENT-loop T+10m sentinel fresh, so the hook MUST
        # advance. That proves the walk reaches the current loop, which is
        # what makes the unchanged result below attributable to the boundary
        # plus the freshness gate rather than to a hook that never ran.
        write_anchor(sid, t0=t0, last_hb=t0 + 5 * 60, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: witness run: post-tool-use.py exited {rc}"
        witness_hb = int(read_anchor(sid).get("LAST_HEARTBEAT", "0"))
        if witness_hb < now - 2:
            return False, (f"FAIL: witness did not advance "
                           f"(LAST_HEARTBEAT={witness_hb}, expected ~{now}); "
                           f"the unchanged assertion below would be vacuous")
        write_anchor(sid, t0=t0, last_hb=last_hb_initial, tier="T4")
        rc = run_post_tool_use(sid, tx)
        if rc != 0:
            return False, f"FAIL: post-tool-use.py exited {rc}"
        anchor = read_anchor(sid)
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        if last_hb == last_hb_initial:
            return True, (f"OK (witness advanced to ~{witness_hb}; walk halted "
                          f"at user-prompt; T+20m from prior loop unreached; "
                          f"T+10m stale)")
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


def case_silent_stop_with_thinking_does_not_block(tmp: Path) -> tuple[bool, str]:
    """v1.12.1 OBS-vcroe-thinking-block-replay-01: thinking present -> no block.

    Extended-thinking turns carry a thinking block before the tool_use.
    Emitting {"decision":"block"} on that shape forces Claude Code to
    replay the finalized assistant message; the thinking blocks lose
    byte-identity with the original signed response and the API rejects
    the replay with 400 "thinking blocks ... cannot be modified". The
    has_thinking guard stands the blocker down: the walk yields
    has_tool_use=True, has_text=False, has_thinking=True, so no decision
    is emitted. Stop falls through to the sentinel-grep path and returns
    0 with empty stdout.
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
                 {"type": "thinking",
                  "thinking": "I should install vitest before continuing",
                  "signature": "sig-abc"},
                 {"type": "tool_use", "id": "toolu_03",
                  "name": "Bash",
                  "input": {"command": "pnpm add -D vitest"}},
             ]},
            {"role": "tool-result", "text": "ok"},
        ])
        rc, stdout = run_stop(sid, tx)
        if rc != 0:
            return False, f"FAIL: stop.py rc={rc}, stdout={stdout!r}"
        if stdout.strip():
            return False, (f"FAIL: stdout non-empty: {stdout!r}; thinking "
                           f"block must suppress the silent-stop blocker")
        return True, "OK (thinking present; silent-stop guard stood down)"
    finally:
        _cleanup(sid)


def run_stop_logged(session_id: str, transcript_path: Path, home: Path,
                    extra: dict | None = None) -> tuple[int, list[dict]]:
    """Run stop.py with HOME pointed at a temp dir; return (rc, this session's log rows).

    v1.26.0: the hook log lives under the home directory, so a temp HOME keeps
    the test off the operator's real log and lets it read back what was written.
    """
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "cwd": str(PLUGIN_ROOT),
    }
    event.update(extra or {})
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    proc = subprocess.run(
        [sys.executable, str(STOP_HOOK)],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    rows: list[dict] = []
    log = home / ".claude" / "methodology-hook.log"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("hook") == "stop" and row.get("session_id") == session_id:
                rows.append(row)
    return proc.returncode, rows


def _stop_hook_active_case(tmp: Path, extra: dict, expect: object,
                           silent_stop: bool = False,
                           text: str = "Done with this step.") -> tuple[bool, str]:
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    home = tmp / f"home-{sid}"
    home.mkdir()
    try:
        now = int(time.time())
        write_anchor(sid, t0=now - 5 * 60, last_hb=0, tier="T4")
        tx = tmp / f"transcript-{sid}.jsonl"
        if silent_stop:
            turns = [
                {"role": "user-prompt", "text": "go"},
                {"role": "assistant", "blocks": [
                    {"type": "tool_use", "id": "toolu_9", "name": "Bash",
                     "input": {"command": "true"}}]},
                {"role": "tool-result", "text": "ok"},
            ]
        else:
            turns = [
                {"role": "user-prompt", "text": "go"},
                {"role": "assistant", "text": text},
            ]
        write_transcript(tx, turns)
        rc, rows = run_stop_logged(sid, tx, home, extra)
        if rc != 0:
            return False, f"FAIL: stop.py rc={rc}"
        if not rows:
            return False, "FAIL: no stop log row written for this session"
        row = rows[-1]
        if "stop_hook_active" not in row:
            return False, f"FAIL: log row has no stop_hook_active key: {row}"
        if row["stop_hook_active"] != expect:
            return False, f"FAIL: stop_hook_active={row['stop_hook_active']!r}, expected {expect!r}"
        return True, f"OK (logged stop_hook_active={expect!r})"
    finally:
        _cleanup(sid)


def case_stop_logs_stop_hook_active_true(tmp: Path) -> tuple[bool, str]:
    """v1.26.0: a continuation Claude Code marks with stop_hook_active=true is recorded as true."""
    return _stop_hook_active_case(tmp, {"stop_hook_active": True}, True)


def case_stop_logs_stop_hook_active_absent(tmp: Path) -> tuple[bool, str]:
    """v1.26.0: an event without the field is recorded as null, not as false."""
    return _stop_hook_active_case(tmp, {}, None)


def case_stop_logs_stop_hook_active_on_block(tmp: Path) -> tuple[bool, str]:
    """v1.26.0: the silent-stop block row carries the field too."""
    return _stop_hook_active_case(tmp, {"stop_hook_active": False}, False, silent_stop=True)


def case_stop_logs_stop_hook_active_on_sentinel(tmp: Path) -> tuple[bool, str]:
    """v1.26.0: the sentinel-found row (heartbeat advanced) carries the field too."""
    return _stop_hook_active_case(tmp, {"stop_hook_active": True}, True,
                                  text="Status block.\n[heartbeat-fired:T+5m]")


def run_ups(session_id: str, enforce: bool = False) -> str:
    """Run hooks/user-prompt-submit.py end-to-end; return its additionalContext."""
    event = {"session_id": session_id, "cwd": str(PLUGIN_ROOT), "user_prompt": "carry on"}
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if enforce:
        env["VC_ROE_HEARTBEAT_ENFORCEMENT"] = "1"
    else:
        env.pop("VC_ROE_HEARTBEAT_ENFORCEMENT", None)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "hooks" / "user-prompt-submit.py")],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        return ""
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return out


DEMAND = "Emit the session-health heartbeat"


def case_ups_informational_tag_carries_no_demand(tmp: Path) -> tuple[bool, str]:
    """I-9 default: past the cadence, the tag reports and demands nothing."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        # 45 min since T0 with no heartbeat: past the 30 min cadence, under 2x.
        write_anchor(sid, t0=now - 45 * 60, last_hb=0, tier="T4")
        ctx = run_ups(sid, enforce=False)
        if "[session-clock:" not in ctx:
            return False, f"FAIL: no clock tag emitted: {ctx!r}"
        if ", DUE]" not in ctx:
            return False, f"FAIL: expected the neutral DUE reading: {ctx!r}"
        if DEMAND in ctx or "OVERDUE" in ctx:
            return False, f"FAIL: demand or OVERDUE wording survived: {ctx!r}"
        return True, "OK (clock tag only, reads DUE, nothing demanded)"
    finally:
        _cleanup(sid)


def case_ups_enforcement_switch_restores_demand(tmp: Path) -> tuple[bool, str]:
    """I-9 reversibility: the pre-1.20.0 demand comes back with the switch on."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        write_anchor(sid, t0=now - 45 * 60, last_hb=0, tier="T4")
        ctx = run_ups(sid, enforce=True)
        if "OVERDUE" not in ctx or DEMAND not in ctx:
            return False, f"FAIL: enforcement mode did not restore the demand: {ctx!r}"
        return True, "OK (VC_ROE_HEARTBEAT_ENFORCEMENT=1 restores OVERDUE + demand)"
    finally:
        _cleanup(sid)


def case_ups_under_cadence_is_ok(tmp: Path) -> tuple[bool, str]:
    """20 min in is now under cadence: the tag reads OK, not DUE."""
    sid = f"vc-roe-test-{uuid.uuid4().hex[:8]}"
    try:
        now = int(time.time())
        write_anchor(sid, t0=now - 20 * 60, last_hb=0, tier="T4")
        ctx = run_ups(sid, enforce=False)
        if ", OK]" not in ctx:
            return False, f"FAIL: expected OK at 20 min under a 30 min cadence: {ctx!r}"
        return True, "OK (20 min reads OK under the 30 min cadence)"
    finally:
        _cleanup(sid)


def main() -> int:
    global ANCHOR_DIR
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
        ("silent-stop with thinking does not block (v1.12.1)",
         case_silent_stop_with_thinking_does_not_block),
        ("clock tag informational, no demand (v1.20.0 I-9)",
         case_ups_informational_tag_carries_no_demand),
        ("enforcement switch restores the demand (v1.20.0 I-9)",
         case_ups_enforcement_switch_restores_demand),
        ("20 min is under the 30 min cadence (v1.20.0 I-9)",
         case_ups_under_cadence_is_ok),
        ("stop logs stop_hook_active=true (v1.26.0)",
         case_stop_logs_stop_hook_active_true),
        ("stop logs stop_hook_active=null when absent (v1.26.0)",
         case_stop_logs_stop_hook_active_absent),
        ("stop logs stop_hook_active on a silent-stop block (v1.26.0)",
         case_stop_logs_stop_hook_active_on_block),
        ("stop logs stop_hook_active on a heartbeat advance (v1.26.0)",
         case_stop_logs_stop_hook_active_on_sentinel),
    ]
    parent = Path(tempfile.mkdtemp(prefix="vc-roe-heartbeat-tests-"))
    # The isolation below moves process-wide state, and the finally clause
    # deletes the directory it points at, so save what it overwrites and put
    # it back. Without this a second in-process main() would resolve its
    # temp dir to the tree the first one removed.
    saved_env = {k: os.environ.get(k)
                 for k in ("TMPDIR", "TEMP", "TMP", "HOME", "USERPROFILE")}
    saved_tempdir = tempfile.tempdir
    saved_anchor_dir = ANCHOR_DIR
    try:
        # F043: do this BEFORE any case runs. Until it does, ANCHOR_DIR and
        # every child hook point at the shared temp namespace live sessions
        # use, so the suite mutates production state as a side effect.
        anchors = isolate_anchor_namespace(parent)
        print(f"== Heartbeat hook regression (fixtures: {parent}) ==")
        print(f"   private anchor namespace: {anchors}\n")
        before = sorted(p.name for p in anchors.iterdir())
        misses = 0
        for label, fn in cases:
            ok, detail = fn(parent)
            status = "OK  " if ok else "FAIL"
            print(f"  [{status}] {label} — {detail}")
            if not ok:
                misses += 1
        # F043 leak guard: anything the run added to the anchor namespace and
        # did not remove would, unisolated, have been added to the shared one.
        after = sorted(p.name for p in anchors.iterdir())
        leaked = sorted(set(after) - set(before))
        if leaked:
            print(f"  [FAIL] anchor-namespace leak check: {len(leaked)} file(s) "
                  f"left behind out of {len(after)} present: {leaked}")
            misses += 1
        else:
            print(f"  [OK  ] anchor-namespace leak check: no residue "
                  f"({len(before)} file(s) before, {len(after)} after)")
        print()
        print(f"Misses: {misses} / {len(cases) + 1}")
        return 0 if misses == 0 else 1
    finally:
        import shutil
        shutil.rmtree(parent, ignore_errors=True)
        ANCHOR_DIR = saved_anchor_dir
        tempfile.tempdir = saved_tempdir
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    sys.exit(main())
