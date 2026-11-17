#!/usr/bin/env python3
"""The floor leaves nothing in the temp directory (VCR-I-49).

Runs every other floor suite (each `test-*.py` at the repository root, and
`codex/test_continuation.py`) as its own process with TMPDIR, TEMP and TMP set
to a fresh, empty directory, and fails when a suite leaves anything in it: a
`mkdtemp` folder it never removed, or a session anchor a hook wrote for a test
session id. Up to v1.35.6, five suites did (1, 16, 8, 1 and 2 entries).

A suite's exit code is read under the T2 exit-code contract (v1.36.1, VCR-I-52):
0 passed, 3 NOT-RUNNABLE (a case could not run on this platform), anything else
failed. A NOT-RUNNABLE suite is reported as such, never as a failure, and its
residue is still checked. Exit: 1 on any failure, else 3 if any suite was not
runnable, else 0; `--allow-skips` turns that 3 into 0.

Controls, run first:
  - a planted script that leaves one file is reported (the check can fail);
  - a planted script that leaves nothing is not (the check can pass);
  - planted scripts that exit 0, 3 and 1 read as passed, not runnable and
    failed, through the same function the suite loop uses;
  - a read-only file is removed by this guard's own cleanup (v1.36.1, VCR-I-53:
    on Windows git writes object files read-only, and
    `shutil.rmtree(ignore_errors=True)` silently leaves them).
At the end the guard checks that its own scratch root is gone.

What it cannot see, stated so nobody relies on it: a path a suite builds
without the temp-directory variables (a literal "/tmp/..."), and anything a
suite writes outside the temp directory (its own HOME handling is checked by
the suites themselves). Run on Linux and on Windows.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()

PASS = 0
FAIL = 0
FAILURES: list[str] = []
NOTRUN: list[str] = []


def check(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  [FAIL] {label}")


def suite_verdict(rc: int) -> str:
    """A suite's exit code under the T2 exit-code contract: 'pass', 'notrun' or 'fail'."""
    if rc == 0:
        return "pass"
    if rc == 3:
        return "notrun"
    return "fail"


def force_rmtree(path: Path) -> None:
    """Remove `path`, read-only files included. Clearing the read-only bit first
    works on every Python version, unlike rmtree's onerror/onexc split."""
    if not path.exists():
        return
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if name in dirnames else 0))
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def residue(cmd: list[str], scratch: Path) -> tuple[int, list[str]]:
    """Run `cmd` with an empty temp directory; return its exit code and what it left."""
    tmp = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)})
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    left = sorted(p.name for p in tmp.iterdir())
    return r.returncode, left


def main(argv: list[str]) -> int:
    allow_skips = "--allow-skips" in argv
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
        for code, want in ((0, "pass"), (3, "notrun"), (1, "fail")):
            planted = scratch / f"exit{code}.py"
            planted.write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
            rc, _left = residue([sys.executable, str(planted)], scratch)
            check(suite_verdict(rc) == want, f"a planted script exiting {code} reads as {want}: "
                                             f"rc={rc} verdict={suite_verdict(rc)}")
        ro = scratch / "readonly-tree"
        (ro / "objects").mkdir(parents=True)
        ro_file = ro / "objects" / "blob"
        ro_file.write_text("x", encoding="utf-8")
        os.chmod(ro_file, stat.S_IREAD)
        force_rmtree(ro)
        check(not ro.exists(), "this guard's cleanup removes a tree holding a read-only file")

        suites = sorted(p for p in ROOT.glob("test-*.py") if p.resolve() != SELF)
        suites.append(ROOT / "codex" / "test_continuation.py")
        print(f"Suites: {len(suites)}")
        check(len(suites) >= 17, f"{len(suites)} suites found (at least 17 expected)")
        for suite in suites:
            name = suite.relative_to(ROOT)
            rc, left = residue([sys.executable, str(suite)], scratch)
            shown = left[:5] + ([f"... {len(left) - 5} more"] if len(left) > 5 else [])
            verdict = suite_verdict(rc)
            if verdict == "notrun":
                NOTRUN.append(f"{name}: exit 3")
                print(f"  [NRUN] {name}: exit 3 (NOT-RUNNABLE on this platform; not a failure)")
            else:
                check(verdict == "pass", f"{name}: exit {rc}")
            check(left == [], f"{name}: {len(left)} entr{'y' if len(left) == 1 else 'ies'} "
                              f"left in the temp directory {shown}")
    finally:
        force_rmtree(scratch)
    check(not scratch.exists(), "this guard's own scratch root is removed")

    print(f"\n{PASS} pass / {FAIL} fail / {len(NOTRUN)} not runnable")
    for f in FAILURES:
        print(f"  - {f}")
    for n in NOTRUN:
        print(f"  - NOT-RUNNABLE {n}")
    if FAIL:
        return 1
    if NOTRUN and not allow_skips:
        print("VERDICT: NOT-RUNNABLE (a suite could not run every case here; pass --allow-skips to accept)")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
