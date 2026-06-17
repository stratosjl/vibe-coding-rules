#!/usr/bin/env python3
"""PostToolUse hook for the VC-RoE (Vibe Coding · Rules of Engagement) Claude Code plugin.

Added at v1.1.0 (2026-05-08) to close the autonomous-work heartbeat-silence
gap surfaced at session 33 of the [EXAMPLE-PROJ] Website project (96-minute silent
window between UserPromptSubmit fires; sid f31d8778 in the hook log shows
status=OVERDUE-2X t0=96 hb=96 at the next user prompt). Root cause:
UserPromptSubmit fires only on user prompts; Stop hook fires every turn
but only greps for the sentinel and never injects context. During long
autonomous work the assistant has no in-band signal that the cadence
elapsed.

Fix: PostToolUse fires after every tool call. When TIER is T2+ and the
heartbeat cadence is OVERDUE or OVERDUE-2X, emit an additionalContext
clock-tag asking the assistant to surface the heartbeat block. Rate-
limited to once per 60 seconds via LAST_PTU_TAG_SEC field in the anchor
to avoid spamming when the assistant runs many tool calls back-to-back.
At OVERDUE-2X, auto-advance LAST_HEARTBEAT to mirror the
user-prompt-submit.py fail-safe semantics (D-MET-41).

v1.1.1 (2026-05-09) F-8 closure: PostToolUse now mirrors stop.py's
transcript-grep responsibility. Operational reality (confirmed via the
[EXAMPLE-PROJ] session 38 hook log forensics): Claude Code fires Stop only at the
end of an agent loop, not after every assistant turn. Heartbeat
sentinels emitted in intermediate turns within a single agent loop are
invisible to Stop until loop-end, leaving LAST_HEARTBEAT stale and
producing repeated false OVERDUE alarms via PostToolUse. The fix: when
PostToolUse runs, it reads the transcript and greps the agent-loop's
assistant text for sentinels; if a sentinel newer than current
LAST_HEARTBEAT is present, LAST_HEARTBEAT advances before the OVERDUE
check, suppressing false alarms while preserving the existing fail-safe
semantics. Sentinel freshness gates the advance (parsed minute must
exceed current LAST_HEARTBEAT minute) so stale loop-history sentinels
cannot mask a legitimate cadence miss.

Pure stdlib. Never throws. Logs to ~/.claude/methodology-hook.log.
Tier-aware: short-circuits to no-op at T0/T1.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# OBS-MET-AI: Windows stdout defaults to cp1252 which cannot encode non-ASCII.
# PTU output is ASCII but reconfigure defensively for parity with the other
# vc-roe hooks. No-op on streams that don't support reconfigure.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")

ROUTINE_VERSION = "1.15.1"
ANCHOR_DIR = Path(tempfile.gettempdir())  # OBS-MET-AK: cross-runtime /tmp divergence on Windows
ANCHOR_PREFIX = "claude-methodology-anchor-"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"
UPS_MARKER_PREFIX = "claude-methodology-current-session-"

CADENCE_SEC = 15 * 60
OVERDUE_2X_SEC = 30 * 60
PTU_RATE_LIMIT_SEC = 60
TIER_ACTIVE = {"T2", "T3", "T4"}

SENTINEL_RE = re.compile(r"\[heartbeat-fired:T\+(\d+)m\]")

# v1.10.0 (F-63-01 Layer 2): chat-claim writer-promotion. PostToolUse fires
# after every tool call; on a watched-path edit or a git-mutation Bash, we
# promote the chat-claim from mode=reader to mode=writer (or refresh the
# writer-ts if we're already the writer). If another alive chat holds the
# writer-lease we emit a conflict banner via additionalContext and append a
# conflict row to the writer-lease ledger; the mutation itself is post-hoc
# (Claude Code has no PreToolUse hook entry today, by operator-confirmed
# scope at s68 OPEN Q1 — post-hoc detect + audit-pin was selected as the
# Layer 2 shape).
CLAIM_FILENAME = "chat-claim.json"
WRITER_LEASE_FILENAME = "writer-lease.jsonl"
MODE_READER = "reader"
MODE_WRITER = "writer"
WATCHED_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Watched-path regex: absolute-path substring matches. Matches the
# regulator-presentable surfaces per open-issues.md § F-63-01 Layer 2 scope
# (handovers/, decisions.md, open-issues.md, hooks/, bin/, plugin.json) plus
# the v1.8.0 .githooks/ sibling and the T4-close CHANGELOG.md + methodology
# slices. Substring-anywhere rather than cwd-prefix to handle the vc-roe
# split-tree setup where the cwd (***REMOVED*** bundle) and the working tree
# (~/Projects/...) are separate paths but both are part of the same project
# surface from the operator's perspective.
WATCHED_PATH_RE = re.compile(
    r"(?:"
    r"/handovers/[^/]+\.md$"
    r"|/decisions\.md$"
    r"|/open-issues\.md$"
    r"|/hooks/[^/]+\.(?:py|json)$"
    r"|/bin/[^/]+\.(?:sh|py)$"
    r"|/\.claude-plugin/plugin\.json$"
    r"|/\.githooks/[^/]+$"
    r"|/CHANGELOG\.md$"
    r"|/methodology-content/T[0-4]\.md$"
    r")"
)

# Git-mutation Bash detection. Matches `git <mutating-verb>` anywhere in the
# command after a shell-statement boundary (start, ;, &&, ||, |). Read-only
# git invocations (status, log, diff, show, rev-parse, branch -l, etc.) are
# intentionally NOT in this set — they do not mutate the working tree or
# refs and should not promote the claim to writer.
GIT_MUTATION_RE = re.compile(
    r"(?:^|[\s;&|])git\s+(?:"
    r"commit|push|tag|merge|rebase|reset|cherry-pick|revert"
    r"|filter-repo|filter-branch|am"
    r"|stash\s+(?:apply|pop|drop|push)"
    r"|worktree\s+(?:add|remove|prune|move)"
    r"|switch|checkout(?!\s+--\s*$)"
    r"|restore"
    r"|clean"
    r"|update-ref|symbolic-ref|notes\s+(?:add|edit|remove|append|copy)"
    r")\b"
)


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def check_marker_mismatch(session_id: str, cwd_raw: Optional[str]) -> None:
    """v1.1.2 PTU forensic parity: marker-mismatch diagnostic.

    Mirrors stop.py's check_marker_mismatch so PostToolUse also surfaces
    cross-process anchor-rewrite races and other cwd-related drift in
    soak data. Reads /tmp/claude-methodology-current-session-<cwd-hash>
    and compares its writer's session_id against the calling
    session_id; if they differ, logs a marker-mismatch diagnostic.
    Recording-only; no behaviour change.

    Note re OBS-48-01 ([EXAMPLE-PROJ] sessions 47 + 48 dual-startup pattern): this
    helper catches concurrent same-cwd cross-session races but does NOT
    by itself detect the OBS-48-01 case (sequential SessionStart events
    in different cwds within the same chat after /clear), since each
    session's marker is keyed by its own cwd. The OBS-48-01 root issue
    is owned by Claude Code's cwd-at-launch + post-/clear session_id
    binding; the operator-side workaround is to launch claude from
    inside the project directory the chat is for. The diagnostic here
    is structural addition for general forensic surface, not a
    complete OBS-48-01 detector.
    """
    if not session_id or not cwd_raw:
        return
    try:
        cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd_raw).resolve()))
        marker = ANCHOR_DIR / f"{UPS_MARKER_PREFIX}{cwd_dashed}"  # OBS-MET-AK
        if not marker.is_file():
            return
        marker_session = marker.read_text(encoding="utf-8").strip()
        if marker_session and marker_session != session_id:
            append_log({
                "ts": time.time(),
                "hook": "post-tool-use",
                "diagnostic": "marker-mismatch",
                "session_id": session_id,
                "marker_session_id": marker_session,
                "cwd": cwd_raw,
                "routine_version": ROUTINE_VERSION,
            })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# v1.10.0 (F-63-01 Layer 2) writer-promotion helpers.
#
# Hook independence (canonical vc-roe pattern since v1.3.0): post-tool-use.py
# duplicates the liveness-probe + claim-path helpers from session-start.py
# rather than importing, so each hook file is self-contained. Read the
# v1.9.0 session-start.py docstrings for the canonical rationale; the
# behaviour mirrored here is byte-equivalent in semantic outcome.
# ---------------------------------------------------------------------------


def claim_dir_for(cwd_raw: Optional[str]) -> Optional[Path]:
    if not cwd_raw:
        return None
    try:
        cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd_raw).resolve()))
        return Path.home() / ".claude" / "projects" / cwd_dashed
    except Exception:
        return None


def claim_path_for(cwd_raw: Optional[str]) -> Optional[Path]:
    d = claim_dir_for(cwd_raw)
    return (d / CLAIM_FILENAME) if d else None


def writer_lease_path_for(cwd_raw: Optional[str]) -> Optional[Path]:
    d = claim_dir_for(cwd_raw)
    return (d / WRITER_LEASE_FILENAME) if d else None


def read_claim_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_claim_file(path: Path, claim: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(claim, f)
            f.write("\n")
        return True
    except Exception:
        return False


def append_writer_lease_row(lease_path: Optional[Path], row: dict[str, Any]) -> None:
    """Append a single JSONL row to the writer-lease ledger. Never throws.

    Per s68 OPEN Q4 operator decision, the ledger lives at
    ~/.claude/projects/<slug>/writer-lease.jsonl alongside chat-claim.json.
    Append-only; T4-close summarises into handover audit-trail markdown.
    """
    if lease_path is None:
        return
    try:
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lease_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_boot_id() -> str:
    """v1.9.0 mirror: stable per-boot identifier for the host (Linux). Empty
    string on macOS / Windows / read-error."""
    try:
        path = Path("/proc/sys/kernel/random/boot_id")
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def read_pid_starttime(pid: int) -> Optional[str]:
    """v1.9.0 mirror: per-process creation marker. Linux /proc/<pid>/stat
    field 22, Windows GetProcessTimes() FILETIME, else None."""
    if sys.platform.startswith("linux"):
        try:
            stat_path = Path(f"/proc/{pid}/stat")
            if not stat_path.is_file():
                return None
            content = stat_path.read_text(encoding="utf-8", errors="replace")
            close = content.rfind(")")
            if close < 0:
                return None
            fields = content[close + 1:].split()
            if len(fields) < 20:
                return None
            return fields[19]
        except Exception:
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_t = wintypes.FILETIME()
                kernel_t = wintypes.FILETIME()
                user_t = wintypes.FILETIME()
                ok = kernel32.GetProcessTimes(
                    h, ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel_t), ctypes.byref(user_t),
                )
                if not ok:
                    return None
                ft = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return str(ft)
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return None
    return None


def is_pid_alive(pid: int) -> Optional[bool]:
    """v1.9.0 mirror: True if PID currently exists; False if dead; None if
    probe is impossible. POSIX uses os.kill(pid, 0); Windows uses
    OpenProcess + GetExitCodeProcess via ctypes."""
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                err = ctypes.get_last_error()
                if err == 5:
                    return True
                return False
            try:
                exit_code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                if not ok:
                    return None
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def is_in_git_worktree(cwd_raw: Optional[str]) -> bool:
    """v1.9.0 mirror: True iff cwd sits inside a linked git worktree."""
    if not cwd_raw:
        return False
    try:
        common = subprocess.run(
            ["git", "-C", str(cwd_raw), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if common.returncode != 0:
            return False
        gitdir = subprocess.run(
            ["git", "-C", str(cwd_raw), "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if gitdir.returncode != 0:
            return False
        cwd_p = Path(cwd_raw)
        try:
            common_p = (cwd_p / common.stdout.strip()).resolve()
            gitdir_p = (cwd_p / gitdir.stdout.strip()).resolve()
        except Exception:
            return False
        return common_p != gitdir_p
    except Exception:
        return False


def detect_write(tool_name: str, tool_input: Any) -> Optional[tuple[str, str]]:
    """Returns (kind, target) on a watched-write detection, else None.

    kind is one of "watched-path" or "git-mutation". target is the matched
    file_path (for watched-path) or a one-line summary of the command (for
    git-mutation). Read-only tools, non-watched paths, and read-only git
    invocations all return None.
    """
    if not tool_name:
        return None
    if tool_name in WATCHED_WRITE_TOOLS:
        if not isinstance(tool_input, dict):
            return None
        file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not file_path:
            return None
        # WATCHED_PATH_RE is written with POSIX `/` separators. On Windows the
        # tool passes backslash paths, so normalize before matching or
        # watched-path writer-promotion never fires there (git-mutation
        # promotion is command-based and unaffected).
        if WATCHED_PATH_RE.search(file_path.replace("\\", "/")):
            return ("watched-path", file_path)
        return None
    if tool_name == "Bash":
        if not isinstance(tool_input, dict):
            return None
        command = str(tool_input.get("command") or "")
        if not command:
            return None
        if GIT_MUTATION_RE.search(command):
            # Return a truncated summary; full command can be long and may
            # contain HEREDOC content we don't want in the ledger.
            summary = command.strip().splitlines()[0][:160] if command.strip() else command[:160]
            return ("git-mutation", summary)
        return None
    return None


def evaluate_writer_promotion(claim: Optional[dict[str, Any]], our_session: str,
                              our_host: str, our_boot_id: str,
                              ) -> tuple[str, dict[str, Any]]:
    """Decide promotion action for the post-hoc writer-claim transition.

    Returns (action, info) where action is one of:
        "no-claim"             no claim file on disk; skip promotion (SessionStart
                               either bypassed via worktree or has not yet fired).
        "promote"              claim is mode=reader (any session) OR mode=writer
                               owned by a dead/stale owner; safe to take writer
                               lease.
        "refresh"              claim is mode=writer owned by us; refresh
                               writer_last_mutation_ts.
        "conflict"             claim is mode=writer owned by another alive
                               session; emit banner + ledger conflict row, do
                               NOT overwrite.
        "legacy-skip"          claim is a legacy v1.3.0..v1.9.x shape (no
                               mode field); preserve exclusive-claim semantics
                               by NOT promoting from PostToolUse, only the
                               TTL/SessionEnd cycle releases legacy claims.

    The liveness probe mirrors session-start.py's evaluate_claim: same-host
    + boot_id mismatch → owner cannot be alive; same-host + pid_starttime
    differs or PID is dead → owner gone.
    """
    if not isinstance(claim, dict):
        return ("no-claim", {"reason": "no-claim-on-disk"})

    existing_mode = str(claim.get("mode", "") or "").strip()
    if not existing_mode:
        return ("legacy-skip", {"reason": "legacy-claim-no-mode-field"})

    other_session = str(claim.get("session_id", "") or "")
    other_writer = str(claim.get("writer_session_id", "") or "")
    other_host = str(claim.get("host", "?") or "?")

    # If mode=writer and writer_session_id matches us → refresh path.
    if existing_mode == MODE_WRITER and other_writer == our_session:
        return ("refresh", {
            "reason": "same-session-writer-refresh",
            "writer_session": other_writer,
        })

    # If mode=writer and writer_session_id is someone else → check liveness.
    if existing_mode == MODE_WRITER and other_writer and other_writer != our_session:
        # Mirror session-start.py liveness probe to decide alive vs dead.
        other_pid_raw = claim.get("pid", None)
        other_pid_starttime = claim.get("pid_starttime", None)
        other_boot_id = str(claim.get("boot_id", "") or "")
        same_host = bool(other_host and other_host != "?" and other_host == our_host)

        # boot-id mismatch → host rebooted → owner can't be alive.
        if same_host and our_boot_id and other_boot_id and our_boot_id != other_boot_id:
            return ("promote", {
                "reason": "writer-take-orphan-boot-id-mismatch",
                "displaced_writer": other_writer,
            })

        # pid liveness probe.
        if same_host and other_pid_raw is not None:
            try:
                other_pid = int(other_pid_raw)
            except (TypeError, ValueError):
                other_pid = None
            if other_pid is not None and other_pid > 0:
                if other_pid_starttime is not None:
                    current_starttime = read_pid_starttime(other_pid)
                    if current_starttime is None:
                        return ("promote", {
                            "reason": "writer-take-orphan-pid-dead",
                            "displaced_writer": other_writer,
                        })
                    if str(current_starttime) != str(other_pid_starttime):
                        return ("promote", {
                            "reason": "writer-take-orphan-pid-recycled",
                            "displaced_writer": other_writer,
                        })
                else:
                    alive = is_pid_alive(other_pid)
                    if alive is False:
                        return ("promote", {
                            "reason": "writer-take-orphan-pid-dead",
                            "displaced_writer": other_writer,
                        })
        # Owner appears alive (or probe inconclusive on cross-host /
        # incomplete claim): conflict path.
        return ("conflict", {
            "reason": "active-writer-conflict",
            "other_writer": other_writer,
            "other_host": other_host,
        })

    # mode=reader (any session, including same-session — promote from
    # reader to writer is the canonical Layer 2 transition).
    if existing_mode == MODE_READER:
        return ("promote", {
            "reason": "reader-to-writer-promote",
            "prior_reader_session": other_session,
        })

    # Defensive: unknown mode string → treat as legacy.
    return ("legacy-skip", {"reason": f"unknown-mode-{existing_mode}"})


def promote_or_refresh_writer(claim_path: Path, claim: Optional[dict[str, Any]],
                              action: str, our_session: str, our_host: str,
                              our_boot_id: str, our_pid: int,
                              our_pid_starttime: Optional[str],
                              now: int) -> bool:
    """Write the post-promotion / post-refresh claim. Returns True on success.

    On "promote": writes a fresh writer claim seeded with our liveness fields.
    On "refresh": preserves existing writer_acquired_ts, updates
    writer_last_mutation_ts + ts to now.
    """
    if action == "refresh" and isinstance(claim, dict):
        new_claim = dict(claim)
        new_claim["ts"] = now
        new_claim["writer_last_mutation_ts"] = now
        # Keep writer_acquired_ts as-is.
        return write_claim_file(claim_path, new_claim)
    if action == "promote":
        # Preserve top-level pid/host fields as our own; the writer-lease
        # IS our session now. Preserve writer_idle_demote_seconds if
        # present, otherwise default to 1800.
        prior_idle = 1800
        if isinstance(claim, dict):
            try:
                prior_idle = int(claim.get("writer_idle_demote_seconds", 1800))
            except (TypeError, ValueError):
                prior_idle = 1800
        new_claim = {
            "session_id": our_session,
            "ts": now,
            "host": our_host,
            "pid": our_pid,
            "pid_starttime": our_pid_starttime,
            "boot_id": our_boot_id,
            "mode": MODE_WRITER,
            "writer_session_id": our_session,
            "writer_acquired_ts": now,
            "writer_last_mutation_ts": now,
            "writer_idle_demote_seconds": prior_idle,
        }
        return write_claim_file(claim_path, new_claim)
    return False


def writer_conflict_banner(info: dict[str, Any], target_kind: str,
                           target: str) -> str:
    other_writer = info.get("other_writer", "?")
    other_writer_short = (
        (other_writer[:8] + "...")
        if isinstance(other_writer, str) and len(other_writer) > 12
        else other_writer
    )
    other_host = info.get("other_host", "?")
    return (
        "<system-reminder>\n"
        "## CHAT-CLAIM WRITER CONFLICT (Layer 2 post-hoc detect)\n\n"
        f"Another Claude Code session holds the active writer-lease on this "
        f"project. The mutation you just executed ({target_kind}: `{target}`) "
        "has been recorded in the writer-lease ledger as a conflict row.\n\n"
        f"- Active writer session_id: `{other_writer_short}` (host `{other_host}`)\n"
        f"- This session: implicit-reader; writer-promotion was BLOCKED.\n\n"
        "**Recommended action:** halt further mutations on the regulator-"
        "presentable surface (handovers/, decisions.md, open-issues.md, "
        "hooks/, bin/, .claude-plugin/plugin.json, .githooks/, CHANGELOG.md, "
        "methodology-content/T*.md) until the active writer session releases "
        "the lease (idle-demote after 30 min OR SessionEnd OR PID death). "
        "Read-only investigation is safe.\n\n"
        "If the active writer session is known-dead (operator killed it, "
        "host rebooted, etc.) the lease will auto-release on the next probe; "
        "manual override: delete the claim file at the path the SessionStart "
        "trace cites.\n"
        "</system-reminder>"
    )


def handle_writer_promotion(session_id: str, event: dict[str, Any]) -> str:
    """Run the v1.10.0 Layer 2 writer-promotion path. Returns a banner string
    to emit via additionalContext (only on conflict), else empty string.

    Behaviour matrix:
      - No tool_name / not a watched write / read-only tool → silent no-op.
      - cwd inside a git worktree → bypass (mirror of SessionStart Layer 4).
      - No claim file on disk → silent no-op (don't cold-start; SessionStart
        is the canonical acquire site).
      - Legacy v1.3.0..v1.9.x claim shape (no `mode` field) → preserve
        v1.9.x exclusive semantics; do not promote post-hoc, only TTL/
        SessionEnd cycle handles the legacy population.
      - mode=writer same-session → refresh writer_last_mutation_ts.
      - mode=writer dead-other-session → take orphan + promote.
      - mode=writer alive-other-session → CONFLICT banner + ledger row.
      - mode=reader (any session) → promote to writer.

    Every detected write also appends one row to writer-lease.jsonl with
    {ts, session_id, host, pid, tool_name, target_kind, target, action,
    reason}. Ledger is append-only; T4 close summarises into the audit-
    trail markdown per element 4 (compliance trace).
    """
    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input")
    cwd_raw = event.get("cwd")

    detection = detect_write(tool_name, tool_input)
    if detection is None:
        return ""

    target_kind, target = detection

    # Layer 4 mirror: worktree bypass.
    if is_in_git_worktree(cwd_raw):
        return ""

    cp = claim_path_for(cwd_raw)
    lp = writer_lease_path_for(cwd_raw)
    if cp is None:
        return ""

    claim = read_claim_file(cp)
    if claim is None:
        # No claim — likely SessionStart did not fire or claim was deleted.
        # Don't cold-start the writer claim from PostToolUse; just record a
        # diagnostic ledger row so the gap is visible at audit-trail-time.
        append_writer_lease_row(lp, {
            "ts": int(time.time()),
            "session_id": session_id,
            "tool_name": tool_name,
            "target_kind": target_kind,
            "target": target,
            "action": "no-claim-skip",
            "reason": "no-claim-on-disk",
            "routine_version": ROUTINE_VERSION,
        })
        return ""

    try:
        our_host = socket.gethostname()
    except Exception:
        our_host = "?"
    our_boot_id = read_boot_id()
    our_pid = os.getpid()

    action, info = evaluate_writer_promotion(claim, session_id, our_host, our_boot_id)

    now = int(time.time())

    if action == "legacy-skip":
        append_writer_lease_row(lp, {
            "ts": now,
            "session_id": session_id,
            "tool_name": tool_name,
            "target_kind": target_kind,
            "target": target,
            "action": "legacy-skip",
            "reason": info.get("reason", ""),
            "routine_version": ROUTINE_VERSION,
        })
        return ""

    if action == "conflict":
        append_writer_lease_row(lp, {
            "ts": now,
            "session_id": session_id,
            "tool_name": tool_name,
            "target_kind": target_kind,
            "target": target,
            "action": "conflict",
            "reason": info.get("reason", ""),
            "other_writer": info.get("other_writer", ""),
            "other_host": info.get("other_host", ""),
            "host": our_host,
            "pid": our_pid,
            "routine_version": ROUTINE_VERSION,
        })
        append_log({
            "ts": time.time(),
            "hook": "post-tool-use",
            "event": "writer-conflict",
            "session_id": session_id,
            "tool_name": tool_name,
            "target_kind": target_kind,
            "target": target,
            "other_writer": info.get("other_writer", ""),
            "routine_version": ROUTINE_VERSION,
        })
        return writer_conflict_banner(info, target_kind, target)

    # promote / refresh: write claim + ledger acquire/refresh row.
    our_pid_starttime = read_pid_starttime(our_pid)
    promote_or_refresh_writer(
        cp, claim, action, session_id, our_host, our_boot_id,
        our_pid, our_pid_starttime, now,
    )
    append_writer_lease_row(lp, {
        "ts": now,
        "session_id": session_id,
        "host": our_host,
        "pid": our_pid,
        "tool_name": tool_name,
        "target_kind": target_kind,
        "target": target,
        "action": action,  # "promote" or "refresh"
        "reason": info.get("reason", ""),
        "routine_version": ROUTINE_VERSION,
    })
    return ""


def read_anchor(session_id: str) -> Optional[dict[str, str]]:
    path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_anchor(session_id: str, fields: dict[str, str]) -> None:
    path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
    try:
        ordered = "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n"
        path.write_text(ordered, encoding="utf-8")
    except Exception:
        pass


def emit(additional_context: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    }
    try:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def extract_assistant_text(turn: Any) -> str:
    """Best-effort extract of assistant-message text across transcript shapes.

    Mirrors stop.py at v1.1.1 so the two hooks share grep behaviour."""
    if not isinstance(turn, dict):
        return ""
    role = turn.get("role") or turn.get("type")
    if role != "assistant":
        msg = turn.get("message")
        if isinstance(msg, dict):
            return extract_assistant_text(msg)
        return ""
    content = turn.get("content")
    if content is None:
        msg = turn.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def agent_loop_assistant_text(transcript_path: str) -> Optional[str]:
    """Collect assistant text from the current agent loop.

    Walks the transcript backwards from the end, gathering all assistant-
    role lines, skipping role="user" lines that carry only tool_result
    blocks (those are tool returns within the same agent loop, not turn
    boundaries), and skipping synthetic Claude Code transcript metadata
    lines (attachment, last-prompt, ai-title, permission-mode,
    file-history-snapshot, plus any future unknown line type). Stops
    only at a true user-prompt boundary.

    The walk shape matches stop.py's last_assistant_text. The naming
    "agent_loop_assistant_text" emphasises the v1.1.1 insight that this
    text spans the entire current agent loop (multi-turn / multi-tool):
    Stop only sees this scope once per loop, but PostToolUse can sample
    it after every tool call.

    v1.1.2 fix for OBS-46-02 ([EXAMPLE-PROJ] sessions 46 + 48 + 49 forensics):
    earlier versions halted the walk at any non-assistant /
    non-user-with-tool_result role, including synthetic Claude Code
    metadata lines that interleave between assistant text and the most-
    recent tool_result in real chats. The bug missed pre-tool sentinel
    emissions and produced repeated false OVERDUE alarms. New rule: HALT
    only on a true user-prompt boundary (role="user" whose content is
    NOT a tool_result-bearing block list). Any other role/type is
    transparent and skipped past. Confirmed via live reproduction at
    [EXAMPLE-PROJ] s48 + s49 where the assistant's own heartbeat sentinel was
    missed by the immediately-following PostToolUse despite being on
    disk.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    parts: list[str] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            turn = json.loads(line)
        except Exception:
            continue

        role = turn.get("role") or turn.get("type") or ""
        if not role:
            inner = turn.get("message")
            if isinstance(inner, dict):
                role = inner.get("role") or inner.get("type") or ""

        if role == "assistant":
            t = extract_assistant_text(turn)
            if t:
                parts.append(t)
            continue

        if role == "user":
            # Distinguish tool_result-bearing user lines (internal to the
            # agent loop, skip) from real user-prompt lines (boundary,
            # halt). A user line whose content list contains any
            # tool_result block is treated as internal.
            content = turn.get("content")
            if content is None:
                msg = turn.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
            if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result"
                for c in content
            ):
                continue
            # Real user-prompt boundary: halt the walk.
            break

        # v1.1.2 fix for OBS-46-02: any other role/type (synthetic Claude
        # Code metadata like attachment / last-prompt / ai-title /
        # permission-mode / file-history-snapshot, or any future unknown
        # synthetic line type) is transparent and skipped past. Do NOT
        # halt the walk on these. The previous "if role: break" rule
        # halted on synthetic metadata, missing pre-tool sentinels.
        continue

    if not parts:
        return None

    parts.reverse()
    return "\n".join(parts)


def max_sentinel_minute(text: str) -> int:
    """Return the highest T+N minute from any heartbeat sentinel in text.

    Returns -1 if no sentinel is present. Used to gate LAST_HEARTBEAT
    advance on freshness: a sentinel whose declared minute is no greater
    than current LAST_HEARTBEAT_MIN is stale loop-history and must not
    suppress a legitimate OVERDUE alarm.
    """
    best = -1
    for m in SENTINEL_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (ValueError, IndexError):
            continue
        if n > best:
            best = n
    return best


def main() -> int:
    started = time.time()
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({"ts": time.time(), "hook": "post-tool-use", "phase": "stdin", "error": str(e)})
        return 0

    session_id = event.get("session_id") or ""
    if not session_id:
        return 0

    check_marker_mismatch(session_id, event.get("cwd"))

    # v1.10.0 (F-63-01 Layer 2): writer-claim promotion block. T-agnostic
    # (chat-claim semantics are not tier-gated, same as v1.3.0..v1.9.x).
    # Post-hoc detect + audit-pin per operator-confirmed s68 OPEN Q1.
    # Returns a banner string ONLY on conflict; otherwise empty.
    writer_banner = handle_writer_promotion(session_id, event)

    anchor = read_anchor(session_id)
    if not anchor:
        # No anchor — heartbeat path is unavailable; still emit the writer
        # banner if we have one so the conflict surfaces to the operator.
        if writer_banner:
            emit(writer_banner)
        return 0

    tier = anchor.get("TIER", "T0")
    if tier not in TIER_ACTIVE:
        if writer_banner:
            emit(writer_banner)
        return 0

    try:
        t0 = int(anchor.get("T0", "0"))
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        last_ptu_tag = int(anchor.get("LAST_PTU_TAG_SEC", "0"))
    except ValueError:
        if writer_banner:
            emit(writer_banner)
        return 0
    if t0 <= 0:
        if writer_banner:
            emit(writer_banner)
        return 0

    now = int(started)

    # v1.1.1 F-8 closure: grep the agent-loop's assistant text for any
    # heartbeat sentinel emitted in an intermediate turn and advance
    # LAST_HEARTBEAT before the cadence check. Stop fires only at loop-end,
    # so intermediate-turn sentinels otherwise stay invisible until then,
    # producing repeated false OVERDUE alarms.
    transcript_path = event.get("transcript_path") or ""
    if transcript_path:
        text = agent_loop_assistant_text(transcript_path)
        if text:
            max_min = max_sentinel_minute(text)
            last_hb_min = (last_hb - t0) // 60 if last_hb > 0 else 0
            if max_min > last_hb_min:
                # Fresh sentinel — advance LAST_HEARTBEAT to NOW for
                # consistency with stop.py advance semantics. Preserve
                # LAST_PTU_TAG_SEC; the rate-limit window stays in force.
                last_hb = now
                write_anchor(session_id, {
                    "T0": str(t0),
                    "LAST_HEARTBEAT": str(now),
                    "TIER": tier,
                    "LAST_PTU_TAG_SEC": str(last_ptu_tag),
                })
                append_log({
                    "ts": time.time(),
                    "hook": "post-tool-use",
                    "session_id": session_id,
                    "tier": tier,
                    "tool_name": event.get("tool_name", ""),
                    "diagnostic": "sentinel-grep-advanced",
                    "max_sentinel_min": max_min,
                    "advanced_to_min": (now - t0) // 60,
                    "routine_version": ROUTINE_VERSION,
                })

    elapsed_t0 = now - t0
    last_hb_effective = last_hb if last_hb > 0 else t0
    elapsed_since_hb = now - last_hb_effective

    if elapsed_since_hb < CADENCE_SEC:
        if writer_banner:
            emit(writer_banner)
        return 0

    # Rate-limit: emit at most once per PTU_RATE_LIMIT_SEC. The Stop hook
    # advances LAST_HEARTBEAT when the assistant fires the sentinel; in the
    # healthy path, elapsed_since_hb resets and we no-op naturally. Rate
    # limit is the safety net for the bug case (assistant ignores prompt).
    if last_ptu_tag > 0 and now - last_ptu_tag < PTU_RATE_LIMIT_SEC:
        if writer_banner:
            emit(writer_banner)
        return 0

    elapsed_t0_min = elapsed_t0 // 60
    elapsed_hb_min = elapsed_since_hb // 60
    next_due_min = elapsed_t0_min + max(0, (CADENCE_SEC - elapsed_since_hb) // 60)

    if elapsed_since_hb >= OVERDUE_2X_SEC:
        status = "OVERDUE-2X"
    else:
        status = "OVERDUE"

    last_hb_display = (last_hb - t0) // 60 if last_hb > 0 else 0
    clock_tag = (
        f"[session-clock: T+{elapsed_t0_min}m | "
        f"last-heartbeat: T+{last_hb_display}m | "
        f"next-due: T+{next_due_min}m, {status}] (PostToolUse)"
    )

    if status == "OVERDUE":
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE per D-MET-33 surfaced via PostToolUse "
            "(autonomous-work coverage; no user prompt has fired the cadence "
            "during the in-flight tool sequence). Emit the session-health "
            "heartbeat block at the start of your next reply: 5 substantive "
            "content lines (1: session goal restated; 2: scope status; 3: "
            "anomaly status; 4: side-questions status; 5: background tasks) "
            f"followed by the literal sentinel [heartbeat-fired:T+{elapsed_t0_min}m] "
            "on its own line. Six lines total. Layered fail-safe per "
            "D-MET-41.\n</system-reminder>"
        )
    else:
        ctx = (
            f"<system-reminder>\n{clock_tag}\n\n"
            "Heartbeat OVERDUE-2X per D-MET-33 surfaced via PostToolUse: two "
            "consecutive cadences elapsed without sentinel detection during "
            "an autonomous-work window. Auto-advancing LAST_HEARTBEAT as the "
            "layered fail-safe per D-MET-41. Emit a recovery session-health "
            "heartbeat block at the start of your next reply: 5 substantive "
            "content lines (1: session goal restated; 2: scope status; 3: "
            "anomaly status with explicit acknowledgement of the missed "
            "cadences; 4: side-questions status; 5: background tasks) "
            f"followed by the literal sentinel [heartbeat-fired:T+{elapsed_t0_min}m] "
            "on its own line. Six lines total.\n</system-reminder>"
        )

    if status == "OVERDUE-2X":
        write_anchor(session_id, {
            "T0": str(t0),
            "LAST_HEARTBEAT": str(now),
            "TIER": tier,
            "LAST_PTU_TAG_SEC": str(now),
        })
    else:
        write_anchor(session_id, {
            "T0": str(t0),
            "LAST_HEARTBEAT": str(last_hb),
            "TIER": tier,
            "LAST_PTU_TAG_SEC": str(now),
        })

    # v1.10.0 (F-63-01 Layer 2): if a writer-conflict banner is also pending
    # from the same fire, prepend it so both surface in a single emission
    # rather than two stacked stdout writes (Claude Code merges trailing
    # additionalContext into the next assistant turn; a single write keeps
    # the protocol surface clean).
    if writer_banner:
        ctx = writer_banner + "\n\n" + ctx

    emit(ctx)

    append_log({
        "ts": time.time(),
        "hook": "post-tool-use",
        "session_id": session_id,
        "tier": tier,
        "tool_name": event.get("tool_name", ""),
        "elapsed_t0_min": elapsed_t0_min,
        "elapsed_since_hb_min": elapsed_hb_min,
        "status": status,
        "routine_version": ROUTINE_VERSION,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
