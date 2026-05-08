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
ANCHOR_DIR = Path("/tmp")


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

    Each turn dict supports {role, text, tool_result} shapes; the helper
    builds a minimally-realistic content block list for each."""
    lines: list[str] = []
    for turn in turns:
        role = turn["role"]
        if role == "assistant":
            content = [{"type": "text", "text": turn["text"]}]
        elif role == "user-prompt":
            role = "user"
            content = turn["text"]
        elif role == "tool-result":
            role = "user"
            content = [{"type": "tool_result", "content": turn.get("text", "ok")}]
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
        ["python3", str(HOOK)],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode


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


def main() -> int:
    cases = [
        ("fresh sentinel advances", case_fresh_sentinel_advances),
        ("stale sentinel no-op", case_stale_sentinel_does_not_advance),
        ("no sentinel no-op", case_no_sentinel_no_change),
        ("multi-sentinel picks max", case_multiturn_picks_max_sentinel),
        ("T1 short-circuit", case_tier_t1_no_op),
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
