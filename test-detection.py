#!/usr/bin/env python3
"""Validation runner for the vc-roe SessionStart hook.

Generates synthetic project fixtures under a private vc-roe-fixtures-*
tempdir that exercise each scope x criticality combination, invokes the
hook with a synthetic SessionStart event for each fixture, parses the
additionalContext output, and prints an inferred-vs-expected table.

Each case asserts the whole (tier, scope, criticality) triple. Asserting
the tier alone is not enough: the tier matrix is many-to-one, so a fixture
can move between scopes with its tier unchanged and the runner reports OK.

Every hook subprocess runs against a throwaway HOME and TMP inside that
same tempdir, so the suite writes nothing outside it and one rmtree is the
whole teardown.

Cleans up its fixtures on exit.

Usage:
    python3 test-detection.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent
HOOK = PLUGIN_ROOT / "hooks" / "session-start.py"


def make_fixture(parent: Path, name: str, files: dict[str, str], with_git: bool = True) -> Path:
    fix = parent / name
    fix.mkdir(parents=True, exist_ok=True)
    if with_git:
        (fix / ".git").mkdir(exist_ok=True)
    for rel, content in files.items():
        target = fix / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return fix


def build_fixtures(parent: Path) -> list[dict[str, Any]]:
    """Return a list of {cwd, expected_tier, expected_scope, expected_crit, note} cases.

    Every fixture is fully synthetic so the suite is reproducible on any
    machine and self-contained inside the supplied parent dir.

    expected_scope / expected_crit are asserted, not merely displayed
    (main() compares the triple). They carry "n/a" for the three sentinel
    cases, which is what parse_additional yields when a CLAUDE.md or
    AGENTS.md tier sentinel overrides auto-detection and the trace prints no
    scope or criticality. Before this was asserted, the t3-reg case had been
    running S1/C2 for its whole life while its own note claimed S0 + C2: the
    tier matrix sends both pairs to T3, so the tier alone could not tell them
    apart and the suite printed OK over an untested path."""
    cases = []

    # T0: empty dir, no signals.
    fix0 = make_fixture(parent, "t0-empty", {}, with_git=False)
    cases.append({"cwd": str(fix0), "expected_tier": "T0", "expected_scope": "S0",
                  "expected_crit": "C0", "note": "empty dir, no signals"})

    # T1: a git-tracked single bash script. S1 (.git) + C0.
    fix1 = make_fixture(parent, "t1-bash", {"script.sh": "#!/bin/bash\necho hello\n"})
    cases.append({"cwd": str(fix1), "expected_tier": "T1", "expected_scope": "S1",
                  "expected_crit": "C0",
                  "note": "git tracked, no docs/decisions, no regulatory text"})

    # T2: small project with decisions.md + handovers/. S2 + C0.
    fix2 = make_fixture(
        parent,
        "t2-small",
        {
            "docs/decisions.md": "| D-1 | LOCKED | example decision |\n",
            "docs/handovers/session-01.md": "first session\n",
            "CLAUDE.md": "Personal-blog rebuild project. Bilingual content site.\n",
        },
    )
    cases.append({"cwd": str(fix2), "expected_tier": "T2", "expected_scope": "S2",
                  "expected_crit": "C0", "note": "S2 (docs/decisions + handovers) + C0"})

    # T3 via S3: BUILD_LOG.md + openapi.yaml at root → S3 + C0 → T3.
    fix3a = make_fixture(
        parent,
        "t3-arch",
        {
            "BUILD_LOG.md": "# Build log\n",
            "openapi.yaml": "openapi: 3.0.0\n",
            "CLAUDE.md": "Generic architecture-grade service.\n",
        },
    )
    cases.append({"cwd": str(fix3a), "expected_tier": "T3", "expected_scope": "S3",
                  "expected_crit": "C0", "note": "S3 (BUILD_LOG + openapi) + C0"})

    # T3 via C2: regulatory keyword density in CLAUDE.md → C2.
    # with_git=False is load-bearing: make_fixture defaults to creating .git,
    # which is the S1 signal, so this case ran S1/C2 rather than the S0/C2 its
    # note names. Both pairs map to T3, so only the asserted scope catches it.
    fix3b = make_fixture(
        parent,
        "t3-reg",
        {
            "CLAUDE.md": "Project subject to GDPR Art. 25 and DORA Art. 16.\n",
        },
        with_git=False,
    )
    cases.append({"cwd": str(fix3b), "expected_tier": "T3", "expected_scope": "S0",
                  "expected_crit": "C2",
                  "note": "S0 + C2 (≥2 regulatory keywords) → T3 from matrix"})

    # v1.13.0: T4 via CLAUDE.md sentinel in YAML frontmatter (1b portability fix).
    # Verifies find_tier_in_claude_md(session-start.py:150) picks up the sentinel
    # written by bin/claude-md-sentinel.py, with absolute precedence over auto.
    fix_sentinel = make_fixture(
        parent,
        "v113-sentinel-frontmatter",
        {
            "CLAUDE.md": "---\ntier: T4\n---\n\n# Project\n\nMinimal content.\n",
        },
    )
    cases.append({
        "cwd": str(fix_sentinel),
        "expected_tier": "T4",
        "expected_scope": "n/a",
        "expected_crit": "n/a",
        "note": "v1.13.0: YAML frontmatter `tier: T4` sentinel overrides auto (1b)",
    })

    # v1.13.0: T4 via CLAUDE.md sentinel as bare legacy line (no frontmatter).
    # Verifies the reader's permissive regex (^\s*tier:\s*T([0-4])\b) matches
    # legacy CLAUDE.md formats too.
    fix_sentinel_bare = make_fixture(
        parent,
        "v113-sentinel-bare",
        {
            "CLAUDE.md": "# Project\n\ntier: T4\n\nSome body content.\n",
        },
    )
    cases.append({
        "cwd": str(fix_sentinel_bare),
        "expected_tier": "T4",
        "expected_scope": "n/a",
        "expected_crit": "n/a",
        "note": "v1.13.0: bare legacy `tier: T4` line also recognised as sentinel (backwards-compat)",
    })

    # AGENTS.md criticality: regulatory keyword in AGENTS.md lifts C (S1/C1 -> T2).
    cases.append({
        "cwd": str(make_fixture(parent, "agents-md-crit", {
            "AGENTS.md": "# project\nGDPR applies to this service.\n",
        })),
        "expected_tier": "T2",
        "expected_scope": "S1",
        "expected_crit": "C1",
        "note": "AGENTS.md regulatory keyword -> C1 (S1/C1 -> T2)",
    })
    # AGENTS.md sentinel: tier: T3 line in AGENTS.md overrides (T3).
    cases.append({
        "cwd": str(make_fixture(parent, "agents-md-sentinel", {
            "AGENTS.md": "---\ntier: T3\n---\n# project\n",
        })),
        "expected_tier": "T3",
        "expected_scope": "n/a",
        "expected_crit": "n/a",
        "note": "AGENTS.md tier sentinel -> T3 override",
    })

    return cases


def run_hook(cwd: str, home: Path, hook_tmp: Path) -> dict[str, Any]:
    """Invoke the hook with a synthetic SessionStart event for the given cwd.

    home and hook_tmp are private throwaway directories, so the hook writes
    its state where this suite can delete it. Without them the runner is not
    read-only: measured 2026-09-04, one run deposits 22 files and 9
    permanently uncleaned directories under the caller's real
    ~/.claude/projects (9 chat-claim.json, 6 methodology-tier-floor, 6
    methodology-tier-floor.previous), appends to ~/.claude/methodology-hook.log,
    and leaves claude-methodology-anchor-test-validation in the shared system
    temp. The hook resolves all of those from Path.home() and
    tempfile.gettempdir(), both of which read the environment, so redirecting
    them here is the whole fix and the existing shutil.rmtree(parent) in
    main() is the whole teardown.

    Isolating also makes the parse hermetic: a real home carries
    ~/.claude/vc-roe-addons, whose injected block would otherwise sit in the
    additionalContext that parse_additional searches.

    Same shape as test-chat-claim.py:199-208, whose comment records why both
    HOME and USERPROFILE are needed: Path.home() consults HOME on POSIX and
    USERPROFILE on Windows, where HOME is ignored outright.
    """
    event = {
        "hook_event_name": "SessionStart",
        "session_id": "test-validation",
        "transcript_path": "/dev/null",
        "cwd": cwd,
        "permission_mode": "default",
        "source": "startup",
        "model": "claude-opus-4-7",
    }
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    # ntpath.expanduser prefers USERPROFILE, then HOMEDRIVE + HOMEPATH; a
    # HOMEPATH left in the inherited environment would win over the HOME above
    # on any machine where USERPROFILE is absent, so clear the pair.
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    # tempfile.gettempdir() reads these three in order, and the hook writes its
    # heartbeat anchor there (hooks/session-start.py ANCHOR_DIR).
    env["TMPDIR"] = str(hook_tmp)
    env["TEMP"] = str(hook_tmp)
    env["TMP"] = str(hook_tmp)
    env.pop("CLAUDE_TIER", None)

    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return {"error": f"hook exit={proc.returncode}", "stderr": stderr, "stdout": stdout}
    if not stdout.strip():
        return {"error": "empty stdout", "stderr": stderr}

    try:
        parsed = json.loads(stdout.strip().split("\n")[-1])
    except Exception as e:
        return {"error": f"parse failed: {e}", "stdout": stdout, "stderr": stderr}

    additional = parsed.get("hookSpecificOutput", {}).get("additionalContext", "")
    return parse_additional(additional)


def parse_additional(additional: str) -> dict[str, Any]:
    out: dict[str, Any] = {"raw_size": len(additional)}

    m = re.search(r"effective_tier:\s*(T[0-4])", additional)
    if m:
        out["tier"] = m.group(1)
    m = re.search(r"scope:\s*(S[0-3]|n/a)", additional)
    if m:
        out["scope"] = m.group(1)
    m = re.search(r"criticality:\s*(C[0-2]|n/a)", additional)
    if m:
        out["crit"] = m.group(1)
    m = re.search(r"label:\s*(.+)", additional)
    if m:
        out["label"] = m.group(1).strip()
    m = re.search(r"source:\s*(\w[\w.-]+)", additional)
    if m:
        out["source"] = m.group(1)
    m = re.search(r"signals:\s*(\[.*?\])", additional)
    if m:
        out["signals"] = m.group(1)
    m = re.search(r"project_root:\s*(\S.*)", additional)
    if m:
        out["project_root"] = m.group(1).strip()
    m = re.search(r"git_root_found:\s*(True|False)", additional)
    if m:
        out["git_root_found"] = m.group(1) == "True"
    return out


def main() -> int:
    # v1.13.0 cross-platform fix: Windows console defaults to cp1252 which
    # cannot encode unicode glyphs in fixture notes (e.g. `>=`). Reconfigure
    # stdout to UTF-8 with replace-on-error so the suite runs on both
    # Linux and Windows without spurious UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parent = Path(tempfile.mkdtemp(prefix="vc-roe-fixtures-"))
    try:
        # Private HOME and temp for the hook subprocesses. Dotted names so they
        # cannot be mistaken for fixtures; both are inside parent, so the
        # finally: shutil.rmtree(parent) below is the only teardown needed.
        hook_home = parent / ".home"
        hook_home.mkdir()
        hook_tmp = parent / ".tmp"
        hook_tmp.mkdir()
        cases = build_fixtures(parent)
        print(f"== Detection validation (fixtures: {parent}) ==")
        print(f"   hook HOME: {hook_home}")
        print(f"   hook TMP : {hook_tmp}\n")
        rows = []
        for case in cases:
            cwd = case["cwd"]
            expected = case["expected_tier"]
            result = run_hook(cwd, hook_home, hook_tmp)
            rows.append({"cwd": cwd, "expected": expected,
                         "expected_scope": case["expected_scope"],
                         "expected_crit": case["expected_crit"],
                         "result": result, "note": case["note"]})

        width = 60
        print(f"{'cwd':<{width}} | {'exp':<3} | {'got':<3} | {'(S/C)':<8} | {'src':<14} | match")
        print("-" * (width + 60))
        misses = 0
        for row in rows:
            cwd_display = row["cwd"][-width:] if len(row["cwd"]) > width else row["cwd"]
            result = row["result"]
            if "error" in result:
                print(f"{cwd_display:<{width}} | {row['expected']:<3} | ERR | {result.get('error', '?')[:40]}")
                misses += 1
                continue
            got = result.get("tier", "?")
            s = result.get("scope", "?")
            c = result.get("crit", "?")
            src = result.get("source", "?")
            # Assert the triple, not the tier alone. The tier matrix is
            # many-to-one (12 scope x criticality pairs collapse onto 4 tiers,
            # T3 reachable from 6 of them), so a fixture can drift between
            # scopes with the tier unmoved and the runner none the wiser.
            expected_triple = (row["expected"], row["expected_scope"], row["expected_crit"])
            match = "OK" if (got, s, c) == expected_triple else "MISS"
            if match == "MISS":
                misses += 1
            sc = "ovrride" if (s == "n/a" and c == "n/a") else f"{s}/{c}"
            print(f"{cwd_display:<{width}} | {row['expected']:<3} | {got:<3} | {sc:<8} | {src:<14} | {match}")

        print()
        print(f"Misses: {misses} / {len(rows)}")

        print("\n== Detail ==")
        for row in rows:
            print(f"\n--- {row['cwd']}")
            print(f"    expected:    {row['expected']} "
                  f"({row['expected_scope']}/{row['expected_crit']})")
            print(f"    note:        {row.get('note', '')}")
            for k, v in row["result"].items():
                print(f"    {k:<15}: {v}")

        return 0 if misses == 0 else 1
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
