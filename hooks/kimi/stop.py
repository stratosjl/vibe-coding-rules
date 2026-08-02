#!/usr/bin/env python3
"""Kimi Code Stop adapter for vc-roe: heartbeat clock, informational by default.

Kimi's Stop event is blockable (exit 2 + stderr lets the model continue the
turn), so instead of the Claude side's transcript sentinel-grep (Kimi Stop
payloads carry no verified transcript path), an overdue heartbeat USED TO
block the turn end with an explicit instruction. Consecutive blocks were
capped at MAX_BLOCKS; the cap allow trust-advanced LAST_HEARTBEAT.

v1.20.0 heartbeat demotion (I-9): that block no longer fires by default. An
overdue cadence advances LAST_HEARTBEAT quietly and allows the turn to end.
The blocking path is kept intact behind HEARTBEAT_ENFORCEMENT, so setting
VC_ROE_HEARTBEAT_ENFORCEMENT=1 (or flipping the default constant) restores
the previous behaviour byte for byte, cap and trust-advance included.
Evidence for the demotion is in hooks/user-prompt-submit.py and the v1.20.0
CHANGELOG entry.

Note for the enforcement path: the Kimi heartbeat has no compliance channel
(no transcript access), so a compliant model is still blocked up to
MAX_BLOCKS per cycle before the trust-advance moves LAST_HEARTBEAT. This is
intentional; do not "fix" it.

Also refreshes the chat-claim ts, mirroring the Claude stop.py side effect.

Pure stdlib. Never throws; exit 0 (allow) on any error.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _adapter as A  # noqa: E402

CADENCE_SEC = 30 * 60  # I-9: 30 min, up from 15 at v1.20.0
MAX_BLOCKS = 2
TIER_ACTIVE = {"T2", "T3", "T4"}

# I-9 reversibility switch (v1.20.0), same name and semantics as the Claude
# hooks. False = never block a turn end on the heartbeat.
HEARTBEAT_ENFORCEMENT_DEFAULT = False
HEARTBEAT_ENFORCEMENT = os.environ.get(
    "VC_ROE_HEARTBEAT_ENFORCEMENT",
    "1" if HEARTBEAT_ENFORCEMENT_DEFAULT else "0",
).strip().lower() in {"1", "true", "yes", "on"}

INSTRUCTION = (
    "vc-roe heartbeat due ({elapsed}m since session start, {cadence}m cadence, "
    "D-MET-33). Before ending this turn, emit the session-health heartbeat "
    "block: 5 substantive content lines (1: session goal restated; 2: scope "
    "status; 3: anomaly status; 4: side-questions status; 5: background "
    "tasks) followed by the literal sentinel [heartbeat-fired:T+{elapsed}m] "
    "on its own line. Six lines total."
)


def main() -> int:
    started = time.time()
    try:
        event = A.read_event()
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-stop", "phase": "stdin", "error": str(e)})
        return 0
    session_id = str(event.get("session_id") or "")
    if not session_id:
        return 0
    try:
        ups = A.load_hook_module("user-prompt-submit")
        try:  # claim refresh parity with Claude stop.py; side effect only
            A.load_hook_module("stop").refresh_claim(session_id, event.get("cwd"), int(started))
        except Exception:
            pass
        anchor = ups.read_anchor(session_id)
        if not anchor:
            return 0
        if anchor.get("TIER", "T0") not in TIER_ACTIVE:
            return 0
        t0 = int(anchor.get("T0", "0"))
        last_hb = int(anchor.get("LAST_HEARTBEAT", "0"))
        blocks = int(anchor.get("STOP_BLOCKS", "0"))
    except Exception as e:
        A.append_log({"ts": started, "hook": "kimi-stop", "phase": "anchor", "error": str(e)})
        return 0
    if t0 <= 0:
        return 0
    now = int(started)
    last_hb_effective = last_hb if last_hb > 0 else t0
    if now - last_hb_effective < CADENCE_SEC:
        if blocks:
            ups.write_anchor(session_id, {**anchor, "STOP_BLOCKS": "0"})
        return 0
    if not HEARTBEAT_ENFORCEMENT:
        # I-9: the cadence has elapsed, which is a clock reading and not a
        # violation. Advance quietly and let the turn end.
        ups.write_anchor(session_id, {**anchor, "STOP_BLOCKS": "0", "LAST_HEARTBEAT": str(now)})
        A.append_log({"ts": started, "hook": "kimi-stop", "session_id": session_id,
                      "blocked": False, "heartbeat_advanced": "informational",
                      "enforcement": "informational"})
        return 0
    if now - last_hb_effective >= 2 * CADENCE_SEC:
        # Time-based escape mirroring user-prompt-submit's OVERDUE_2X: a lapse
        # of two cadences or more force-advances LAST_HEARTBEAT, so a session
        # whose anchor writes keep failing cannot be blocked on every Stop.
        ups.write_anchor(session_id, {**anchor, "STOP_BLOCKS": "0", "LAST_HEARTBEAT": str(now)})
        A.append_log({"ts": started, "hook": "kimi-stop", "session_id": session_id,
                      "blocked": False, "heartbeat_advanced": "trust-2x"})
        return 0
    if blocks < MAX_BLOCKS:
        ups.write_anchor(session_id, {**anchor, "STOP_BLOCKS": str(blocks + 1)})
        A.append_log({"ts": started, "hook": "kimi-stop", "session_id": session_id,
                      "blocked": True, "stop_blocks": blocks + 1})
        return A.block(INSTRUCTION.format(elapsed=(now - t0) // 60,
                                          cadence=CADENCE_SEC // 60))
    ups.write_anchor(session_id, {**anchor, "STOP_BLOCKS": "0", "LAST_HEARTBEAT": str(now)})
    A.append_log({"ts": started, "hook": "kimi-stop", "session_id": session_id,
                  "blocked": False, "heartbeat_advanced": "trust"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
