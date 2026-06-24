#!/usr/bin/env python3
"""SessionStart hook for the VC-RoE (Vibe Coding · Rules of Engagement) Claude Code plugin.

Renamed from vc-roe at v1.0.0 (2026-05-06). Hook log filename
preserved as ~/.claude/methodology-hook.log for log-history continuity.

Reads stdin JSON (Claude Code SessionStart event), walks up to the nearest
.git/ within max levels, scores Scope and Criticality, applies override
precedence, returns additionalContext JSON. Logs every run to
~/.claude/methodology-hook.log.

Pure stdlib. No third-party dependencies. Never throws; on any error, logs
and returns 0 with empty context so Claude Code session start is unaffected.

Override precedence (closer-file-wins):
    1. tier: T<N> line in any CLAUDE.md from cwd up to project_root.
    2. .claude/methodology.json {"tier": "T<N>"} at project_root.
    3. CLAUDE_TIER environment variable.
    4. Auto-detected (S, C) -> matrix lookup.
    5. Default T0.

The /tier slash command is precedence 0 (highest) but fires AFTER hook time;
Claude Code itself handles it at runtime, not this hook.

Source basis: tier-model-v2.md section 4, locked at D-MET-1..13.

Documented spec deviations (locked at session 04 audit-pass, D-MET-20..21):

- The S2 size signal in tier-model-v2 section 4 step 6 is an AND between
  file_count > min_file_count and git_age_days > min_git_age_days. The hook
  enforces both: when no .git/ is found at project_root, the size signal
  cannot lift S to S2. Implemented at v0.1.1 (D-MET-20).

- tier-model-v2 section 4 step 8 (msg_delta from oneshot/irreversible-action/
  regulatory keywords matched against first_user_message) is NOT implemented.
  The SessionStart event has no first-user-message access; that scoring is
  architecturally incompatible with this hook contract. The unused keyword
  sets were removed from detection-rules.json at v0.1.1 (F-MET-14, D-MET-23).

- tier-model-v2 section 4 step 11 ambiguity guard (multiple .git/ within 2
  levels of cwd -> ASK_OPERATOR) is NOT implemented. find_git_root walks up
  only and returns the first git boundary it finds. Practical risk on the
  typical project layout (each project being a self-contained git root, not
  a parent-directory holding multiple git roots within 2 levels) is low.
  Revisit if a multi-root case surfaces in soak.
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

# OBS-MET-AI: Windows stdout defaults to cp1252 which cannot encode the U+2192
# arrow embedded in methodology slice content. Without this reconfigure, the
# JSON write below crashes silently and no additionalContext lands. No-op on
# streams that don't support reconfigure (Python <3.7 behaviour, piped streams).
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8", errors="replace")


PLUGIN_ROOT = Path(
    os.environ.get("CLAUDE_PLUGIN_ROOT", str(Path(__file__).resolve().parent.parent))
)
DETECTION_RULES_PATH = PLUGIN_ROOT / "detection-rules.json"
CONTENT_DIR = PLUGIN_ROOT / "methodology-content"
LOG_PATH = Path.home() / ".claude" / "methodology-hook.log"

ROUTINE_VERSION = "1.18.0"
ANCHOR_DIR = Path(tempfile.gettempdir())  # OBS-MET-AK: cross-runtime /tmp divergence on Windows; macOS gap documented in README "Platform coverage notes"
ANCHOR_PREFIX = "claude-methodology-anchor-"
TIER_FLOOR_FILENAME = "methodology-tier-floor"
TIER_FLOOR_PREVIOUS_FILENAME = "methodology-tier-floor.previous"

# v1.3.0: chat-claim primitive (multi-chat-access protection per
# OBS-vcroe-multi-chat-contamination-01). SessionStart acquires; Stop
# refreshes ts per turn; SessionEnd releases; TTL reaps orphans.
# v1.9.0 (F-63-01 Layer 1): adds pid + pid_starttime + boot_id fields for
# liveness-probe-before-refuse. Auto-release on dead-PID, recycled-PID, or
# host-reboot, so a killed chat no longer parks the claim for the full TTL.
# v1.9.0 (F-63-01 Layer 4): adds worktree-aware bypass — claim skipped
# entirely when CWD sits inside a `git worktree` (operator escape valve
# for parallel work).
# v1.10.0 (F-63-01 Layer 2): adds mode + writer_session_id + writer_*_ts
# + writer_idle_demote_seconds fields. SessionStart acquires mode=reader;
# multiple alive-reader chats coexist (no refuse). PostToolUse promotes
# the claim to mode=writer on first imminent write (watched-path edit or
# git-mutation Bash); writer is exclusive. Stop demotes writer→reader
# after writer_idle_demote_seconds of no mutation. Legacy v1.3.0..v1.9.x
# claims (no mode field) preserve exclusive-claim semantics for safety.
CLAIM_FILENAME = "chat-claim.json"
CLAIM_TTL_ENV_VAR = "VC_ROE_CLAIM_TTL_HOURS"
DEFAULT_CLAIM_TTL_HOURS = 8.0
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
MODE_READER = "reader"
MODE_WRITER = "writer"
DEFAULT_WRITER_IDLE_DEMOTE_SECONDS = 1800  # 30 min per s68 operator decision

# v1.4.0: publish-state broadcast (OBS-vcroe-coordination-cron-broadcast-01
# closure). A user crontab is expected to run `bash bin/publish-audit-state.sh
# --json-out PUBLISH_STATE_PATH` periodically; SessionStart reads the JSON
# at session-open and surfaces a one-line `publish_state:` trace so the
# operator sees post-publish leak-state freshness without re-running the
# audit by hand. Read-only; never written by this hook.
PUBLISH_STATE_PATH = Path.home() / ".claude" / "vc-roe-publish-state.json"
PUBLISH_STATE_STALE_MINUTES = 65  # 30 min cadence + 35 min grace


def load_rules() -> dict[str, Any]:
    with open(DETECTION_RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_git_root(cwd: Path, max_levels: int) -> Optional[Path]:
    """Walk up from cwd to the nearest directory containing .git/."""
    cur = cwd.resolve()
    for _ in range(max_levels + 1):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def walk_up_for_file(name: str, start: Path, stop: Path) -> Optional[Path]:
    """Walk from start up to stop (inclusive), return first match for `name`."""
    cur = start.resolve()
    stop_resolved = stop.resolve()
    seen_stop = False
    while True:
        candidate = cur / name
        if candidate.is_file():
            return candidate
        if seen_stop or cur.parent == cur:
            return None
        if cur == stop_resolved:
            seen_stop = True
        cur = cur.parent


def find_tier_in_claude_md(claude_md: Optional[Path]) -> Optional[str]:
    if not claude_md or not claude_md.is_file():
        return None
    try:
        text = claude_md.read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(r"^\s*tier:\s*T([0-4])\b", text, re.MULTILINE | re.IGNORECASE)
    return f"T{m.group(1)}" if m else None


def find_tier_in_methodology_json(project_root: Path) -> Optional[str]:
    cfg_path = project_root / ".claude" / "methodology.json"
    if not cfg_path.is_file():
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    tier = cfg.get("tier")
    if isinstance(tier, str) and re.fullmatch(r"T[0-4]", tier):
        return tier
    return None


def max_scope(a: str, b: str) -> str:
    order = ["S0", "S1", "S2", "S3"]
    return a if order.index(a) >= order.index(b) else b


def max_crit(a: str, b: str) -> str:
    order = ["C0", "C1", "C2"]
    return a if order.index(a) >= order.index(b) else b


def score_scope(project_root: Path, rules: dict[str, Any]) -> tuple[str, list[str]]:
    s = "S0"
    signals: list[str] = []

    s1_signals = rules["scope_signals"].get("S1_signals", [])
    s2_signals = rules["scope_signals"].get("S2_signals", [])
    s3_signals = rules["scope_signals"].get("S3_signals", [])

    for name in s1_signals:
        if (project_root / name).exists():
            s = max_scope(s, "S1")
            signals.append(f"S1:{name}")
            break

    for name in s2_signals:
        if (project_root / name).exists():
            s = max_scope(s, "S2")
            signals.append(f"S2:{name}")

    for name in s3_signals:
        if (project_root / name).exists():
            s = max_scope(s, "S3")
            signals.append(f"S3:{name}")

    size_cfg = rules["scope_signals"].get("S2_size_signals")
    if size_cfg and project_root.is_dir():
        try:
            min_count = size_cfg.get("min_file_count", 999_999_999)
            min_age_days = size_cfg.get("min_git_age_days")
            git_dir = project_root / ".git"

            git_age_required = min_age_days is not None
            if git_age_required and not git_dir.exists():
                pass
            else:
                count = 0
                limit = min_count + 1
                for entry in project_root.rglob("*"):
                    if entry.is_file():
                        count += 1
                        if count >= limit:
                            break
                if count > min_count:
                    age_ok = True
                    age_days_observed: Optional[int] = None
                    if git_age_required:
                        age_seconds = time.time() - git_dir.stat().st_mtime
                        age_days_observed = int(age_seconds // 86400)
                        age_ok = age_seconds > (min_age_days * 86400)
                    if age_ok:
                        s = max_scope(s, "S2")
                        if git_age_required:
                            signals.append(
                                f"S2:file_count>{min_count}+git_age>{min_age_days}d"
                                f"(observed:{age_days_observed}d)"
                            )
                        else:
                            signals.append(f"S2:file_count>{min_count}")
        except Exception:
            pass

    return s, signals


def score_criticality(project_root: Path, rules: dict[str, Any]) -> tuple[str, list[str]]:
    c = "C0"
    signals: list[str] = []

    crit_cfg = rules.get("criticality_signals", {})
    sources = crit_cfg.get("criticality_text_sources", ["CLAUDE.md"])

    text_parts: list[str] = []
    for rel in sources:
        candidate = project_root / rel
        if candidate.is_file():
            try:
                text_parts.append(candidate.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    text = "\n".join(text_parts)

    rk = rules.get("regulatory_keywords", [])
    text_upper = text.upper()
    hits = [kw for kw in rk if kw.upper() in text_upper]
    if hits:
        signals.extend(f"C:{kw}" for kw in hits)

    if len(hits) >= crit_cfg.get("C1_min_regulatory_hits", 1):
        c = max_crit(c, "C1")
    if len(hits) >= crit_cfg.get("C2_min_regulatory_hits", 2):
        c = max_crit(c, "C2")

    for path_name in crit_cfg.get("C2_path_signals", []):
        if (project_root / path_name).exists():
            c = max_crit(c, "C2")
            signals.append(f"C2:{path_name}/")

    return c, signals


def tier_index(t: str) -> int:
    return int(t[1])


def read_tier_floor(cwd: Path) -> Optional[str]:
    # v0.3.0 D-MET-62: project tier floor (sticky across sessions). Marker is
    # written by /tier, /raise-tier, /lower-tier slash commands. SessionStart
    # reads it unconditionally; effective_tier = max(auto, floor) when source
    # is auto-detect; CLAUDE.md sentinel + .claude/methodology.json + env var
    # remain absolute overrides per the design lock at session 13.
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))  # OBS-MET-AJ
    marker = Path.home() / ".claude" / "projects" / cwd_dashed / TIER_FLOOR_FILENAME
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"T[0-4]", text):
            return text
    except Exception:
        pass
    return None


def read_tier_floor_previous(cwd: Path) -> Optional[str]:
    # v0.4.0 OBS-MET-AG closure: previous-session floor recording for drift
    # detection. SessionStart reads this at start, compares to current floor,
    # and emits floor-drift-detected signal if they differ. Then writes the
    # current floor to this file for the next SessionStart. Forensic trail
    # for any explicit --project floor changes by sibling sessions.
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))  # OBS-MET-AJ
    marker = Path.home() / ".claude" / "projects" / cwd_dashed / TIER_FLOOR_PREVIOUS_FILENAME
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"T[0-4]", text):
            return text
    except Exception:
        pass
    return None


def write_tier_floor_previous(cwd: Path, floor: Optional[str]) -> None:
    # Companion to read_tier_floor_previous. Writes current floor as previous
    # for the next SessionStart to compare against. No-op if floor is None
    # (never had a floor; nothing to record).
    if floor is None:
        return
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))  # OBS-MET-AJ
    marker_dir = Path.home() / ".claude" / "projects" / cwd_dashed
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / TIER_FLOOR_PREVIOUS_FILENAME
        marker.write_text(floor + "\n", encoding="utf-8")
    except Exception:
        pass


def write_tier_floor(cwd: Path, floor: str) -> None:
    # v0.5.0 OBS-MET-AH closure: HWM auto-elevation writes the project tier
    # floor when SessionStart's auto_detected > current_floor. Companion to
    # read_tier_floor. Slash-commands also write this file; HWM is the
    # SessionStart-side writer.
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))  # OBS-MET-AJ
    marker_dir = Path.home() / ".claude" / "projects" / cwd_dashed
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / TIER_FLOOR_FILENAME
        marker.write_text(floor + "\n", encoding="utf-8")
    except Exception:
        pass


def detect_tier(cwd: Path, env: dict[str, str], rules: dict[str, Any]) -> dict[str, Any]:
    # v1.4.0 OBS-vcroe-tier-banner-no-scope-when-override-01 closure
    # (reading 1, intentional-design). When tier is set via override
    # (CLAUDE.md sentinel, .claude/methodology.json, or CLAUDE_TIER env),
    # scope and crit are returned as None and surfaced as "n/a" in the
    # trace block and the first-line tier banner. Reporting computed S/C
    # alongside an override would conflate the operator-decided override
    # path with the signal-driven auto-detect path and mislead the reader
    # about which one drove the effective tier. The "n/a" is by design.
    max_levels = rules.get("max_walk_levels", 6)
    git_root = find_git_root(cwd, max_levels)
    project_root = git_root or cwd

    claude_md = walk_up_for_file("CLAUDE.md", cwd, project_root)
    cm_tier = find_tier_in_claude_md(claude_md)
    if cm_tier:
        return {
            "tier": cm_tier,
            "scope": None,
            "crit": None,
            "source": "claude.md",
            "signals": [f"sentinel:{cm_tier}@{claude_md}"],
            "project_root": str(project_root),
            "git_root_found": git_root is not None,
        }

    cfg_tier = find_tier_in_methodology_json(project_root)
    if cfg_tier:
        return {
            "tier": cfg_tier,
            "scope": None,
            "crit": None,
            "source": "claude-config",
            "signals": [f"config:{cfg_tier}"],
            "project_root": str(project_root),
            "git_root_found": git_root is not None,
        }

    env_tier = env.get("CLAUDE_TIER")
    if env_tier and re.fullmatch(r"T[0-4]", env_tier):
        return {
            "tier": env_tier,
            "scope": None,
            "crit": None,
            "source": "env",
            "signals": [f"env:{env_tier}"],
            "project_root": str(project_root),
            "git_root_found": git_root is not None,
        }

    s, s_signals = score_scope(project_root, rules)
    c, c_signals = score_criticality(project_root, rules)

    matrix = rules.get("tier_matrix", {})
    base_tier = matrix.get(s, {}).get(c, "T0")

    ceiling = rules.get("auto_promotion_ceiling", "T3")
    if tier_index(base_tier) > tier_index(ceiling):
        base_tier = ceiling

    return {
        "tier": base_tier,
        "scope": s,
        "crit": c,
        "source": "auto",
        "signals": s_signals + c_signals,
        "project_root": str(project_root),
        "git_root_found": git_root is not None,
    }


def label_for(s: Optional[str], c: Optional[str], rules: dict[str, Any]) -> str:
    if not s or not c:
        return "override"
    return rules.get("labels", {}).get(f"{s}_{c}", f"{s}/{c}")


def load_slice(tier: str) -> str:
    path = CONTENT_DIR / f"{tier}.md"
    if not path.is_file():
        return f"# Methodology slice for {tier} not found at {path}"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        return f"# Methodology slice for {tier} could not be read: {e}"


def append_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def claim_path(cwd: Path) -> Path:
    cwd_dashed = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))  # OBS-MET-AJ
    return Path.home() / ".claude" / "projects" / cwd_dashed / CLAIM_FILENAME


def claim_ttl_hours() -> float:
    raw = os.environ.get(CLAIM_TTL_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_CLAIM_TTL_HOURS
    try:
        v = float(raw)
        if v <= 0:
            return DEFAULT_CLAIM_TTL_HOURS
        return v
    except ValueError:
        return DEFAULT_CLAIM_TTL_HOURS


def read_claim(cwd: Path) -> Optional[dict[str, Any]]:
    path = claim_path(cwd)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_claim(cwd: Path, session_id: str, ts: int, host: str,
                pid: int, pid_starttime: Optional[str], boot_id: str,
                mode: str = MODE_READER,
                writer_session_id: Optional[str] = None,
                writer_acquired_ts: Optional[int] = None,
                writer_last_mutation_ts: Optional[int] = None,
                writer_idle_demote_seconds: int = DEFAULT_WRITER_IDLE_DEMOTE_SECONDS,
                ) -> bool:
    """Write the chat-claim JSON.

    v1.9.0: schema gains pid + pid_starttime + boot_id for the liveness probe.
    pid_starttime may be None on platforms where it can't be read (macOS);
    in that case the field is stored as null and evaluate_claim falls back to
    the simpler is_pid_alive probe.

    v1.10.0 (F-63-01 Layer 2): schema gains mode + writer_session_id +
    writer_acquired_ts + writer_last_mutation_ts + writer_idle_demote_seconds.
    SessionStart writes mode=reader with writer_* fields null. PostToolUse
    promotes to mode=writer on first watched-path edit or git-mutation Bash.
    Stop demotes writer→reader after writer_idle_demote_seconds idle.
    """
    path = claim_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "ts": ts,
            "host": host,
            "pid": pid,
            "pid_starttime": pid_starttime,
            "boot_id": boot_id,
            "mode": mode,
            "writer_session_id": writer_session_id,
            "writer_acquired_ts": writer_acquired_ts,
            "writer_last_mutation_ts": writer_last_mutation_ts,
            "writer_idle_demote_seconds": writer_idle_demote_seconds,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.write("\n")
        return True
    except Exception:
        return False


def read_boot_id() -> str:
    """v1.9.0: stable per-boot identifier for the host.

    Linux: contents of /proc/sys/kernel/random/boot_id (UUID, stable for the
    lifetime of the kernel boot).
    macOS / Windows / read-error: empty string. Caller treats "" as "no
    boot_id comparison possible" and falls back to the pid-starttime path
    or to TTL-only evaluation.
    """
    try:
        if BOOT_ID_PATH.is_file():
            return BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def read_pid_starttime(pid: int) -> Optional[str]:
    """v1.9.0: per-process creation marker. Combined with pid, uniquely
    identifies a specific process across PID recycling and reboots.

    Linux: /proc/<pid>/stat field 22 (starttime in clock ticks since boot).
    Windows: GetProcessTimes() creation FILETIME via ctypes (64-bit).
    macOS / other / probe-error: None — caller falls back to is_pid_alive.
    """
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
                    h,
                    ctypes.byref(creation),
                    ctypes.byref(exit_t),
                    ctypes.byref(kernel_t),
                    ctypes.byref(user_t),
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
    """v1.9.0: True if PID currently exists on this host; False if dead;
    None if probe is not possible (returned only on Windows probe errors).

    POSIX (Linux, macOS): os.kill(pid, 0). EPERM means the PID exists but
    we cannot signal it — treat as alive.
    Windows: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetExitCodeProcess
    via ctypes; ERROR_ACCESS_DENIED (5) on OpenProcess means PID exists but is
    inaccessible (e.g. system process) — treat as alive.
    """
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


def is_in_git_worktree(cwd: Path) -> bool:
    """v1.9.0 (F-63-01 Layer 4): True if cwd sits inside a linked git
    worktree, False otherwise.

    Detection: `git rev-parse --git-common-dir` vs `--git-dir`. In a linked
    worktree, --git-dir resolves to <main>/.git/worktrees/<name> while
    --git-common-dir resolves to <main>/.git; they differ. In a main working
    tree, in a submodule, or in a non-git directory, the two resolve equal
    (or `git rev-parse` returns rc != 0); we return False.
    """
    try:
        common = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if common.returncode != 0:
            return False
        gitdir = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if gitdir.returncode != 0:
            return False
        try:
            common_p = (cwd / common.stdout.strip()).resolve()
            gitdir_p = (cwd / gitdir.stdout.strip()).resolve()
        except Exception:
            return False
        return common_p != gitdir_p
    except Exception:
        return False


def evaluate_claim(claim: Optional[dict[str, Any]], current_session: str,
                   now: int, ttl_hours: float, our_host: str,
                   our_boot_id: str) -> tuple[str, dict[str, Any]]:
    """Decide what to do with an existing chat-claim.

    Returns (action, info) where action is one of:
        "take-new"        no existing claim; acquire fresh.
        "resume"          claim's session_id matches current; refresh ts.
        "take-orphan"     claim exists but TTL-expired, corrupt, or liveness
                          probe (v1.9.0) shows the owning process is dead /
                          host rebooted / PID recycled; take over.
        "refuse"          claim exists, valid, owned by another live writer
                          (mode=writer) or a legacy-shape claim within TTL;
                          refuse this session and surface conflict.
        "reader-coexist"  v1.10.0 Layer 2: claim is mode=reader, owned by
                          another live session within TTL; coexist as
                          implicit-reader (do NOT overwrite the existing
                          reader claim; multiple alive-readers per project
                          is the new allowed shape).

    v1.9.0 (F-63-01 Layer 1): the liveness probe runs before the TTL check.
    If host matches and the owning PID is known-dead, or boot_id mismatches
    (host rebooted since the claim was written), we take-orphan immediately
    rather than waiting out the full TTL.

    v1.10.0 (F-63-01 Layer 2): the reader-coexist branch runs AFTER the
    liveness probe and BEFORE the legacy TTL-refuse path. Only mode=reader
    claims coexist; mode=writer claims and legacy v1.3.0..v1.9.x no-mode
    claims still hit the refuse path (exclusive-claim semantics preserved
    for both the new writer-lease and the legacy compat surface).
    """
    if not claim or not isinstance(claim, dict):
        return ("take-new", {"reason": "no-existing-claim"})
    other_session = str(claim.get("session_id", "") or "")
    other_host = str(claim.get("host", "?") or "?")
    other_ts_raw = claim.get("ts", 0)
    try:
        other_ts = int(other_ts_raw)
    except (TypeError, ValueError):
        return ("take-orphan", {
            "reason": "corrupt-ts",
            "other_session": other_session,
            "other_host": other_host,
        })
    info: dict[str, Any] = {
        "other_session": other_session,
        "other_host": other_host,
        "other_ts": other_ts,
    }
    if other_session == current_session:
        info["reason"] = "same-session-resume"
        return ("resume", info)

    # v1.9.0 Layer 1: liveness probe before refuse. Two probe paths:
    #
    #   (a) boot_id mismatch: claim's host matches ours AND both sides have
    #       a non-empty boot_id AND they differ -> host rebooted since the
    #       claim was written -> owner cannot possibly be alive.
    #
    #   (b) pid liveness: claim's host matches ours AND claim has a pid.
    #       If pid_starttime is present on both sides, compare directly
    #       (catches PID recycling). Otherwise fall back to is_pid_alive.
    #
    # Either probe path returning a definitive dead-owner signal yields
    # take-orphan. Indeterminate results (unknown host, missing fields,
    # probe error) fall through to the existing TTL evaluation so legacy
    # v1.3.0..v1.8.1 claims and cross-host claims keep their TTL semantics.
    other_pid_raw = claim.get("pid", None)
    other_pid_starttime = claim.get("pid_starttime", None)
    other_boot_id = str(claim.get("boot_id", "") or "")
    same_host = bool(other_host and other_host != "?" and other_host == our_host)

    if same_host and our_boot_id and other_boot_id and our_boot_id != other_boot_id:
        info["reason"] = "boot-id-mismatch"
        return ("take-orphan", info)

    if same_host and other_pid_raw is not None:
        try:
            other_pid = int(other_pid_raw)
        except (TypeError, ValueError):
            other_pid = None
        if other_pid is not None and other_pid > 0:
            if other_pid_starttime is not None:
                current_starttime = read_pid_starttime(other_pid)
                if current_starttime is None:
                    info["reason"] = "pid-dead"
                    return ("take-orphan", info)
                if str(current_starttime) != str(other_pid_starttime):
                    info["reason"] = "pid-recycled"
                    return ("take-orphan", info)
                # Same pid + same starttime: owner alive; fall through to TTL.
            else:
                alive = is_pid_alive(other_pid)
                if alive is False:
                    info["reason"] = "pid-dead"
                    return ("take-orphan", info)
                # alive is True or None (probe inconclusive): fall through.

    age_seconds = max(0, now - other_ts)
    ttl_seconds = int(ttl_hours * 3600)
    info["age_seconds"] = age_seconds
    info["ttl_seconds"] = ttl_seconds
    if age_seconds >= ttl_seconds:
        info["reason"] = "ttl-expired"
        return ("take-orphan", info)

    # v1.10.0 (F-63-01 Layer 2): reader-coexist branch. If the existing
    # claim is mode=reader and we got here (liveness probe did not
    # auto-orphan + within TTL), the owning chat is alive-reader. Coexist
    # as implicit-reader rather than refuse. The on-disk claim is left
    # untouched; our session has no persistent presence (intentional —
    # reader identity is not regulator-presentable evidence, so we save
    # the file-write and the second-reader's write would clobber the first
    # reader's pid/boot_id/ts anyway, breaking the first reader's
    # liveness probe surface for any later writer-promotion attempt).
    # Legacy claims (v1.3.0..v1.9.x, no mode field) DO NOT match here —
    # they fall through to the refuse path to preserve v1.9.x semantics
    # for sessions that pre-date the Layer 2 rollout.
    existing_mode = str(claim.get("mode", "") or "").strip()
    if existing_mode == MODE_READER:
        info["existing_mode"] = MODE_READER
        info["reason"] = "reader-coexist"
        return ("reader-coexist", info)

    info["reason"] = "active-claim-conflict"
    info["existing_mode"] = existing_mode or "legacy"
    return ("refuse", info)


def claim_refuse_banner(info: dict[str, Any], ttl_hours: float,
                        cwd: Path) -> str:
    other = info.get("other_session", "?")
    other_short = (other[:8] + "...") if isinstance(other, str) and len(other) > 12 else other
    other_host = info.get("other_host", "?")
    other_ts = info.get("other_ts", 0)
    age_s = int(info.get("age_seconds", 0))
    age_h = age_s / 3600.0
    file_path = claim_path(cwd)
    return (
        "## CHAT-CLAIM CONFLICT (top-priority instruction)\n\n"
        "Another Claude Code session holds an active chat-claim on this "
        f"project. The claim is {age_h:.2f}h old; TTL is {ttl_hours:.1f}h.\n\n"
        f"- Owning session_id: `{other_short}` (host `{other_host}`, "
        f"ts {other_ts})\n"
        f"- Claim file: `{file_path}`\n"
        f"- TTL override env var: `{CLAIM_TTL_ENV_VAR}` "
        f"(currently {ttl_hours:.1f}h)\n\n"
        "**You MUST**:\n"
        "1. Output the literal first-line tier banner per the methodology "
        "slice below (format compliance is preserved).\n"
        "2. Then surface this CHAT-CLAIM CONFLICT to the operator as your "
        "first prose response. Cite the owning session_id, host, and age.\n"
        "3. Halt all file modifications, all git mutations, and all "
        "package operations within this project root. Read-only "
        "investigation (`git status`, `git log`, file reads) is "
        "permitted to characterise current state, but treat the working "
        "tree as a shared resource you do not own.\n"
        "4. Suggest the operator either: (a) close the owning chat (its "
        "SessionEnd hook will release the claim cleanly), (b) wait for "
        "TTL to expire and reopen this session, OR (c) manually delete "
        f"the claim file at `{file_path}` if the owning chat is known "
        "to be dead (process killed, host rebooted).\n\n"
        "---\n\n"
    )


def read_publish_state() -> Optional[dict[str, Any]]:
    """v1.4.0: read the cron-written publish-audit-state JSON broadcast.

    Returns the parsed dict, or None if the file is absent / unreadable /
    malformed. Caller decides freshness via PUBLISH_STATE_STALE_MINUTES.
    """
    try:
        if not PUBLISH_STATE_PATH.is_file():
            return None
        with open(PUBLISH_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def format_publish_state(state: Optional[dict[str, Any]], now: int) -> str:
    """Render a one-line publish-state trace.

    Possible shapes:
      "absent (cron not configured; see CHANGELOG v1.4.0)"
      "corrupt (<reason>)"
      "stale (last broadcast T-<N>m, threshold <T>m)"
      "DENY hits=<K> as of T-<N>m (<W> WARN, history <state>)"
      "history-dirty as of T-<N>m (<W> WARN, HEAD clean)"
      "clean as of T-<N>m (<W> WARN, history clean)"
    """
    if not state or not isinstance(state, dict):
        return "absent (cron not configured; see CHANGELOG v1.4.0)"
    ts_raw = state.get("ts", None)
    if ts_raw is None:
        return "corrupt (ts missing)"
    try:
        ts_int = int(ts_raw)
    except (TypeError, ValueError):
        return "corrupt (ts not int)"
    age_seconds = max(0, now - ts_int)
    age_min = age_seconds // 60
    if age_min > PUBLISH_STATE_STALE_MINUTES:
        return f"stale (last broadcast T-{age_min}m, threshold {PUBLISH_STATE_STALE_MINUTES}m)"
    deny_raw = state.get("deny_count", None)
    warn_raw = state.get("warn_count", None)
    hist_clean = state.get("history_walk_clean", None)
    try:
        deny = int(deny_raw) if deny_raw is not None else None
    except (TypeError, ValueError):
        deny = None
    try:
        warn = int(warn_raw) if warn_raw is not None else None
    except (TypeError, ValueError):
        warn = None
    if deny is None or warn is None:
        return "corrupt (counts missing)"
    if deny > 0:
        hist_txt = "history clean" if hist_clean is True else (
            "history dirty" if hist_clean is False else "history unknown"
        )
        return f"DENY hits={deny} as of T-{age_min}m ({warn} WARN, {hist_txt})"
    if hist_clean is False:
        return f"history-dirty as of T-{age_min}m ({warn} WARN, HEAD clean)"
    return f"clean as of T-{age_min}m ({warn} WARN, history clean)"


def write_anchor_if_missing(session_id: Optional[str], t0_epoch: int, tier: str) -> None:
    """Heartbeat-enforcement anchor (D-MET-39, v0.1.7).

    Writes /tmp/claude-methodology-anchor-<session_id> with T0, LAST_HEARTBEAT,
    and TIER on first SessionStart for the session. Idempotent across
    resume/compact: existing anchor is preserved so wall-clock stays anchored
    to the original session-open epoch.

    The companion UserPromptSubmit hook reads this file every turn to compute
    elapsed-since-T0 and elapsed-since-LAST_HEARTBEAT; the Stop hook greps the
    transcript for the heartbeat sentinel and updates LAST_HEARTBEAT.
    """
    if not session_id:
        return
    try:
        path = ANCHOR_DIR / f"{ANCHOR_PREFIX}{session_id}"
        if path.exists():
            return
        path.write_text(
            f"T0={t0_epoch}\nLAST_HEARTBEAT=0\nTIER={tier}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def main() -> int:
    started = time.time()

    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        append_log({"ts": time.time(), "phase": "stdin_parse", "error": str(e)})
        return 0

    cwd_raw = event.get("cwd") or os.getcwd()
    cwd = Path(cwd_raw)
    env = dict(os.environ)

    try:
        rules = load_rules()
    except Exception as e:
        append_log({"ts": time.time(), "phase": "load_rules", "error": str(e)})
        return 0

    try:
        detection = detect_tier(cwd, env, rules)
    except Exception as e:
        append_log({
            "ts": time.time(),
            "phase": "detect",
            "error": str(e),
            "cwd": str(cwd),
        })
        return 0

    # v0.3.0 D-MET-62: apply project tier floor when tier was auto-detected.
    # CLAUDE.md sentinel / .claude/methodology.json / CLAUDE_TIER env remain
    # absolute overrides per the design lock; they bypass the floor.
    # v0.5.0 OBS-MET-AH closure: HWM auto-elevation. When auto_detected exceeds
    # the current floor (or no floor exists yet), lift the floor automatically
    # to match auto_detected and persist it across future sessions. The floor
    # can only be lifted to a tier the auto-routine itself returned, so HWM
    # never bypasses the T3 auto-promotion ceiling per D-MET-29; T4 still
    # requires explicit operator action per §3.5.
    current_floor = read_tier_floor(cwd)
    if detection["source"] == "auto":
        if current_floor and tier_index(current_floor) > tier_index(detection["tier"]):
            # Floor wins (current floor is higher than what auto-detected).
            detection["tier"] = current_floor
            detection["source"] = "auto+floor"
            detection["signals"].append(f"floor:{current_floor}")
        else:
            # HWM auto-elevation: auto >= floor (or no floor). Lift floor up to
            # auto_detected and persist. Effective tier is auto_detected.
            new_floor = detection["tier"]
            if current_floor != new_floor:
                write_tier_floor(cwd, new_floor)
                detection["signals"].append(f"floor:{new_floor}(hwm)")
                current_floor = new_floor
            else:
                detection["signals"].append(f"floor:{new_floor}")

    # v0.4.0 OBS-MET-AG closure: floor-drift detection. Compare current floor
    # against the recorded previous floor; emit drift signal if they differ.
    # Forensic trail for any explicit --project floor changes by sibling
    # sessions in the same cwd, OR HWM auto-elevation from a previous
    # SessionStart, OR operator-initiated runtime ASK answered project-scope.
    # Recording-only; does not alter the effective tier (drift may go either
    # direction; the tier in force is whatever current_floor + auto-detect
    # already produced above).
    previous_floor = read_tier_floor_previous(cwd)
    if current_floor and previous_floor and current_floor != previous_floor:
        detection["signals"].append(
            f"floor-drift-detected:{previous_floor}->{current_floor}"
        )
    write_tier_floor_previous(cwd, current_floor)

    tier = detection["tier"]
    s = detection.get("scope")
    c = detection.get("crit")
    source = detection["source"]
    label = label_for(s, c, rules)
    slice_content = load_slice(tier)

    # Optional machine-local SessionStart addon (extension point). An install
    # that carries a private extension module at ~/.claude/vc-roe-addons may
    # contribute an extra context block (e.g. infrastructure-specific session
    # context) and a one-word trace state. Plain public installs have no such
    # directory and skip this entirely. Fully fail-soft: any import or runtime
    # issue degrades to an empty block and never affects session start.
    addon_block, addon_state = "", "none"
    try:
        _addon_dir = Path.home() / ".claude" / "vc-roe-addons"
        if _addon_dir.is_dir():
            if str(_addon_dir) not in sys.path:
                sys.path.insert(0, str(_addon_dir))
            from vc_roe_local_addons import session_start_block  # type: ignore
            addon_block, addon_state = session_start_block(detection, tier)
    except Exception:
        addon_block, addon_state = "", "error"

    # v1.3.0: chat-claim acquire (multi-chat-access protection per
    # OBS-vcroe-multi-chat-contamination-01). SessionStart writes a claim
    # file to the project memory dir; if a conflicting claim younger than
    # CLAIM_TTL_HOURS exists, prepend a refusal banner to additionalContext
    # so the assistant halts mutations on the working tree. Stop refreshes
    # the claim ts (keeps it alive during active use); SessionEnd deletes
    # the claim (clean release on chat close); TTL reaps orphans.
    #
    # v1.9.0 (F-63-01): Layer 1 (liveness probe + boot_id) lets evaluate_claim
    # auto-release on dead-PID, recycled-PID, or host-reboot, so a killed
    # chat no longer parks the claim for the full TTL. Layer 4 short-circuits
    # the whole acquire path when cwd is inside a git worktree — explicit
    # parallel-work escape valve, composes with EnterWorktree agent isolation.
    #
    # v1.10.0 (F-63-01 Layer 2): SessionStart acquires mode=reader (multiple
    # alive-reader chats coexist on the same project). When evaluate_claim
    # returns "reader-coexist", we DO NOT overwrite the existing reader
    # claim — we operate as an implicit-reader without persistent file
    # presence. Writer-promotion happens later in PostToolUse on first
    # imminent write.
    session_id = str(event.get("session_id") or "")
    ttl_hours = claim_ttl_hours()
    claim_action = "no-session-id"
    claim_info: dict[str, Any] = {}
    claim_banner_text = ""
    if session_id:
        if is_in_git_worktree(cwd):
            claim_action = "bypass-worktree"
            claim_info = {"reason": "cwd-inside-git-worktree"}
        else:
            try:
                host = socket.gethostname()
            except Exception:
                host = "?"
            our_boot_id = read_boot_id()
            our_pid = os.getpid()
            our_pid_starttime = read_pid_starttime(our_pid)
            existing_claim = read_claim(cwd)
            claim_action, claim_info = evaluate_claim(
                existing_claim, session_id, int(started), ttl_hours,
                host, our_boot_id,
            )
            if claim_action == "refuse":
                claim_banner_text = claim_refuse_banner(claim_info, ttl_hours, cwd)
            elif claim_action == "reader-coexist":
                # v1.10.0 Layer 2: do not write. Existing reader claim
                # remains authoritative; our session is an implicit-reader.
                pass
            else:
                # take-new / resume / take-orphan: write fresh reader claim.
                write_claim(
                    cwd, session_id, int(started), host,
                    our_pid, our_pid_starttime, our_boot_id,
                    mode=MODE_READER,
                )

    sc_trace = f"({s}/{c})" if s and c else "(override)"
    # v1.4.0: publish-state broadcast read (OBS-vcroe-coordination-cron-broadcast-01).
    publish_state = read_publish_state()
    publish_state_line = format_publish_state(publish_state, int(started))
    additional = (
        f"{claim_banner_text}"
        f"## Methodology in force: {tier} {sc_trace}\n\n"
        f"{slice_content}\n\n"
        f"{addon_block}"
        f"## Tier detection trace\n"
        f"- effective_tier: {tier}\n"
        f"- scope: {s or 'n/a'}\n"
        f"- criticality: {c or 'n/a'}\n"
        f"- label: {label}\n"
        f"- source: {source}\n"
        f"- signals: {detection.get('signals', [])}\n"
        f"- project_root: {detection.get('project_root', str(cwd))}\n"
        f"- git_root_found: {detection.get('git_root_found', False)}\n"
        f"- routine_version: {ROUTINE_VERSION}\n"
        f"- chat_claim_action: {claim_action}\n"
        f"- chat_claim_ttl_hours: {ttl_hours}\n"
        f"- publish_state: {publish_state_line}\n"
        f"- session_start_addon: {addon_state}\n"
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional,
        }
    }

    try:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception as e:
        append_log({"ts": time.time(), "phase": "stdout_write", "error": str(e)})
        return 0

    write_anchor_if_missing(event.get("session_id"), int(started), tier)

    append_log({
        "ts": time.time(),
        "duration_ms": int((time.time() - started) * 1000),
        "session_id": event.get("session_id"),
        "source_event": event.get("source"),
        "cwd": str(cwd),
        "tier": tier,
        "scope": s,
        "criticality": c,
        "tier_source": source,
        "label": label,
        "signals": detection.get("signals", []),
        "project_root": detection.get("project_root", str(cwd)),
        "git_root_found": detection.get("git_root_found", False),
        "routine_version": ROUTINE_VERSION,
        "chat_claim_action": claim_action,
        "chat_claim_info": claim_info,
        "chat_claim_ttl_hours": ttl_hours,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
