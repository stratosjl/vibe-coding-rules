#!/usr/bin/env python3
"""Chat-claim regression tests for vc-roe v1.9.0.

Covers the multi-chat-access protection introduced at v1.3.0 per
OBS-vcroe-multi-chat-contamination-01, plus v1.9.0 (F-63-01) Layer 1
liveness probe and Layer 4 worktree-aware bypass:

- SessionStart acquires the claim or refuses on conflict
- Stop hook refreshes the claim ts (per-turn heartbeat)
- SessionEnd releases the claim
- TTL reaps orphans when env var override is set short
- v1.9.0 Layer 1: dead-PID, boot-ID-mismatch take-orphan paths;
  legacy v1.3.0 claim shapes fall through to TTL-only behaviour
- v1.9.0 Layer 4: cwd inside a git worktree bypasses claim entirely

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
import socket
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
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def read_my_boot_id() -> str:
    """v1.9.0: mirror of session-start.py read_boot_id; for test claim setup."""
    try:
        if BOOT_ID_PATH.is_file():
            return BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def read_my_starttime() -> Optional[str]:
    """v1.9.0: read THIS process's starttime via /proc/<pid>/stat (Linux only).

    Used to construct claims that genuinely match the test process so the
    alive-pid path can be exercised. Returns None on non-Linux.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        content = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
        close = content.rfind(")")
        if close < 0:
            return None
        fields = content[close + 1:].split()
        if len(fields) < 20:
            return None
        return fields[19]
    except Exception:
        return None


def my_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "?"


def cwd_dashed(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))


def claim_path(home: Path, cwd: Path) -> Path:
    return home / ".claude" / "projects" / cwd_dashed(cwd) / CLAIM_FILENAME


def write_claim(path: Path, session_id: str, ts: int, host: str = "test-host") -> None:
    """Legacy v1.3.0-shape claim (no pid / pid_starttime / boot_id). Used by
    the v1.3.0 regression cases and by the v1.9.0 backward-compat case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": session_id, "ts": ts, "host": host}) + "\n",
                    encoding="utf-8")


def write_claim_v9(path: Path, session_id: str, ts: int, host: str,
                   pid: int, pid_starttime: Optional[str], boot_id: str) -> None:
    """v1.9.0-shape claim with the new pid + pid_starttime + boot_id fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "ts": ts,
        "host": host,
        "pid": pid,
        "pid_starttime": pid_starttime,
        "boot_id": boot_id,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


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


# ---------------------------------------------------------------------------
# v1.9.0 (F-63-01) test cases — Layer 1 liveness probe and Layer 4 worktree bypass
# ---------------------------------------------------------------------------


def case_dead_pid_takes_orphan(home: Path, tmp: Path) -> None:
    print("\n[case] dead PID + same host -> take-orphan (Layer 1)")
    cwd = make_test_cwd(tmp, "proj-dead-pid")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # PID 99999999 is guaranteed not to exist (max pid on Linux is ~4M).
    write_claim_v9(cp, other_sid, ts=int(time.time()) - 60,
                   host=my_hostname(), pid=99999999,
                   pid_starttime="9999999999", boot_id=read_my_boot_id())
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("no refusal banner (auto-released on dead pid)",
          "CHAT-CLAIM CONFLICT" not in out, f"out-head={out[:400]}")
    claim = read_claim(cp)
    check("claim ownership transferred",
          claim is not None and claim.get("session_id") == me_sid,
          f"claim={claim}")


def case_alive_pid_within_ttl_refuses(home: Path, tmp: Path) -> None:
    print("\n[case] alive PID + matching starttime + within TTL -> refuse (Layer 1)")
    cwd = make_test_cwd(tmp, "proj-alive-pid")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # Use this test process's own pid + starttime as the claim "owner". The
    # liveness probe will see this PID as alive with matching starttime, so
    # the boot_id check / pid-recycle check both pass and we fall through to
    # the TTL evaluation; the claim is fresh -> refuse.
    my_pid = os.getpid()
    my_st = read_my_starttime()
    if my_st is None and sys.platform.startswith("linux"):
        check("read_my_starttime returned non-None on Linux", False,
              "harness regression: cannot read own /proc/<pid>/stat")
        return
    write_claim_v9(cp, other_sid, ts=int(time.time()) - 60,
                   host=my_hostname(), pid=my_pid,
                   pid_starttime=my_st, boot_id=read_my_boot_id())
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("refusal banner present (live owner)",
          "CHAT-CLAIM CONFLICT" in out, f"out-head={out[:400]}")
    claim = read_claim(cp)
    check("existing claim untouched",
          claim is not None and claim.get("session_id") == other_sid)


def case_boot_id_mismatch_takes_orphan(home: Path, tmp: Path) -> None:
    print("\n[case] boot_id mismatch + same host -> take-orphan (Layer 1)")
    cwd = make_test_cwd(tmp, "proj-boot-mismatch")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    our_boot = read_my_boot_id()
    if not our_boot:
        # Non-Linux: skip; boot_id probe is Linux-only.
        print("  SKIP  (boot_id not available on this platform)")
        return
    # Use a guaranteed-different boot_id (different UUID).
    fake_boot = "00000000-0000-0000-0000-000000000000"
    if fake_boot == our_boot:
        fake_boot = "11111111-1111-1111-1111-111111111111"
    write_claim_v9(cp, other_sid, ts=int(time.time()) - 60,
                   host=my_hostname(), pid=os.getpid(),
                   pid_starttime=read_my_starttime(), boot_id=fake_boot)
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("no refusal banner (boot-id mismatch -> auto-orphan)",
          "CHAT-CLAIM CONFLICT" not in out, f"out-head={out[:400]}")
    claim = read_claim(cp)
    check("claim ownership transferred after boot-id mismatch",
          claim is not None and claim.get("session_id") == me_sid)


def case_legacy_claim_no_pid_falls_through_to_ttl(home: Path, tmp: Path) -> None:
    print("\n[case] legacy v1.3.0 claim (no pid/boot_id) -> TTL-only behaviour preserved")
    cwd = make_test_cwd(tmp, "proj-legacy-claim")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # Legacy claim shape: just session_id + ts + host. Within TTL window.
    # v1.9.0 must NOT auto-orphan this; behaviour matches v1.3.0..v1.8.1.
    write_claim(cp, other_sid, ts=int(time.time()) - 60, host=my_hostname())
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("refusal banner present (legacy claim within TTL)",
          "CHAT-CLAIM CONFLICT" in out, f"out-head={out[:400]}")
    claim = read_claim(cp)
    check("legacy claim untouched",
          claim is not None and claim.get("session_id") == other_sid
          and "pid" not in claim)


def case_worktree_bypass_no_claim_written(home: Path, tmp: Path) -> None:
    print("\n[case] cwd inside git worktree -> bypass (no claim file written) (Layer 4)")
    # Build a real git repo + linked worktree. The worktree's .git is a file
    # pointing at <main-repo>/.git/worktrees/<name>, and `git rev-parse
    # --git-common-dir` resolves to <main-repo>/.git while --git-dir resolves
    # to <main-repo>/.git/worktrees/<name>; they differ, so is_in_git_worktree
    # returns True.
    main_repo = tmp / "wt-main-repo"
    main_repo.mkdir(parents=True, exist_ok=True)
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "HOME": str(home), "PATH": os.environ.get("PATH", "")}
    def git(*args: str, cwd: Path) -> None:
        subprocess.run(["git", "-C", str(cwd), *args], check=True,
                       capture_output=True, env={**os.environ, **env})
    try:
        git("init", "-q", "-b", "main", cwd=main_repo)
        git("config", "user.email", "test@example.invalid", cwd=main_repo)
        git("config", "user.name", "vc-roe-test", cwd=main_repo)
        (main_repo / "seed.txt").write_text("seed", encoding="utf-8")
        git("add", "seed.txt", cwd=main_repo)
        git("commit", "-q", "-m", "seed", cwd=main_repo)
        wt = tmp / "wt-linked"
        git("worktree", "add", "-q", str(wt), "-b", "wt-br", cwd=main_repo)
    except subprocess.CalledProcessError as e:
        check("git worktree setup", False,
              f"git failed: {e.stderr.decode('utf-8', 'replace') if e.stderr else e}")
        return
    (wt / "CLAUDE.md").write_text("# test project\ntier: T0\n", encoding="utf-8")
    sid = str(uuid.uuid4())
    rc, out, err = run_hook(SESSION_START, {"session_id": sid, "cwd": str(wt),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    cp = claim_path(home, wt)
    check("no claim file written in worktree", not cp.is_file(),
          f"unexpected claim at {cp}")
    check("trace shows bypass-worktree action",
          "bypass-worktree" in out, f"out-tail={out[-400:]}")
    check("no refusal banner emitted", "CHAT-CLAIM CONFLICT" not in out)


def case_pid_remote_host_falls_through(home: Path, tmp: Path) -> None:
    print("\n[case] dead PID but on remote host -> cannot probe, fall through to TTL")
    cwd = make_test_cwd(tmp, "proj-remote-host")
    other_sid = str(uuid.uuid4())
    me_sid = str(uuid.uuid4())
    cp = claim_path(home, cwd)
    # Different host from ours: pid liveness cannot be probed remotely. Claim
    # is fresh -> TTL evaluation -> refuse. The owning PID could be anything;
    # we use a dead-on-OUR-machine PID to prove the host check guards us
    # from probing the wrong process.
    other_host = "other-machine-not-localhost"
    if other_host == my_hostname():
        other_host = "different-machine-still-not-this-one"
    write_claim_v9(cp, other_sid, ts=int(time.time()) - 60,
                   host=other_host, pid=99999999,
                   pid_starttime="9999999999", boot_id="other-boot-uuid")
    rc, out, err = run_hook(SESSION_START, {"session_id": me_sid, "cwd": str(cwd),
                                            "source": "startup"}, home)
    check("rc == 0", rc == 0, err[:200])
    check("refusal banner present (cross-host claim, TTL evaluation)",
          "CHAT-CLAIM CONFLICT" in out, f"out-head={out[:400]}")
    claim = read_claim(cp)
    check("cross-host claim untouched",
          claim is not None and claim.get("session_id") == other_sid)


def main() -> int:
    print("vc-roe chat-claim regression suite (v1.9.0)")
    home = Path(tempfile.mkdtemp(prefix="vcroe-test-home-"))
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-test-cwd-"))
    try:
        # v1.3.0 baseline cases
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
        # v1.9.0 (F-63-01) Layer 1 + Layer 4 cases
        case_dead_pid_takes_orphan(home, tmp)
        case_alive_pid_within_ttl_refuses(home, tmp)
        case_boot_id_mismatch_takes_orphan(home, tmp)
        case_legacy_claim_no_pid_falls_through_to_ttl(home, tmp)
        case_worktree_bypass_no_claim_written(home, tmp)
        case_pid_remote_host_falls_through(home, tmp)
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
