#!/usr/bin/env python3
"""The floor leaves nothing in the temp directory (VCR-I-49).

Runs every other floor suite (each `test-*.py` at the repository root, and
`codex/test_continuation.py`) as its own process with TMPDIR, TEMP and TMP set
to a fresh, empty directory, and fails when a suite leaves anything in it: a
`mkdtemp` folder it never removed, or a session anchor a hook wrote for a test
session id. Up to v1.35.6, five suites did (1, 16, 8, 1 and 2 entries).

Controls, run first:
  - a planted script that leaves one file is reported (the check can fail);
  - a planted script that leaves nothing is not (the check can pass).

What it cannot see, stated so nobody relies on it: a path a suite builds
without the temp-directory variables (a literal "/tmp/..."), and anything a
suite writes outside the temp directory (its own HOME handling is checked by
the suites themselves). Proven on Linux; on Windows the variables are the same
three, but that has not been run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  [FAIL] {label}")


def residue(cmd: list[str], scratch: Path) -> tuple[int, list[str]]:
    """Run `cmd` with an empty temp directory; return its exit code and what it left."""
    tmp = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)})
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    left = sorted(p.name for p in tmp.iterdir())
    return r.returncode, left


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="vcroe-floor-residue-"))
    try:
        print("Controls")
        leak = scratch / "leak.py"
        leak.write_text("import tempfile\ntempfile.mkdtemp(prefix='planted-')\n", encoding="utf-8")
        clean = scratch / "clean.py"
        clean.write_text("import tempfile\nwith tempfile.TemporaryDirectory():\n    pass\n", encoding="utf-8")
        rc, left = residue([sys.executable, str(leak)], scratch)
        check(rc == 0 and len(left) == 1 and left[0].startswith("planted-"),
              f"a planted leftover is reported: {left}")
        rc, left = residue([sys.executable, str(clean)], scratch)
        check(rc == 0 and left == [], f"a script that cleans up is not reported: {left}")

        suites = sorted(p for p in ROOT.glob("test-*.py") if p.resolve() != SELF)
        suites.append(ROOT / "codex" / "test_continuation.py")
        print(f"Suites: {len(suites)}")
        check(len(suites) >= 17, f"{len(suites)} suites found (at least 17 expected)")
        for suite in suites:
            rc, left = residue([sys.executable, str(suite)], scratch)
            shown = left[:5] + ([f"... {len(left) - 5} more"] if len(left) > 5 else [])
            check(rc == 0, f"{suite.relative_to(ROOT)}: exit {rc}")
            check(left == [], f"{suite.relative_to(ROOT)}: {len(left)} entr{'y' if len(left) == 1 else 'ies'} "
                              f"left in the temp directory {shown}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{PASS} pass / {FAIL} fail")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
