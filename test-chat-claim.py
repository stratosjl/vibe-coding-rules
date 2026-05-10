#!/usr/bin/env python3
"""Chat-claim regression tests for vc-roe v1.3.0.

Covers the multi-chat-access protection introduced at v1.3.0 per
OBS-vcroe-multi-chat-contamination-01:

- SessionStart acquires the claim or refuses on conflict
- Stop hook refreshes the claim ts (per-turn heartbeat)
- SessionEnd releases the claim
- TTL reaps orphans when env var override is set short

Each test isolates filesystem state by setting HOME to a tempdir, so
~/.claude/projects/... cannot pollute the real operator home dir. The
project cwd used inside the test is a separate tempdir so the claim
file path is fully synthetic.

Pure stdlib. Cleans up its tempdirs on exit.

Usage:
    python3 test-chat-claim.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent
SESSION_START = PLUGIN_ROOT / "hooks" / "session-start.py"
STOP_HOOK = PLUGIN_ROOT / "hooks" / "stop.py"
SESSION_END = PLUGIN_ROOT / "hooks" / "session-end.py"

CLAIM_FILENAME = "chat-claim.json"


def cwd_dashed(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))


def claim_path(home: Path, cwd: Path) -> Path:
    return home / ".claude" / "projects" / cwd_dashed(cwd) / CLAIM_FILENAME


def write_claim(path: Path, session_id: str, ts: int, host: str = "test-host") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": session_id, "ts": ts, "host": host}) + "\n",
                    encoding="utf-8")


def read_claim(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_hook(hook_path: Path, event: dict[str, Any], home: Path,
             extra_env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


def make_test_cwd(tmp: Path, name: str) -> Path:
    p = tmp / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "CLAUDE.md").write_text("# test project\ntier: T0\n", encoding="utf-8")
    return p


PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  -- {detail}")


def case_no_existing_claim_takes_new(home: Path, tmp: Path) -> None:
    print("\n[case] no existing claim -> take-new")
    cwd = make_test_cwd(tmp, "proj-take-new")
    sid = str(uuid.uuid4())
    rc, out, err = run_hook(SESSION_START, {"session_id": sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, f"rc={rc} stderr={err[:200]}")
    cp = claim_path(home, cwd)
    claim = read_claim(cp)
    check("claim file exists", claim is not None, f"path={cp}")
    if claim:
        check("claim session_id matches", claim.get("session_id") == sid)
        check("claim ts present", isinstance(claim.get("ts"), int))
    check("no refusal banner in stdout", "CHAT-CLAIM CONFLICT" not in out)
    check("trace shows take-new", '"chat_claim_action": "take-new"' in out
          or 'take-new' in out, f"out-tail={out[-300:]}")


def case_same_session_resumes(home: Path, tmp: Path) -> None:
    print("\n[case] same session -> resume (refresh ts)")
    cwd = make_test_cwd(tmp, "proj-resume")
    sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, sid, ts=100)  # very old ts
    rc, out, err = run_hook(SESSION_START, {"session_id": sid, "cwd": str(cwd),
                                            "source": "resume"}, home)
    check("rc == 0", rc == 0, err[:200])
    claim = read_claim(cp)
    check("claim still exists", claim is not None)
    if claim:
        check("session_id unchanged", claim.get("session_id") == sid)
        check("ts refreshed (>100)", int(claim.get("ts", 0)) > 100,
              f"ts={claim.get('ts')}")
    check("no refusal banner", "CHAT-CLAIM CONFLICT" not in out)


def case_different_session_within_ttl_refuses(home: Path, tmp: Path) -> None:
    print("\n[case] different session within TTL -> refuse (banner emitted)")
    cwd = make_test_cwd(tmp, "proj-refuse")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    now = int(time.time())
    cp = claim_path(home, cwd)
    write_claim(cp, other_sid, ts=now - 60, host="other-host")
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("refusal banner in stdout", "CHAT-CLAIM CONFLICT" in out,
          f"out-head={out[:500]}")
    check("banner cites owning session", other_sid[:8] in out)
    claim = read_claim(cp)
    check("existing claim untouched", claim is not None and claim.get("session_id") == other_sid)
    check("trace shows refuse", '"chat_claim_action": "refuse"' in out
          or 'refuse' in out)


def case_different_session_past_ttl_takes_orphan(home: Path, tmp: Path) -> None:
    print("\n[case] different session past TTL -> take-orphan")
    cwd = make_test_cwd(tmp, "proj-orphan")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # claim is 100 hours old; default TTL is 8h -> expired.
    write_claim(cp, other_sid, ts=int(time.time()) - 100 * 3600)
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("no refusal banner", "CHAT-CLAIM CONFLICT" not in out)
    claim = read_claim(cp)
    check("claim ownership transferred", claim is not None and claim.get("session_id") == me_sid,
          f"claim={claim}")


def case_corrupt_claim_takes_orphan(home: Path, tmp: Path) -> None:
    print("\n[case] corrupt claim -> take-orphan")
    cwd = make_test_cwd(tmp, "proj-corrupt")
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text("not-json{{{", encoding="utf-8")
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("no refusal banner", "CHAT-CLAIM CONFLICT" not in out)
    claim = read_claim(cp)
    check("claim now valid + owned", claim is not None and claim.get("session_id") == me_sid)


def case_env_var_overrides_ttl(home: Path, tmp: Path) -> None:
    print("\n[case] VC_ROE_CLAIM_TTL_HOURS env override -> short TTL ages out fast")
    cwd = make_test_cwd(tmp, "proj-ttl-env")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # claim is 10 seconds old; with TTL=0.001h (~3.6s) that's expired.
    write_claim(cp, other_sid, ts=int(time.time()) - 10)
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home,
                            extra_env={"VC_ROE_CLAIM_TTL_HOURS": "0.001"})
    check("rc == 0", rc == 0, err[:200])
    check("no refusal banner with short TTL", "CHAT-CLAIM CONFLICT" not in out,
          f"out-head={out[:500]}")
    claim = read_claim(cp)
    check("claim taken over with short TTL", claim is not None and claim.get("session_id") == me_sid)


def case_stop_refreshes_own_claim(home: Path, tmp: Path) -> None:
    print("\n[case] Stop hook refreshes own claim ts")
    cwd = make_test_cwd(tmp, "proj-stop-refresh")
    sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, sid, ts=100)  # ancient
    transcript = tmp / "stop-refresh-transcript.jsonl"
    transcript.write_text("", encoding="utf-8")  # empty transcript -> early return path
    rc, out, err = run_hook(STOP_HOOK, {"session_id": sid, "cwd": str(cwd),
                                        "transcript_path": str(transcript)}, home)
    check("rc == 0", rc == 0, err[:200])
    claim = read_claim(cp)
    check("claim ts refreshed", claim is not None and int(claim.get("ts", 0)) > 100,
          f"ts={claim.get('ts') if claim else 'no-claim'}")


def case_stop_does_not_refresh_other_claim(home: Path, tmp: Path) -> None:
    print("\n[case] Stop hook does not refresh another session's claim")
    cwd = make_test_cwd(tmp, "proj-stop-other")
    me_sid = str(uuid.uuid4())
    other_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, other_sid, ts=100)
    transcript = tmp / "stop-other-transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    rc, out, err = run_hook(STOP_HOOK, {"session_id": me_sid, "cwd": str(cwd),
                                        "transcript_path": str(transcript)}, home)
    check("rc == 0", rc == 0, err[:200])
    claim = read_claim(cp)
    check("other claim unchanged",
          claim is not None and claim.get("session_id") == other_sid and int(claim.get("ts", 0)) == 100,
          f"claim={claim}")


def case_session_end_releases_own_claim(home: Path, tmp: Path) -> None:
    print("\n[case] SessionEnd releases own claim")
    cwd = make_test_cwd(tmp, "proj-end-own")
    sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, sid, ts=int(time.time()))
    rc, out, err = run_hook(SESSION_END, {"session_id": sid, "cwd": str(cwd),
                                          "reason": "exit"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("claim file deleted", not cp.is_file(), f"still at {cp}")


def case_session_end_does_not_release_other_claim(home: Path, tmp: Path) -> None:
    print("\n[case] SessionEnd does NOT release another session's claim")
    cwd = make_test_cwd(tmp, "proj-end-other")
    me_sid = str(uuid.uuid4())
    other_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, other_sid, ts=int(time.time()))
    rc, out, err = run_hook(SESSION_END, {"session_id": me_sid, "cwd": str(cwd),
                                          "reason": "exit"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("other claim file still present", cp.is_file())
    claim = read_claim(cp)
    check("other claim untouched",
          claim is not None and claim.get("session_id") == other_sid)


def case_session_end_no_session_id_noop(home: Path, tmp: Path) -> None:
    print("\n[case] SessionEnd with no session_id is a no-op")
    cwd = make_test_cwd(tmp, "proj-end-noop")
    other_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    write_claim(cp, other_sid, ts=int(time.time()))
    rc, out, err = run_hook(SESSION_END, {"cwd": str(cwd), "reason": "exit"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("claim untouched", cp.is_file())


def main() -> int:
    print("vc-roe chat-claim regression suite (v1.3.0)")
    home = Path(tempfile.mkdtemp(prefix="vcroe-test-home-"))
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-test-cwd-"))
    try:
        case_no_existing_claim_takes_new(home, tmp)
        case_same_session_resumes(home, tmp)
        case_different_session_within_ttl_refuses(home, tmp)
        case_different_session_past_ttl_takes_orphan(home, tmp)
        case_corrupt_claim_takes_orphan(home, tmp)
        case_env_var_overrides_ttl(home, tmp)
        case_stop_refreshes_own_claim(home, tmp)
        case_stop_does_not_refresh_other_claim(home, tmp)
        case_session_end_releases_own_claim(home, tmp)
        case_session_end_does_not_release_other_claim(home, tmp)
        case_session_end_no_session_id_noop(home, tmp)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nResults: {PASS} pass, {FAIL} fail")
    if FAIL:
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
